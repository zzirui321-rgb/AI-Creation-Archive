import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from aica_gateway.app import create_app
from conftest import upload
from fastapi.testclient import TestClient

PASSWORD = "a-strong-test-password-2026"
DOCUMENT = {"modules": [{"id": "one", "title": "Concept", "prompt": "a cat"},
                        {"id": "two", "title": "Render", "prompt": "render"}],
            "edges": [{"source": "one", "target": "two"}], "goldenPath": ["one", "two"]}


def login(client, username, register=True):
    if register:
        result = client.post("/api/auth/register", json={"username": username, "name": username, "password": PASSWORD})
        assert result.status_code == 201, result.text
        assert "password" not in result.text
    result = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["access_token"]}


def repository(client, headers, name="cats", visibility="private"):
    result = client.post("/api/repos", headers=headers, json={"name": name, "title": "Cats", "visibility": visibility})
    assert result.status_code == 201, result.text
    return "/api/repos/" + result.json()["repository"]["id"]


def commit(client, headers, url, head=None, branch="main", document=None):
    return client.post(url + "/commits", headers=headers,
                       json={"expected_head": head, "branch": branch, "message": "Update cat",
                             "document": DOCUMENT if document is None else document})


@pytest.fixture
def platform_client(tmp_path):
    app = create_app(tmp_path / "account-platform", tokens={})
    with TestClient(app) as client:
        yield client


def test_accounts_sessions_logout_expiry_and_password_storage(platform_client):
    client = platform_client
    headers = login(client, "alice")
    assert client.get("/api/auth/me", headers=headers).json()["user"]["username"] == "alice"
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/auth/login", json={"username": "missing", "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/register", json={"username": "alice", "name": "a", "password": PASSWORD}).status_code == 409
    with client.app.state.repository.connection() as db:
        row = db.execute("SELECT * FROM users").fetchone()
        assert PASSWORD.encode() not in row["password_hash"]
        session = db.execute("SELECT * FROM sessions").fetchone()
        assert headers["Authorization"].split()[1] not in session["digest"]
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    headers = login(client, "alice", False)
    with client.app.state.repository.connection(write=True) as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_login_throttling_and_body_limits(platform_client):
    client = platform_client
    login(client, "alice")
    for _ in range(9):
        assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 429
    assert client.post("/api/auth/register", content=b"x" * 20000).status_code == 413
    assert client.post("/api/repos", content=b"x" * (6 * 1024 * 1024)).status_code == 422


