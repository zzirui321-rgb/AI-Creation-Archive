import base64
import copy
import hashlib
import io
import zipfile
import zlib
from contextlib import AsyncExitStack

import httpx
import pytest
from aica_mcp.consent import control_request
from aica_mcp.files import read_artifact
from aica_mcp.hosts import confirm_turn_upload
from aica_mcp.transfer import read_portable_json
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, require_archive
from mcp import types
from test_platform import TOKEN, receipt
from test_turns import begin, hook, native_connection
from test_turns import running as running

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=')


def configure(coordinator, monkeypatch):
    monkeypatch.setenv('AICA_UPLOAD_TOKEN', TOKEN)
    result = coordinator.platform.bind('https://platform.invalid', new_id(), True)
    assert not result['auto_upload']


def observe_image(coordinator, bridge=None, turn='turn-1'):
    gate = coordinator.turns
    bridge, result = begin(gate, bridge=bridge, turn=turn)
    fields = {'tool_name': 'imagegen', 'tool_use_id': 'image-call', 'tool_input': {'prompt': 'fixture image'}}
    hook(gate, 'PreToolUse', turn=turn, **fields)
    hook(gate, 'PostToolUse', turn=turn, **fields, tool_response={'path': 'picture.png'})
    (gate.recorder.project / 'picture.png').write_bytes(PNG)
    return bridge


async def test_stdio_png_upload_then_pull_platform_added_image(running, monkeypatch):
    configure(running, monkeypatch)
    bodies = []

    def upload(request):
        bodies.append(request.read())
        return httpx.Response(201, json=receipt(request))

    running.platform.transport = httpx.MockTransport(upload)
    choices = iter(['Yes', 'No', 'Yes', 'Yes'])
    questions = []

    async def confirm(context, params):
        questions.append(params.message)
        return types.ElicitResult(action='accept', content={'choice': next(choices)})

    root = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, root, confirm)
        bridge = next(iter(running.turns.bridges))
        observe_image(running, bridge)
        selected = await session.call_tool('aica_select_turn_artifacts', {'paths': ['picture.png'], 'tool_use_id': 'image-call'})
        assert not selected.is_error, selected
        local_image = selected.structured_content['artifacts'][0]
        run = running.recorder.document['runs'][0]
        assert local_image['producer_run_id'] == run['id']
        assert local_image['producer_node_id'] == run['node_id'] and local_image['id'] in run['artifact_ids']
        stopped = control_request(root, 'hook', {'event': {'cwd': str(root), 'session_id': 'host-1', 'turn_id': 'turn-1',
                                  'hook_event_name': 'Stop', 'last_assistant_message': 'Visible fixture answer'}})
        assert stopped['export']['status'] == 'success' and stopped['upload_bridge_id'] == bridge
        exported, objects, raw = read_portable_json(root / 'exports/aica.json')
        assert objects[local_image['sha256']] == PNG
        assert b'Visible fixture answer' in b''.join(objects.values())
        assert exported['harnesses'] and exported['agents'][0]['model']['name'] == 'observed-agent-model'
        declined = await session.call_tool('aica_sync_platform', {})
        assert not declined.is_error and not declined.structured_content['uploaded'] and not bodies
        accepted = await session.call_tool('aica_sync_platform', {})
        assert not accepted.is_error, accepted
        assert accepted.structured_content['uploaded'] and len(bodies) == 1
        assert running.platform.status()['state'] == 'synced'
        assert not running.platform.reviews
        with zipfile.ZipFile(io.BytesIO(bodies[0])) as bundle:
            document = loads(bundle.read('aica.json').decode())
            manifest = loads(bundle.read('manifest.json').decode())
            remote_objects = {item['sha256']: bundle.read(item['path']) for item in manifest['artifacts']}
        assert document['graph'] == exported['graph'] and remote_objects[local_image['sha256']] == PNG
        remote_png = PNG + b'platform-fixture-version'
        sha256 = hashlib.sha256(remote_png).hexdigest()
        remote_image = {**local_image, 'id': 'platform-image', 'sha256': sha256, 'size': len(remote_png),
                        'uri': 'https://storage.invalid/platform-image.png'}
        document['artifacts'].append(remote_image)
        document['runs'][0]['artifact_ids'].append(remote_image['id'])
        require_archive(document)
        remote_objects[sha256] = remote_png
        commit_id = new_id()
        repo_id = new_id()
        downloads = []

        def download(request):
            downloads.append(request)
            assert request.url.params['commit_id'] == commit_id
            assert request.url.host == 'platform.invalid'
            if request.url.path.endswith('/archive'):
                return httpx.Response(200, json=document)
            artifact_id = request.url.path.rsplit('/', 1)[-1]
            artifact = next(item for item in document['artifacts'] if item['id'] == artifact_id)
            return httpx.Response(200, content=remote_objects[artifact['sha256']])

        running.pulls.transport = httpx.MockTransport(download)
        monkeypatch.setenv('AICA_DOWNLOAD_TOKEN', 'D' * 48)
        result = await session.call_tool('aica_pull_platform', {'server_url': 'https://platform.invalid', 'repo_id': repo_id,
                     'commit_id': commit_id, 'request_id': new_id(),
                     'expected_snapshot_id': running.recorder.document['snapshot']['snapshot_id']})
        assert not result.is_error, result
        assert result.structured_content['pulled'] and (root / result.structured_content['backup_path']).is_dir()
        assert running.recorder.document['graph'] == document['graph']
        for artifact in running.recorder.document['artifacts']:
            assert read_artifact(root, artifact) == remote_objects[artifact['sha256']]
        assert len(downloads) > 1 and len(questions) == 4
        assert not running.platform.status()['auto_upload']


