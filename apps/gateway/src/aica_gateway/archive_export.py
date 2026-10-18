import copy
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from aica_mcp.transfer import build_bundle, build_json, verify_bundle
from aica_schema.protocol import EMBEDDED_EXTENSION, dumps, empty_archive, require, require_archive
from fastapi.responses import FileResponse
from preview import ArtifactStore
from starlette.background import BackgroundTask

from .agent_handoff import attach_handoff


def prepare_export(service, repo_id, commit):
    from .repo_media import generated_artifact, media_path, source_snapshot

    workspace = json.loads(commit['document'])
    objects = {}
    if commit['project_id']:
        snapshot = source_snapshot(service, commit)
        with service.store.connection() as db:
            owner = db.execute('SELECT subject FROM projects WHERE id=?', (commit['project_id'],)).fetchone()['subject']
        original = json.loads(service.store.archive_bytes(owner, commit['project_id'], commit['import_id']))
        for artifact in original['artifacts']:
            record, path = service.store.artifact_in_snapshot(snapshot, artifact['id'])
            objects[record['sha256']] = path
    else:
        original = empty_archive(workspace.get('title', 'Platform workflow'), project_id='platform-' + repo_id, stream_id='platform-' + repo_id)
        original['metadata'].update(created_at=commit['created_at'], updated_at=commit['created_at'])
        original['graph']['revision_id'] = 'platform-source-' + repo_id
        original['snapshot']['snapshot_id'] = 'platform-source-' + repo_id
    archive = copy.deepcopy(original)
    archive.get('extensions', {}).pop(EMBEDDED_EXTENSION, None)
    generations, cached = [], {}
    with service.store.connection() as db:
        lineage = {item['id'] for item in db.execute(
            'WITH RECURSIVE parents(id,parent_repo) AS (SELECT id,parent_repo FROM repositories WHERE id=? '
            'UNION SELECT r.id,r.parent_repo FROM repositories r JOIN parents p ON r.id=p.parent_repo) SELECT id FROM parents', (repo_id,))}

    def attach_record(record, path, artifact_id, producer=None):
        existing = next((item for item in archive['artifacts'] if item['id'] == artifact_id), None)
        require(not existing or existing['sha256'] == record['sha256'], 'Saved artifact ID conflicts with source', 'invalid_contract')
        if not existing:
            archive['artifacts'].append({**record, 'id': artifact_id, 'producer_node_id': producer, 'producer_run_id': None})
        objects[record['sha256']] = path

    def collect_preview(uri, expected_id, node_id, state):
        match = re.fullmatch(r'/api/creation-parser/previews/([a-f0-9]{32})/artifact', urlsplit(uri).path)
        require(match is not None and match[1] == expected_id, 'Confirmed preview identity differs', 'invalid_contract')
        identifier = match[1]
        if identifier not in cached:
            with service.store.connection() as db:
                row = db.execute('SELECT * FROM frontend_jobs WHERE id=? AND state=? AND kind=?', (identifier, 'ready', 'preview')).fetchone()
            require(row is not None and row['repo_id'] in lineage, 'Preview does not belong to this repository', 'not_found')
            result = json.loads(row['result'])
            store = ArtifactStore(service.store.directory / 'frontend-assets' / hashlib.sha256(row['subject'].encode()).hexdigest())
            record = result['stored_artifact']
            store.read(record)
            artifact_id = 'platform-preview-' + identifier
            attach_record(record, store.local_path(record['uri']), artifact_id, node_id if state == 'confirmed' else None)
            source_id = None
            if result.get('stored_source'):
                reference = result['stored_source']
                store.read(reference)
                source_id = 'platform-reference-' + identifier
                attach_record(reference, store.local_path(reference['uri']), source_id)
            cached[identifier] = {'preview_id': identifier, 'artifact_id': artifact_id, 'source_artifact_id': source_id,
                                  'actual_prompt': (result.get('generation_request') or {}).get('prompt', result.get('prompt')),
                                  'user_instruction': result.get('prompt'), 'actual_model': result.get('model'),
                                  'prompt_basis': 'recorded_generation_request' if result.get('generation_request') else 'user_request_effective_prompt_not_recorded',
                                  'generation_request': result.get('generation_request'),
                                  'source_node_id': result.get('node_id'), 'requested_at_epoch': row['created_at'],
                                  'edit_request': result.get('edit_request'), 'local_edit': result.get('local_edit')}
        generations.append({**cached[identifier], 'node_id': node_id, 'state': state})

    steps = workspace.get('steps', workspace.get('modules', []))
    confirmed = set()
    for step in steps:
        identifier = step.get('generatedPreviewId')
        if not identifier:
            continue
        references = [item for item in step.get('artifacts', []) if '/api/creation-parser/previews/' in item.get('uri', '')]
        require(references, 'Confirmed image is missing its saved reference', 'invalid_contract')
        for reference in references:
            collect_preview(reference['uri'], identifier, step['id'], 'confirmed')
        confirmed.add(identifier)
    ui = workspace.get('ui') or {}
    previews = list(ui.get('previewHistory') or workspace.get('preview_history') or [])
    if ui.get('preview'):
        previews.append(ui['preview'])
    for preview in previews:
        identifier = preview.get('id')
        if preview.get('simulated') or not identifier or identifier in confirmed or identifier in cached:
            continue
        reference = preview.get('artifact') or {}
        if reference.get('uri', '').startswith('/api/creation-parser/previews/'):
            collect_preview(reference['uri'], identifier, preview['nodeId'], 'unconfirmed')
    semantic = workspace.get('source_semantic_graph') or workspace
    for node in semantic.get('nodes', []):
        for edit in node.get('image_edits', []):
            record = generated_artifact(service, repo_id, workspace, edit['result_artifact_id'])
            _, path = media_path(service, repo_id, commit, edit['result_artifact_id'])
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'],
                    'Saved image is unavailable', 'storage_corrupt')
            attach_record(record, path, record['id'], node['id'])
            generations.append({'node_id': node['id'], 'state': 'confirmed', 'artifact_id': record['id'],
                                'source_artifact_id': edit['original_artifact_id'], 'actual_prompt': edit['instruction'],
                                'actual_model': edit.get('model'), 'edit_request': edit})
    for step in steps:
        for reference in step.get('artifacts', []):
            match = re.fullmatch(r'/api/3d/jobs/([a-f0-9]{32})/model', urlsplit(reference.get('uri', '')).path)
            if not match:
                continue
            with service.store.connection() as db:
                row = db.execute('SELECT * FROM rodin_jobs WHERE id=? AND state=?',
                                 (match[1], 'succeeded')).fetchone()
            require(row is not None and row['repo_id'] in lineage,
                    '3D model does not belong to this repository', 'not_found')
            metadata = json.loads(row['artifact'])
            path = service.store.directory / 'rodin-models' / (row['id'] + '.glb')
            require(path.is_file() and path.stat().st_size == metadata['size']
                    and hashlib.sha256(path.read_bytes()).hexdigest() == metadata['sha256'],
                    '3D model unavailable', 'storage_corrupt')
            record = {'uri': 'objects/' + metadata['sha256'], 'mime_type': 'model/gltf-binary',
                      'sha256': metadata['sha256'], 'size': metadata['size'], 'availability': 'available'}
            attach_record(record, path, reference['id'], step['id'])
            recipe = json.loads(row['recipe'])
            generations.append({'node_id': step['id'], 'state': 'confirmed', 'artifact_id': reference['id'],
                                'actual_prompt': recipe.get('prompt', ''),
                                'actual_model': step.get('model'), 'generation_request': recipe})
    archive = attach_handoff(service, repo_id, commit, archive, original, workspace, objects, generations)
    require_archive(archive)
    return archive, objects


