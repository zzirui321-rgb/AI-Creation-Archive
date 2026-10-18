from concurrent.futures import ThreadPoolExecutor

from aica_schema.transfer import require_transfer
from conftest import upload
from test_platform_api import login
from test_platform_api import platform_client as platform_client


def project(client, title="MCP original"):
    return "/api/projects/" + client.post("/api/projects", json={"title": title}).json()["project"]["id"]


def test_upload_publication_is_private_atomic_idempotent_and_latest(platform_client, bundle_factory):
    client = platform_client
    client.headers.update(login(client, "mcp-owner"))
    url = project(client)
    first = bundle_factory(title="First archive")
    result = upload(client, url, first)
    assert result.status_code == 201, result.text
    require_transfer(result.json(), "receipt")
    publication = client.get(url + "/repository").json()["repository"]
    assert publication["state"] == "ready"
    repo = "/api/repos/" + publication["id"]
    assert client.get(repo).json()["repository"]["visibility"] == "private"
    assert len(client.get("/api/repos?scope=mine").json()["repositories"]) == 1
    assert client.get(repo + "/graph").json()["document"]["graph"] == first["archive"]["graph"]
    version = {"commit_id": publication["commit_id"]}
    assert client.get(repo + "/archive", params=version).content == first["raw"]
    assert client.get(repo + "/archive-info", params=version).json()["artifacts"][0]["available"]
    assert client.get(repo + "/artifacts/output", params=version).content == first["content"]
    assert upload(client, url, first).json() == result.json()
    assert len(client.get(repo + "/commits").json()["commits"]) == 1
    second = bundle_factory(title="Next archive")
    assert upload(client, url, second).status_code == 201
    current = client.get(url + "/repository").json()["repository"]
    assert current["id"] == publication["id"] and current["commit_id"] != publication["commit_id"]
    assert upload(client, url, first).json() == result.json()
    assert client.post(url + "/repository").json()["repository"] == current
    assert client.get(repo + "/graph").json()["commit"]["id"] == current["commit_id"]
    assert len(client.get(repo + "/commits").json()["commits"]) == 2
    assert client.get(repo + "/archive", params=version).content == first["raw"]
    client.headers.update(login(client, "mcp-other"))
    assert client.get(repo).status_code == 404
    assert client.get(repo + "/archive", params=version).status_code == 404
    assert client.get(repo + "/archive-info", params=version).status_code == 404
    assert client.get(url + "/repository").status_code == 404
    assert client.post(url + "/repository").status_code == 404


def test_concurrent_first_upload_and_old_import_repair(platform_client, bundle_factory):
    client = platform_client
    client.headers.update(login(client, "parallel-mcp"))
    url = project(client)
    bundle = bundle_factory()
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: upload(client, url, bundle), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    assert len(client.get("/api/repos?scope=mine").json()["repositories"]) == 1
    store = client.app.state.repository
    service = client.app.state.platform
    url2 = project(client, "Previously uploaded")
    store.import_publisher = None
    old = upload(client, url2, bundle).json()
    latest = upload(client, url2, bundle_factory(title="Latest valid archive")).json()
    store.import_publisher = service.publish_import
    assert client.get(url2 + "/repository").json()["repository"] is None
    repaired = client.post(url2 + "/repository").json()
    assert repaired["import_id"] == latest["import_id"]
    assert client.post(url2 + "/repository").json() == repaired
    assert upload(client, url2, bundle).json() == old
    repo = "/api/repos/" + repaired["repository"]["id"]
    assert len(client.get(repo + "/commits").json()["commits"]) == 1
    assert client.get(repo + "/graph").json()["document"]["metadata"]["title"] == "Latest valid archive"


def test_publication_failure_rolls_back_and_scoped_token_can_pull(platform_client, bundle_factory):
    client = platform_client
    client.headers.update(login(client, "mcp-rollback"))
    url = project(client)
    other = project(client, "Other project")
    token = client.post("/api/mcp/upload-tokens", json={"project_id": url.split('/')[-1]}).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    bundle = bundle_factory()
    store = client.app.state.repository
    with store.connection(write=True) as db:
        db.execute("CREATE TRIGGER fail_import_commit BEFORE INSERT ON repo_commits BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    assert client.post(url + "/imports", headers={**bundle["headers"], **headers}, content=bundle["path"].read_bytes()).status_code == 503
    assert client.get(url + "/archives").json()["archives"] == []
    assert client.get("/api/repos?scope=mine").json()["repositories"] == []
    with store.connection(write=True) as db:
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_import_commit")
    receipt = client.post(url + "/imports", headers={**bundle["headers"], **headers}, content=bundle["path"].read_bytes()).json()
    require_transfer(receipt, "receipt")
    assert client.get(url + "/repository", headers=headers).json()["repository"]["state"] == "ready"
    pulled = client.get(url + "/archive", headers=headers)
    assert pulled.json()["archive"] == bundle["archive"]
    assert client.get(url + "/artifacts/output", headers=headers).content == bundle["content"]
    assert client.get(other + "/archive", headers=headers).status_code == 401
    assert client.get("/api/repos?scope=mine", headers=headers).status_code == 401