@pytest.mark.parametrize('boundary', ['changed', 'destination', 'expired', 'wrong_bridge', 'busy'])
def test_upload_review_rejects_changed_or_unapproved_state(running, monkeypatch, boundary):
    configure(running, monkeypatch)
    bridge = running.turns.attach()['bridge_id']
    prepared = running.platform.prepare_upload(bridge)
    item = running.platform.reviews[prepared['prepared_id']]
    bodies = []
    running.platform.transport = httpx.MockTransport(lambda request: bodies.append(request) or httpx.Response(500))
    if boundary == 'changed':
        begin(running.turns, bridge=bridge)
    elif boundary == 'destination':
        config = running.platform.read_config()
        config['project_id'] = new_id()
        running.platform.config_path.write_text(dumps(config))
    elif boundary == 'expired':
        item['created'] -= 301
    elif boundary == 'wrong_bridge':
        bridge = running.turns.attach()['bridge_id']
    else:
        running.platform.upload_lock.acquire()
    try:
        with pytest.raises(ProtocolError):
            running.platform.apply_upload(bridge, prepared['prepared_id'])
        assert not bodies
    finally:
        if boundary == 'busy':
            running.platform.upload_lock.release()
        running.platform.cancel_upload(item['bridge_id'], prepared['prepared_id'])


def test_next_turn_and_old_background_grant_never_upload_automatically(running, monkeypatch):
    configure(running, monkeypatch)
    bridge = observe_image(running)
    running.turns.capture(bridge, 'host-1', 'turn-1', ['picture.png'], 'image-call')
    hook(running.turns, 'Stop')
    bodies = []
    running.platform.transport = httpx.MockTransport(lambda request: bodies.append(request.read()) or httpx.Response(201, json=receipt(request)))
    prepared = running.platform.prepare_upload(bridge)
    running.platform.apply_upload(bridge, prepared['prepared_id'])
    begin(running.turns, bridge=bridge, turn='next', prompt='NEW_LOCAL_ONLY')
    config = running.platform.read_config()
    config['auto_upload'] = True
    running.platform.config_path.write_text(dumps(config))
    grant = running.platform.directory(config) / 'authorization.json'
    grant.write_text(dumps({'binding_id': running.platform.binding_id(config)}))
    running.platform.run_once()
    assert len(bodies) == 1 and b'NEW_LOCAL_ONLY' not in bodies[0]
    assert running.platform.status()['state'] == 'confirmation_required'
    with pytest.raises(ProtocolError, match='consent bridge'):
        running.platform.sync_now()