def test_private_public_search_and_write_isolation(platform_client):
    client = platform_client
    alice = login(client, "alice")
    bob = login(client, "bob")
    private = repository(client, alice)
    assert client.get(private).status_code == 404
    assert client.get(private, headers=bob).status_code == 404
    assert commit(client, bob, private).status_code == 404
    assert client.get("/api/repos").json()["repositories"] == []
    assert len(client.get("/api/repos?scope=mine", headers=alice).json()["repositories"]) == 1
    assert client.get("/api/repos?scope=mine").status_code == 401
    assert client.post("/api/repos", headers=alice, json={"name": "CATS", "title": "Duplicate"}).status_code == 409
    assert client.patch(private + "/visibility", headers=bob, json={"visibility": "public"}).status_code == 404
    assert client.patch(private + "/visibility", headers=alice, json={"visibility": "public"}).status_code == 200
    assert client.get(private).status_code == 200
    assert commit(client, bob, private).status_code == 404
    assert len(client.get("/api/repos?q=cat").json()["repositories"]) == 1
    assert client.get("/api/repos?q=no-match").json()["repositories"] == []
    assert client.get(private, headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_branches_history_conflicts_and_fork_snapshot(platform_client):
    client = platform_client
    alice = login(client, "alice")
    bob = login(client, "bob")
    url = repository(client, alice, visibility="public")
    assert client.get(url + "/graph").status_code == 404
    first = commit(client, alice, url)
    assert first.status_code == 201, first.text
    head = first.json()["commit"]["id"]
    assert commit(client, alice, url).status_code == 409
    assert client.post(url + "/branches", headers=alice, json={"name": "experiment/cat"}).status_code == 201
    assert client.post(url + "/branches", headers=alice, json={"name": "experiment/cat"}).status_code == 409
    assert client.post(url + "/branches", headers=alice, json={"name": "../bad"}).status_code == 422
    changed = {"modules": [{"id": "new", "prompt": "dog"}], "edges": []}
    next_commit = commit(client, alice, url, head, "experiment/cat", changed)
    assert next_commit.status_code == 201, next_commit.text
    assert client.get(url + "/graph").json()["document"] == DOCUMENT
    assert client.get(url + "/graph?branch=experiment/cat").json()["document"] == changed
    history = client.get(url + "/commits?branch=experiment/cat").json()["commits"]
    assert [item["id"] for item in history] == [next_commit.json()["commit"]["id"], head]
    result = client.post(url + "/forks", headers=bob, json={"name": "my-cats", "title": "Forked cats"})
    assert result.status_code == 201, result.text
    fork = result.json()["repository"]
    fork_url = "/api/repos/" + fork["id"]
    assert fork["parent_commit"] == head
    assert client.get(fork_url).status_code == 404
    assert commit(client, bob, fork_url, fork["head"], document=changed).status_code == 201
    assert client.get(url + "/graph").json()["document"] == DOCUMENT
    assert client.get(fork_url + "/graph", headers=bob).json()["document"] == changed
    assert client.get(fork_url + "/graph?commit_id=" + head, headers=bob).status_code == 404
    assert client.patch(url + "/visibility", headers=alice, json={"visibility": "private"}).status_code == 200
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(fork_url + "/graph", headers=bob).status_code == 200


@pytest.mark.parametrize("document", [
    {"modules": [{"id": "a"}, {"id": "a"}]},
    {"modules": [{"id": "a"}], "edges": [{"source": "b", "target": "a"}]},
    {"modules": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]},
    {"modules": [{"id": "a", "depends_on": ["a"]}]},
    {"modules": [{"id": "a", "params": {"api_key": "never-save"}}]},
    {"modules": [{"id": "a"}], "goldenPath": ["missing"]},
    {"aica_version": "0.1", "modules": [{"id": "a"}]},
])
def test_reject_invalid_graphs_without_moving_head(platform_client, document):
    client = platform_client
    headers = login(client, "alice")
    url = repository(client, headers)
    result = commit(client, headers, url, document=document)
    assert result.status_code == 422, result.text
    assert "never-save" not in result.text
    assert client.get(url + "/branches", headers=headers).json()["branches"][0]["head"] is None


def test_accounts_repositories_and_sessions_survive_restart(tmp_path):
    directory = tmp_path / "persisted"
    with TestClient(create_app(directory, tokens={})) as client:
        headers = login(client, "alice")
        url = repository(client, headers)
        assert commit(client, headers, url).status_code == 201
    with TestClient(create_app(directory, tokens={})) as client:
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        assert client.get(url + "/graph", headers=headers).json()["document"] == DOCUMENT
        assert login(client, "alice", False)


def test_archive_publication_and_forked_artifacts(platform_client, bundle_factory):
    client = platform_client
    alice = login(client, "alice")
    bob = login(client, "bob")
    project = client.post("/api/projects", headers=alice, json={"title": "Original source"}).json()["project"]["id"]
    bundle = bundle_factory()
    client.headers.update(alice)
    imported = upload(client, "/api/projects/" + project, bundle).json()["import_id"]
    client.headers.pop("Authorization")
    url = repository(client, alice, visibility="public")
    result = client.post(url + "/archive-commits", headers=alice,
                         json={"project_id": project, "import_id": imported, "expected_head": None, "message": "Publish archive"})
    assert result.status_code == 201, result.text
    head = result.json()["commit"]["id"]
    assert client.get(url + "/graph").json()["document"] == bundle["archive"]
    assert client.get(f"{url}/artifacts/output?commit_id={head}").content == bundle["content"]
    assert client.get("/api/projects/" + project + "/archive", headers=bob).status_code == 404
    fork = client.post(url + "/forks", headers=bob, json={"name": "fork", "title": "Fork"}).json()["repository"]
    fork_url = "/api/repos/" + fork["id"]
    assert client.get(f"{fork_url}/artifacts/output?commit_id={fork['head']}", headers=bob).content == bundle["content"]
    assert client.get(f"{fork_url}/artifacts/output?commit_id={fork['head']}").status_code == 404
    assert client.get(f"{fork_url}/artifacts/output?commit_id={head}", headers=bob).status_code == 404
    assert client.get(f"{url}/artifacts/missing?commit_id={head}").status_code == 404
    assert client.post(fork_url + "/archive-commits", headers=bob,
                       json={"project_id": project, "import_id": imported, "expected_head": fork["head"],
                             "message": "Cannot steal private archive"}).status_code == 404


