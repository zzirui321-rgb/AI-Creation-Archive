import io
import json
import zipfile

from aica_mcp.importing import import_archive
from aica_mcp.transfer import build_json, read_portable_json
from aica_schema.protocol import dumps, new_id
from test_platform_api import login
from test_platform_api import platform_client as platform_client


def exported_content(client, repo_id, commit_id, tmp_path):
    response = client.get(f'/api/repos/{repo_id}/handoff', params={'commit_id': commit_id})
    assert response.status_code == 200, response.text
    target = tmp_path / 'handoff.aica'
    target.write_bytes(response.content)
    archive, objects, _ = read_portable_json(target)
    extension = archive['extensions']['aica.agent-handoff']
    by_id = {item['id']: item for item in archive['artifacts']}
    workflow = json.loads(objects[by_id[extension['workflow_artifact_id']]['sha256']])
    operations = json.loads(objects[by_id[extension['operations_artifact_id']]['sha256']])
    return archive, objects, workflow, operations, response


def test_modified_graph_exports_current_definition_audit_and_importable_images(platform_client, bundle_factory, tmp_path):
    client = platform_client
    client.headers.update(login(client, 'handoff-owner'))
    source = bundle_factory()
    archive = source['archive']
    base = archive['graph']['nodes'][0]
    archive['graph']['nodes'] = [{**base, 'id': identifier, 'label': identifier,
        'execution': {'kind': 'model', 'tool': 'recorded-tool', 'version': None, 'model': 'original-model',
                      'inputs': {'prompt': {'kind': 'literal', 'value': 'blue ' + identifier}}, 'params': {}, 'outputs': ['image']}}
        for identifier in ('a', 'b', 'c', 'd')]
    archive['graph']['edges'] = [{'id': 'ab', 'source': 'a', 'target': 'b', 'kind': 'control'},
                                {'id': 'bc', 'source': 'b', 'target': 'c', 'kind': 'control'}]
    archive['artifacts'][0]['producer_node_id'] = 'a'
    (source['root'] / 'aica.json').write_text(dumps(archive), encoding='utf-8')
    portable = source['root'] / 'portable.json'
    build_json(source['root'], portable)
    imported = client.post('/api/creation-parser/imports', content=portable.read_bytes()).json()
    original = client.get(f"/api/creation-parser/jobs/{imported['id']}/result").json()['repository']
    repo, old_commit = original['id'], original['commit_id']
    document = {'format': 'aica-ui-module-record', 'title': 'Modified workflow', 'steps': [
        {'id': identifier, 'title': identifier, 'prompt': 'red a' if identifier == 'a' else 'blue ' + identifier, 'model': 'original-model'}
        for identifier in ('a', 'b', 'c', 'd')], 'edges': archive['graph']['edges']}
    saved = client.post(f'/api/repos/{repo}/commits', json={'expected_head': old_commit, 'message': 'Change girl hair', 'document': document})
    assert saved.status_code == 201, saved.text
    commit = saved.json()['commit']['id']
    exported, objects, workflow, operations, response = exported_content(client, repo, commit, tmp_path)
    nodes = {node['id']: node for node in exported['graph']['nodes']}
    assert nodes['a']['execution']['inputs']['prompt']['value'] == 'red a'
    assert nodes['d']['execution']['tool'] == 'recorded-tool'
    assert {node['id']: node['plan'] for node in workflow['steps']} == {'a': 'recompute', 'b': 'rebuild', 'c': 'rebuild', 'd': 'reuse'}
    assert objects[archive['artifacts'][0]['sha256']] == source['content']
    change = next(item for item in operations['commits'][-1]['changes'] if item.get('field') == 'prompt')
    assert (change['node_id'], change['before'], change['after']) == ('a', 'blue a', 'red a')
    assert operations['commits'][-1]['actor'].startswith('user:')
    assert operations['commits'][-1]['saved_at'] == saved.json()['commit']['created_at']
    assert client.get(f'/api/repos/{repo}/handoff', params={'commit_id': commit}).content == response.content
    assert client.get(f'/api/repos/{repo}/archive', params={'commit_id': old_commit}).content == portable.read_bytes()
    agent = tmp_path / 'agent'
    agent.mkdir()
    (agent / 'workflow.aica').write_bytes(response.content)
    restored = import_archive(agent, 'workflow.aica', 'continued', new_id(), True)
    assert restored['executed'] is restored['recording_started_by_import'] is False
    local = json.loads((agent / restored['archive_path']).read_text(encoding='utf-8'))
    handoff = local['extensions']['aica.agent-handoff']
    by_id = {item['id']: item for item in local['artifacts']}
    instructions = agent / 'continued' / '.aica' / 'artifacts' / by_id[handoff['instructions_artifact_id']]['sha256']
    assert 'recompute/rebuild' in instructions.read_text(encoding='utf-8')
    assert (agent / 'continued' / '.aica' / 'artifacts' / archive['artifacts'][0]['sha256']).read_bytes() == source['content']
    document['steps'][0]['prompt'] = ''
    document['steps'] = [step for step in document['steps'] if step['id'] != 'd'] + [{'id': 'new', 'prompt': 'new task'}]
    document['edges'] = [{'source': 'c', 'target': 'new', 'kind': 'semantic', 'state': 'candidate'}]
    next_commit = client.post(f'/api/repos/{repo}/commits', json={'expected_head': commit, 'message': 'Clear prompt and reorganize', 'document': document}).json()['commit']['id']
    updated, _, plan, audit, _ = exported_content(client, repo, next_commit, tmp_path)
    assert {node['id'] for node in updated['graph']['nodes']} == {'a', 'b', 'c', 'new'}
    assert updated['graph']['nodes'][0]['execution']['inputs']['prompt']['value'] == ''
    assert updated['graph']['edges'] == []
    assert plan['candidate_relations'][0]['target'] == 'new'
    assert any(item['operation'] == 'node_removed' and item['node_id'] == 'd' for item in audit['commits'][-1]['changes'])
    assert client.get(f'/api/repos/{repo}/handoff', params={'commit_id': commit}).content == response.content
    client.headers.update(login(client, 'handoff-other'))
    assert client.get(f'/api/repos/{repo}/handoff', params={'commit_id': commit}).status_code == 404


