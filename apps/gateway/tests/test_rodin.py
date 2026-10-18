import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest
from aica_gateway import rodin as module
from aica_gateway.app import create_app
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('HYPER3D_API_KEY', 'synthetic-server-secret')
    monkeypatch.setenv('AICA_RODIN_POLL_SECONDS', '0.01')
    monkeypatch.delenv('AICA_HYPER3D_KEY_FILE', raising=False)
    calls = []
    mode = {'value': 'success'}
    def provider(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/rodin'):
            if mode['value'] == 'reject':
                return httpx.Response(201, json={'error': 'API_INSUFFICIENT_FUNDS'})
            if mode['value'] == 'timeout':
                raise httpx.ReadTimeout('Synthetic timeout')
            return httpx.Response(201, json={'uuid': 'external-task', 'jobs': {'subscription_key': 'internal-only'}})
        if request.url.path.endswith('/status'):
            return httpx.Response(200, json={'jobs': [{'status': 'Done'}]})
        return httpx.Response(200, json={'list': [{'name': 'model.glb', 'url': 'https://example.com/model.glb'}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(provider), **kwargs))
    async def download(client, url):
        return b'glTF' + b'\x00' * 20
    monkeypatch.setattr(module, 'public_download', download)
    with TestClient(create_app(tmp_path / 'platform', tokens={})) as client:
        def login(name):
            credentials = {'username': name, 'password': 'synthetic-password-2026'}
            assert client.post('/api/auth/register', json={**credentials, 'name': name}).status_code == 201
            return {'Authorization': 'Bearer ' + client.post('/api/auth/login', json=credentials).json()['access_token']}
        owner, other = login('alice'), login('bob')
        repo = client.post('/api/repos', headers=owner, json={'name': 'image', 'title': 'Image'}).json()['repository']
        uri = '/api/creation-parser/previews/' + 'a' * 32 + '/artifact'
        document = {'steps': [{'id': 'portrait', 'artifacts': [{'id': 'portrait-file', 'uri': uri, 'mime': 'image/png'}]}]}
        head = client.post('/api/repos/' + repo['id'] + '/commits', headers=owner, json={'expected_head': None, 'message': 'Image', 'document': document}).json()['commit']['id']
        monkeypatch.setattr(client.app.state.frontend_v2, 'source_image', lambda subject, source: Image.new('RGB', (8, 8)))
        body = {'request_id': str(uuid4()), 'expected_head': head, 'node_id': 'portrait', 'artifact_id': 'portrait-file', 'source_uri': uri, 'consent': True}
        yield client, owner, other, repo['id'], body, calls, mode


def wait(client, identifier, headers):
    for _ in range(100):
        result = client.get('/api/3d/jobs/' + identifier, headers=headers).json()
        if result['state'] in {'succeeded', 'failed', 'unknown', 'pending'}:
            return result
        time.sleep(.01)
    pytest.fail('Synthetic job did not finish')


def test_idempotency_recipe_private_model_and_fork(setup):
    client, owner, other, repo, body, calls, mode = setup
    route = '/api/3d/repos/' + repo + '/jobs'
    assert client.post(route, headers=other, json=body).status_code == 404
    assert client.post(route, headers=owner, json={**body, 'consent': False}).status_code == 422
    assert client.post(route, headers=owner, json={**body, 'node_id': 'missing'}).status_code == 404
    created = client.post(route, headers=owner, json=body)
    assert created.status_code == 202
    job = wait(client, created.json()['id'], owner)
    assert job['state'] == 'succeeded'
    assert 'synthetic-server-secret' not in json.dumps(job) and 'internal-only' not in json.dumps(job)
    assert job['recipe']['source_commit'] == body['expected_head']
    duplicate = client.post(route, headers=owner, json=body)
    assert duplicate.json()['id'] == job['id']
    assert calls.count('/api/v2/rodin') == 1

    assert client.post(route, headers=owner, json={**body, 'prompt': 'different'}).status_code == 409
    assert client.get(job['artifact']['uri'], headers=owner).content.startswith(b'glTF')
    assert client.get(job['artifact']['uri'], headers=other).status_code == 404
    document = {'steps': [{'id': 'portrait'}, {'id': 'rodin-' + job['id'], 'artifacts': [job['artifact']]}]}
    assert client.post('/api/repos/' + repo + '/commits', headers=owner, json={'expected_head': body['expected_head'], 'message': '3D saved', 'document': document}).status_code == 201
    assert client.patch('/api/repos/' + repo + '/visibility', headers=owner, json={'visibility': 'public'}).status_code == 200
    fork = client.post('/api/repos/' + repo + '/forks', headers=other, json={'name': 'fork', 'title': 'Fork'}).json()['repository']
    assert client.patch('/api/repos/' + repo + '/visibility', headers=owner, json={'visibility': 'private'}).status_code == 200
    assert client.get('/api/3d/jobs/' + job['id'] + '/model?repo_id=' + fork['id'], headers=other).status_code == 200
    assert client.get('/api/3d/jobs/' + job['id'] + '/model?repo_id=' + fork['id']).status_code == 404


@pytest.mark.parametrize('mode,expected', [('reject', 'failed'), ('timeout', 'unknown')])
def test_no_automatic_paid_retry(setup, mode, expected):
    client, owner, other, repo, body, calls, config = setup
    config['value'] = mode
    url = '/api/3d/repos/' + repo + '/jobs'
    result = client.post(url, headers=owner, json=body)
    job = wait(client, result.json()['id'], owner)
    assert job['state'] == expected
    if expected == 'unknown':
        assert client.post(url, headers=owner, json={**body, 'request_id': str(uuid4())}).status_code == 409
    assert client.post(url, headers=owner, json=body).json()['id'] == job['id']
    assert calls.count('/api/v2/rodin') == 1


def test_download_blocks_private_hosts(monkeypatch):
    monkeypatch.setattr(module.socket, 'getaddrinfo', lambda *args, **kwargs: [(0, 0, 0, '', ('127.0.0.1', 443))])
    with pytest.raises(Exception, match='下载地址不可访问'):
        asyncio.run(module.public_download(None, 'https://localhost/model.glb'))


def test_native_mcp_archive_node_binding(setup, bundle_factory):
    client, owner, other, repo, body, calls, config = setup
    document = bundle_factory()['archive']
    result = client.post('/api/repos/' + repo + '/commits', headers=owner,
                         json={'expected_head': body['expected_head'], 'message': 'Native MCP archive', 'document': document})
    assert result.status_code == 201
    body.update(expected_head=result.json()['commit']['id'], node_id='creation', artifact_id='output',
                source_uri='/api/repos/' + repo + '/artifacts/output?commit_id=' + result.json()['commit']['id'])
    response = client.post('/api/3d/repos/' + repo + '/jobs', headers=owner, json=body)
    assert response.status_code == 202
    assert wait(client, response.json()['id'], owner)['state'] == 'succeeded'
    assert calls.count('/api/v2/rodin') == 1


def test_restart_resumes_only_queries(setup):
    client, owner, other, repo, body, calls, config = setup
    response = client.post('/api/3d/repos/' + repo + '/jobs', headers=owner, json=body)
    identifier = response.json()['id']
    assert wait(client, identifier, owner)['state'] == 'succeeded'
    client.app.state.rodin.update(identifier, state='running')
    client.app.state.rodin = module.Rodin(client.app.state.platform, client.app.state.frontend_v2)
    assert client.get('/api/3d/jobs/' + identifier, headers=owner).json()['state'] == 'pending'
    assert client.post('/api/3d/jobs/' + identifier + '/resume', headers=owner).status_code == 200
    assert wait(client, identifier, owner)['state'] == 'succeeded'
    assert calls.count('/api/v2/rodin') == 1


def test_3d_agent_handoff_preserves_model_bytes_and_graph(setup, tmp_path):
    from aica_mcp.transfer import read_portable_json

    client, owner, other, repo, body, calls, _ = setup
    response = client.post('/api/3d/repos/' + repo + '/jobs', headers=owner, json=body)
    job = wait(client, response.json()['id'], owner)
    assert job['state'] == 'succeeded'
    node_id = 'rodin-' + job['id']
    document = {'steps': [{'id': 'portrait', 'prompt': 'Original source'},
                         {'id': node_id, 'title': 'Saved 3D', 'prompt': '', 'model': 'Hyper3D Rodin Gen-2',
                          'inputs': {'source_node_id': 'portrait'}, 'artifacts': [job['artifact']]}],
                'edges': [{'source': 'portrait', 'target': node_id, 'kind': 'control'}]}
    saved = client.post('/api/repos/' + repo + '/commits', headers=owner,
                        json={'expected_head': body['expected_head'], 'message': '3D version', 'document': document})
    assert saved.status_code == 201
    url = '/api/repos/' + repo + '/handoff'
    params = {'commit_id': saved.json()['commit']['id']}
    assert client.get(url, headers=other, params=params).status_code == 404
    response = client.get(url, headers=owner, params=params)
    assert response.status_code == 200, response.text
    path = tmp_path / 'handoff.aica'
    path.write_bytes(response.content)
    archive, objects, _ = read_portable_json(path)
    assert 'aica.agent-handoff' in archive['extensions']
    assert len(archive['graph']['nodes']) == 2
    artifact = next(item for item in archive['artifacts'] if item['mime_type'] == 'model/gltf-binary')
    assert objects[artifact['sha256']] == b'glTF' + b'\x00' * 20
    assert artifact['producer_node_id'] in {item['id'] for item in archive['graph']['nodes']}
    assert calls.count('/api/v2/rodin') == 1