def export_bundle(service, repo_id, commit, portable=False):
    archive, objects = prepare_export(service, repo_id, commit)
    directory = Path(tempfile.mkdtemp(prefix='aica-platform-export-'))
    try:
        source = directory / 'source'
        source.mkdir()
        for artifact in archive['artifacts']:
            sha = artifact['sha256']
            artifact['uri'] = 'objects/' + sha
            artifact.pop('locations', None)
            target = source / artifact['uri']
            target.parent.mkdir(exist_ok=True)
            value = objects[sha]
            if isinstance(value, bytes):
                target.write_bytes(value)
            elif not target.exists():
                shutil.copyfile(value, target)
        (source / 'aica.json').write_text(dumps(archive), encoding='utf-8')
        if portable:
            output = directory / 'workflow.aica'
            report = build_json(source, output)
            return FileResponse(output, media_type='application/json', filename='workflow.aica',
                                headers={'X-AiCA-SHA256': report['sha256'], 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'},
                                background=BackgroundTask(shutil.rmtree, directory))
        output = directory / 'archive.zip'
        build_bundle(source, output)
        stable = directory / 'stable.zip'
        with zipfile.ZipFile(output) as incoming, zipfile.ZipFile(stable, 'w') as outgoing:
            for entry in incoming.infolist():
                with incoming.open(entry) as reader, outgoing.open(zipfile.ZipInfo(entry.filename), 'w') as writer:
                    shutil.copyfileobj(reader, writer)
        report = verify_bundle(stable)
        return FileResponse(stable, media_type='application/vnd.aica.bundle+zip', filename='agent-workflow.aica.zip',
                            headers={'X-AiCA-SHA256': report['bundle_sha256'], 'X-Content-Type-Options': 'nosniff',
                                     'Cache-Control': 'private, no-store', 'Vary': 'Accept'},
                            background=BackgroundTask(shutil.rmtree, directory))
    except BaseException:
        shutil.rmtree(directory)
        raise