def test_ui_only_repository_and_branch_export_without_original_mcp_archive(platform_client, tmp_path):
    client = platform_client
    client.headers.update(login(client, 'handoff-ui'))
    repo = client.post('/api/repos', json={'name': 'ui-only', 'title': 'UI only'}).json()['repository']['id']
    document = {'format': 'aica-ui-module-record', 'title': 'No source required', 'steps': [{'id': 'one', 'prompt': 'draw a cat', 'model': 'chosen-model'},
        {'id': '辅助节点 2', 'prompt': 'compose'}], 'edges': [{'source': 'one', 'target': '辅助节点 2'}]}
    first = client.post(f'/api/repos/{repo}/commits', json={'expected_head': None, 'message': 'New workflow', 'document': document}).json()['commit']['id']
    client.post(f'/api/repos/{repo}/branches', json={'name': 'alternative', 'from_branch': 'main'})
    document['steps'][0]['prompt'] = 'draw a red cat'
    changed = client.post(f'/api/repos/{repo}/commits', json={'branch': 'alternative', 'expected_head': first, 'message': 'Alternative prompt', 'document': document}).json()['commit']['id']
    archive, _, workflow, _, _ = exported_content(client, repo, changed, tmp_path)
    assert archive['graph']['nodes'][0]['execution']['inputs']['prompt']['value'] == 'draw a red cat'
    assert archive['graph']['nodes'][1]['extensions']['aica.platform-node-id'] == '辅助节点 2'
    assert archive['graph']['edges'][0]['target'] == archive['graph']['nodes'][1]['id']
    assert workflow['steps'][0]['adapter_status'] == 'agent_configuration_required'
    assert client.get(f'/api/repos/{repo}/graph').json()['commit']['id'] == first
    bundle = client.get(f'/api/repos/{repo}/handoff', params={'commit_id': changed, 'format': 'bundle'})
    assert bundle.status_code == 200, bundle.text
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as data:
        assert json.loads(data.read('aica.json'))['extensions']['aica.agent-handoff']['commit_id'] == changed


def test_removed_data_dependency_is_marked_unresolved(platform_client, tmp_path):
    from aica_gateway.agent_handoff import normalize_graph
    from aica_schema.protocol import empty_archive, require_archive

    archive = empty_archive('dependency edit')
    nodes = [{'id': name, 'kind': 'tool', 'label': name, 'branch_id': 'main', 'provenance': {'mode': 'recorded', 'evidence': []},
              'execution': {'kind': 'manual', 'tool': 'recorded', 'version': None, 'inputs': {}, 'params': {}, 'outputs': ['text']}} for name in ('a', 'b')]
    nodes[1]['execution']['inputs']['source'] = {'kind': 'node_output', 'node_id': 'a', 'output': 'text'}
    archive['graph']['nodes'] = nodes
    archive['graph']['edges'] = [{'id': 'data', 'source': 'a', 'target': 'b', 'kind': 'data', 'source_output': 'text', 'target_input': 'source'}]
    import copy

    original = copy.deepcopy(archive)
    unresolved = normalize_graph(archive, original, {'steps': [{'id': 'a'}, {'id': 'b'}], 'edges': []}, {'id': 'saved'})
    assert unresolved[0]['node_id'] == 'b'
    assert 'source' not in archive['graph']['nodes'][1]['execution']['inputs']
    require_archive(archive)