@pytest.mark.parametrize('choice,state', [(True, 'accepted'), (False, 'declined'), (False, 'expired'), (False, 'browser_unavailable')])
def test_stop_upload_prompt_requires_explicit_yes(running, monkeypatch, choice, state):
    configure(running, monkeypatch)
    bridge = observe_image(running)
    bodies = []
    running.platform.transport = httpx.MockTransport(lambda request: bodies.append(request.read()) or httpx.Response(201, json=receipt(request)))
    monkeypatch.setattr('aica_mcp.hosts.browser_question', lambda *args: {'choice': choice, 'state': state})
    message = confirm_turn_upload(running.recorder.project, bridge)
    assert len(bodies) == int(choice)
    assert ('已上传' in message) is choice
    assert not running.platform.reviews


def test_missing_png_prevents_upload_review(running, monkeypatch):
    configure(running, monkeypatch)
    bridge = observe_image(running)
    selected = running.turns.capture(bridge, 'host-1', 'turn-1', ['picture.png'], 'image-call')['artifacts'][0]
    (running.recorder.project / selected['uri']).unlink()
    with pytest.raises(ProtocolError):
        running.platform.prepare_upload(bridge)
    assert not running.platform.reviews


def test_unobserved_producer_is_not_invented(running):
    bridge = observe_image(running)
    before = copy.deepcopy(running.recorder.document)
    with pytest.raises(ProtocolError, match='not observed'):
        running.turns.capture(bridge, 'host-1', 'turn-1', ['picture.png'], 'unknown-call')
    assert running.recorder.document == before


def test_failed_upload_retains_identical_bytes_until_confirmed_retry(running, monkeypatch):
    configure(running, monkeypatch)
    bridge = observe_image(running)
    hook(running.turns, 'Stop')
    bodies = []

    def transport(request):
        bodies.append(request.read())
        if len(bodies) == 1:
            raise httpx.ReadError('Fixture response lost after receiving bytes')
        return httpx.Response(201, json=receipt(request))

    running.platform.transport = httpx.MockTransport(transport)
    prepared = running.platform.prepare_upload(bridge)
    with pytest.raises(ProtocolError, match="remote outcome may be unknown"):
        running.platform.apply_upload(bridge, prepared['prepared_id'])
    running.platform.cancel_upload(bridge, prepared['prepared_id'])
    begin(running.turns, bridge=bridge, turn='next', prompt='NOT_IN_PRIOR_SNAPSHOT')
    running.platform.run_once()
    assert len(bodies) == 1
    retry = running.platform.prepare_upload(bridge)
    assert '上次上传' in retry['message']
    running.platform.apply_upload(bridge, retry['prepared_id'])
    assert bodies[0] == bodies[1]
    assert running.platform.status()['state'] == 'confirmation_required'
    assert not running.platform.read_state(running.platform.read_config())['pending']


def test_pause_revokes_prepared_upload(running, monkeypatch):
    configure(running, monkeypatch)
    bridge = running.turns.attach()['bridge_id']
    prepared = running.platform.prepare_upload(bridge)
    running.platform.set_enabled(False)
    with pytest.raises(ProtocolError, match='expired'):
        running.platform.apply_upload(bridge, prepared['prepared_id'])


def test_png_fixture_has_valid_checksums_and_pixel_data():
    assert PNG[:8] == b'\x89PNG\r\n\x1a\n'
    offset = 8
    compressed = bytearray()
    kinds = []
    while offset < len(PNG):
        size = int.from_bytes(PNG[offset:offset + 4], 'big')
        chunk = PNG[offset + 4:offset + 8 + size]
        checksum = int.from_bytes(PNG[offset + 8 + size:offset + 12 + size], 'big')
        assert zlib.crc32(chunk) & 0xffffffff == checksum
        kinds.append(chunk[:4])
        if chunk[:4] == b'IDAT':
            compressed.extend(chunk[4:])
        offset += size + 12
    assert offset == len(PNG) and kinds == [b'IHDR', b'IDAT', b'IEND']
    assert zlib.decompress(compressed) == b'\x01\xff\xff'