def test_request_document_has_no_legacy_five_mib_limit(platform_client):
    client = platform_client
    headers = login(client, "alice")
    url = repository(client, headers)
    document = {"modules": [{"id": "a"}], "extra": "x" * (5 * 1024 * 1024)}
    assert commit(client, headers, url, document=document).status_code == 201
    assert PASSWORD not in json.dumps(client.get(url, headers=headers).json())


def test_parallel_commits_have_one_winner_and_failure_rolls_back(platform_client):
    client = platform_client
    headers = login(client, "alice")
    url = repository(client, headers)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(commit, client, headers, url) for _ in range(2)]
        responses = [future.result() for future in futures]
    assert sorted(result.status_code for result in responses) == [201, 409]
    head = next(result.json()["commit"]["id"] for result in responses if result.status_code == 201)
    with client.app.state.repository.connection(write=True) as db:
        db.executescript("CREATE TRIGGER reject_new_commit BEFORE INSERT ON repo_commits "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic transaction failure'); END;")
    assert commit(client, headers, url, head).status_code == 503
    history = client.get(url + "/commits", headers=headers).json()["commits"]
    assert [record["id"] for record in history] == [head]
    assert client.get(url + "/branches", headers=headers).json()["branches"][0]["head"] == head


def test_c_export_record_and_cors_patch(tmp_path):
    app = create_app(tmp_path / "cors-platform", tokens={}, cors_origins=["http://127.0.0.1:5173"])
    with TestClient(app) as client:
        headers = login(client, "alice")
        url = repository(client, headers)
        record = {"format": "aica-ui-module-record", "title": "C export", "description": "From Studio",
                  "parent_id": None, "steps": DOCUMENT["modules"], "edges": DOCUMENT["edges"],
                  "golden_path": DOCUMENT["goldenPath"]}
        assert commit(client, headers, url, document=record).status_code == 201
        assert client.get(url + "/graph", headers=headers).json()["document"] == record
        response = client.options(url + "/visibility", headers={"Origin": "http://127.0.0.1:5173",
                                  "Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "authorization,content-type"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        assert "PATCH" in response.headers["access-control-allow-methods"]
        response = client.get(url, headers=headers)
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("body", [b'{"username":"alice","name":"\\ud800","password":"long-password-here"}',
                                  b'{"username":"alice","username":"bob"}',
                                  b'{"x":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}'])
def test_invalid_json_is_bounded_and_does_not_echo_input(platform_client, body):
    response = platform_client.post("/api/auth/register", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["message"] == "Invalid JSON request"


def test_historical_branch_and_cross_repository_commit_isolation(platform_client):
    client = platform_client
    headers = login(client, "alice")
    url = repository(client, headers)
    assert client.post(url + "/forks", headers=headers, json={"name": "empty", "title": "Empty"}).status_code == 409
    head = commit(client, headers, url).json()["commit"]["id"]
    assert commit(client, headers, url, head).status_code == 201
    result = client.post(url + "/branches", headers=headers, json={"name": "historical", "from_commit": head})
    assert result.status_code == 201
    assert result.json()["branch"]["head"] == head
    other = repository(client, headers, "other")
    assert client.post(other + "/branches", headers=headers, json={"name": "steal", "from_commit": head}).status_code == 404
    assert client.get(other + "/graph?commit_id=" + head, headers=headers).status_code == 404
