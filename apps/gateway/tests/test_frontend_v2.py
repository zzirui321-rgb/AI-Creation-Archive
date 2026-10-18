import io
import json
from time import monotonic, sleep

import httpx
from PIL import Image
from test_platform_api import login
from test_platform_api import platform_client as platform_client


def wait_preview(client, identifier):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        result = client.get('/api/creation-parser/previews/' + identifier).json()
        if result['status'] in {'ready', 'error', 'cancelled'}:
            return result
        sleep(.01)
    raise AssertionError('Preview did not finish')


def test_v2_import_and_local_edit_keep_platform_ownership_and_pixels(platform_client, bundle_factory, monkeypatch, tmp_path):
    client = platform_client
    client.headers.update(login(client, 'v2-owner'))
    blue, red = io.BytesIO(), io.BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(blue, format='PNG')
    Image.new('RGB', (64, 64), 'red').save(red, format='PNG')
    bundle = bundle_factory(content=blue.getvalue(), title='V2 real source')
    from aica_mcp.transfer import build_json

    exported = bundle['root'] / 'portable.json'
    build_json(bundle['root'], exported)
    imported = client.post('/api/creation-parser/imports', content=exported.read_bytes(), headers={'Content-Type': 'application/json'})
    assert imported.status_code == 201, imported.text
    identifier = imported.json()['id']
    assert client.post('/api/creation-parser/imports', content=exported.read_bytes()).json()['id'] == identifier
    analysis = client.get(f'/api/creation-parser/jobs/{identifier}/result').json()
    assert analysis['format'] == 'aica-graph-analysis/0.1'
    assert analysis['process_graph']['nodes'][0]['id'] == 'creation'
    assert analysis['artifacts'] == [{'id': 'output', 'mime': 'image/png', 'available': True}]
    assert len(client.get('/api/repos?scope=mine').json()['repositories']) == 1
    source_uri = f'/api/creation-parser/jobs/{identifier}/artifacts/output'
    assert client.get(source_uri).content == blue.getvalue()
    calls = []

    def provider(request):
        if request.method == 'GET':
            return httpx.Response(200, content=red.getvalue(), headers={'content-type': 'image/png'})
        calls.append(json.loads(request.content))
        assert request.headers['Authorization'] == 'Bearer synthetic-only-key'
        return httpx.Response(200, json={'output': {'choices': [{'message': {'content': [{'image': 'https://image.test/result.png'}]}}]}})

    client.app.state.image_edit_transport = httpx.MockTransport(provider)
    request = {'project_id': analysis['repository']['id'], 'node_id': 'creation', 'prompt': 'red hair', 'model': 'qwen-image-2.0',
               'request_id': 'a' * 32, 'provider': 'qwen', 'api_base': 'https://dashscope.aliyuncs.com/api/v1', 'api_key': 'synthetic-only-key',
               'local_edit': {'source_uri': source_uri, 'rect': {'x': .1, 'y': .1, 'width': .25, 'height': .25},
                              'feather_px': 0, 'points': [{'x': .1, 'y': .1}, {'x': .35, 'y': .1}, {'x': .1, 'y': .35}]}}
    started = client.post('/api/creation-parser/previews', json=request)
    assert started.status_code == 202, started.text
    preview = wait_preview(client, started.json()['id'])
    assert preview['status'] == 'ready', preview
    result = client.get(preview['artifact']['url'])
    with Image.open(io.BytesIO(result.content)) as image:
        assert image.getpixel((10, 10))[:3] == (255, 0, 0)
        assert image.getpixel((50, 50))[:3] == (0, 0, 255)
    with Image.open(io.BytesIO(client.get(preview['local_edit']['source_url']).content)) as source:
        assert source.convert('RGB').tobytes() == Image.open(io.BytesIO(blue.getvalue())).convert('RGB').tobytes()
    assert client.post('/api/creation-parser/previews', json=request).json()['id'] == started.json()['id']
    assert len(client.get('/api/creation-parser/previews', params={'project_id': analysis['repository']['id']}).json()['previews']) == 1
    assert len(calls) == 1
    repo_id = analysis['repository']['id']
    old_commit = analysis['repository']['commit_id']
    preview_id = started.json()['id']
    saved = client.post(f'/api/repos/{repo_id}/commits', json={
        'expected_head': old_commit, 'message': 'Confirm generated result',
        'document': {'format': 'aica-ui-module-record', 'title': 'Saved result', 'edges': [], 'steps': [
            {'id': 'generated-' + preview_id, 'title': 'Red hair', 'prompt': 'red hair', 'model': 'qwen-image-2.0',
             'generatedPreviewId': preview_id, 'artifacts': [{'id': preview_id, 'uri': preview['artifact']['url'], 'mime': 'image/png'}]}]}})
    assert saved.status_code == 201, saved.text
    new_commit = saved.json()['commit']['id']
    from aica_mcp.pulling import download_archive

    token = client.headers['Authorization'].split(' ', 1)[1]
    monkeypatch.setenv('AICA_DOWNLOAD_TOKEN', token)

    def gateway(request):
        response = client.get(str(request.url), headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

    pulled = download_archive('https://testserver', repo_id, new_commit, transport=httpx.MockTransport(gateway),
                              allow_loopback_http=False)
    result_node = next(node for node in pulled['document']['graph']['nodes'] if node['id'] == 'generated-' + preview_id)
    assert result_node['execution']['inputs']['prompt']['value'] == 'red hair'
    assert pulled['objects'][preview['artifact']['sha256']] == result.content
    assert len([item for item in pulled['document']['history'] if item['kind'] == 'revision']) == 1
    assert len([item for item in pulled['document']['history'] if item['kind'] == 'log']) == 1
    handoff = pulled['document']['extensions']['aica.agent-handoff']
    artifacts = {item['id']: item for item in pulled['document']['artifacts']}
    workflow = json.loads(pulled['objects'][artifacts[handoff['workflow_artifact_id']]['sha256']])
    record = workflow['steps'][0]['generation_records'][0]
    assert record['user_instruction'] == 'red hair'
    assert 'red hair' in record['actual_prompt'] and '根据原图' in record['actual_prompt']
    assert record['actual_model'] == 'qwen-image-2.0'
    assert record['edit_request']['rect']['width'] == .25
    assert pulled['objects'][artifacts[record['source_artifact_id']]['sha256']] == client.get(preview['local_edit']['source_url']).content
    assert 'synthetic-only-key' not in json.dumps(workflow)
    portable = client.get(f'/api/repos/{repo_id}/handoff', params={'commit_id': new_commit})
    assert portable.status_code == 200, portable.text
    from aica_mcp.importing import import_archive

    agent = tmp_path / 'agent-import'
    agent.mkdir()
    (agent / 'modified.aica').write_bytes(portable.content)
    restored = import_archive(agent, 'modified.aica', 'restored', 'synthetic-handoff-import', True)
    assert not restored['executed'] and not restored['recording_started_by_import']
    assert (agent / 'restored' / '.aica' / 'artifacts' / preview['artifact']['sha256']).read_bytes() == result.content
    assert download_archive('https://testserver', repo_id, new_commit, transport=httpx.MockTransport(gateway),
                            expected_sha256=pulled['download_sha256'])['download_sha256'] == pulled['download_sha256']
    fork = client.post(f'/api/repos/{repo_id}/forks', json={'name': 'confirmed-result-fork', 'title': 'Inherited result'}).json()['repository']
    fork_commit = client.get(f"/api/repos/{fork['id']}/graph").json()['commit']['id']
    fork_pull = download_archive('https://testserver', fork['id'], fork_commit, transport=httpx.MockTransport(gateway))
    assert fork_pull['objects'][preview['artifact']['sha256']] == result.content
    # Original archive JSON remains byte-identical for existing browser downloads.
    assert client.get(f'/api/repos/{repo_id}/archive', params={'commit_id': old_commit}).content == exported.read_bytes()
    old_export = client.get(f'/api/repos/{repo_id}/archive', params={'commit_id': old_commit},
                            headers={'Accept': 'application/vnd.aica.bundle+zip'})
    import zipfile

    with zipfile.ZipFile(io.BytesIO(old_export.content)) as old_zip:
        assert len(json.loads(old_zip.read('aica.json'))['graph']['nodes']) == 1
    edited = client.get(f'/api/repos/{repo_id}/graph').json()['document']
    edited['steps'][0]['prompt'] = 'green hair after generation'
    changed = client.post(f'/api/repos/{repo_id}/commits', json={'expected_head': new_commit, 'message': 'Change target after image', 'document': edited}).json()['commit']['id']
    target = client.get(f'/api/repos/{repo_id}/handoff', params={'commit_id': changed}).json()
    from aica_schema.protocol import embedded_objects

    objects = embedded_objects(target, complete=True)
    workflow_id = target['extensions']['aica.agent-handoff']['workflow_artifact_id']
    workflow_hash = next(item['sha256'] for item in target['artifacts'] if item['id'] == workflow_id)
    target_workflow = json.loads(objects[workflow_hash])
    assert target_workflow['steps'][0]['plan'] == 'recompute'
    assert target_workflow['steps'][0]['generation_records'][0]['user_instruction'] == 'red hair'
    unrelated = client.post('/api/repos', json={'name': 'unrelated-preview-owner', 'title': 'Unrelated'}).json()['repository']['id']
    forged = client.post(f'/api/repos/{unrelated}/commits', json={'expected_head': None, 'message': 'Foreign preview reference', 'document': edited}).json()['commit']['id']
    assert client.get(f'/api/repos/{unrelated}/handoff', params={'commit_id': forged}).status_code == 404
    with client.app.state.repository.connection() as db:
        assert 'synthetic-only-key' not in str([tuple(row) for row in db.execute('SELECT * FROM frontend_jobs')])
    client.headers.update(login(client, 'v2-other'))
    assert client.get(preview['artifact']['url']).status_code == 404
    assert client.get(source_uri).status_code == 404
    assert client.get(f'/api/creation-parser/jobs/{identifier}/result').status_code == 404
    assert client.get(f'/api/repos/{repo_id}/archive', params={'commit_id': new_commit},
                      headers={'Accept': 'application/vnd.aica.bundle+zip'}).status_code == 404


def test_v2_deterministic_message_import_never_invents_dependencies(platform_client):
    client = platform_client
    client.headers.update(login(client, 'v2-messages'))
    response = client.post('/api/creation-parser/imports', json={'title': 'Recorded messages', 'messages': [
        {'role': 'user', 'content': 'Draw a blue bird'}, {'role': 'assistant', 'content': 'Observed reply', 'model': 'recorded-model'}]})
    assert response.status_code == 201, response.text
    result = client.get('/api/creation-parser/jobs/' + response.json()['id'] + '/result').json()
    assert len(result['process_graph']['nodes']) == 2
    assert result['process_graph']['edges'] == []
    assert result['process_graph']['nodes'][1]['model'] == 'recorded-model'


def test_v2_video_job_persists_and_serves_ranges_without_exposing_key(platform_client):
    client = platform_client
    client.headers.update(login(client, 'v2-video'))
    imported = client.post('/api/creation-parser/imports', json={'modules': [{'id': 'video', 'prompt': 'bird'}]}).json()
    analysis = client.get('/api/creation-parser/jobs/' + imported['id'] + '/result').json()
    data = b'\x00\x00\x00\x18ftypmp42' + b'fixture-video' * 8

    def upstream(request):
        if request.url.path.endswith('/video-synthesis'):
            assert request.headers['x-dashscope-async'] == 'enable'
            return httpx.Response(200, json={'output': {'task_id': 'fixture-task'}})
        if '/tasks/' in request.url.path:
            return httpx.Response(200, json={'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://example.com/result.mp4'}})
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=data)

    client.app.state.image_edit_transport = httpx.MockTransport(upstream)
    response = client.post('/api/creation-parser/previews', json={
        'project_id': analysis['repository']['id'], 'node_id': 'video', 'prompt': 'flying bird', 'kind': 'video',
        'model': 'wan2.6-t2v', 'provider': 'qwen', 'api_base': 'https://dashscope.aliyuncs.com/api/v1', 'api_key': 'synthetic-video-key'})
    assert response.status_code == 202, response.text
    result = wait_preview(client, response.json()['id'])
    assert result['status'] == 'ready', result
    partial = client.get(result['artifact']['url'], headers={'Range': 'bytes=0-11'})
    assert partial.status_code == 206
    assert partial.content == data[:12]
    with client.app.state.repository.connection() as db:
        assert 'synthetic-video-key' not in str([tuple(row) for row in db.execute('SELECT * FROM frontend_jobs')])
