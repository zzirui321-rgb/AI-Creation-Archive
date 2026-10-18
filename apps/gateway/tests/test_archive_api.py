import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from aica_gateway.app import create_app
from aica_mcp.transfer import upload_bundle
from aica_schema.protocol import ProtocolError
from aica_schema.transfer import require_transfer
from conftest import OTHER_TOKEN, TOKEN, project_url, upload
from fastapi.testclient import TestClient


def test_a_upload_client_preserves_archive_and_reads_after_source_disappears(client, app, bundle_factory):
    bundle = bundle_factory()
    url = project_url(client)

    def send(request):
        response = client.post(request.url.path, headers=dict(request.headers), content=request.read())
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    transport = httpx.MockTransport(send)
    first = upload_bundle(bundle["path"], "https://platform.test" + url + "/imports", TOKEN, True, transport)
    second = upload_bundle(bundle["path"], "https://platform.test" + url + "/imports", TOKEN, True, transport)
    assert first == second
    require_transfer(first["receipt"], "receipt")
    shutil.rmtree(bundle["root"])
    snapshot = client.get(url + "/archive").json()
    assert snapshot["archive"] == bundle["archive"]
    graph = client.get(url + "/graph").json()
    assert graph["graph"] == bundle["archive"]["graph"]
    assert graph["import_id"] == first["receipt"]["import_id"]
    response = client.get(url + "/artifacts/output")
    assert response.content == bundle["content"]
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-aica-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert response.headers["cache-control"] == "private, no-store"
    with app.state.repository.connection() as database:
        row = database.execute("SELECT archive FROM imports").fetchone()
        assert row["archive"] == bundle["raw"]


def test_authentication_and_cross_project_access_do_not_expose_data(client, bundle_factory):
    bundle = bundle_factory()
    url = project_url(client)
    assert upload(client, url, bundle).status_code == 201
    for credential in ("", "Bearer wrong-token", "Basic abc"):
        response = client.get(url + "/graph", headers={"Authorization": credential})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert bundle["archive"]["metadata"]["project_id"] not in response.text
    for suffix in ("/archive", "/graph", "/archives", "/artifacts/output"):
        assert client.get(url + suffix, headers={"Authorization": "Bearer " + OTHER_TOKEN}).status_code == 404
    headers = {**bundle["headers"], "Authorization": "Bearer " + OTHER_TOKEN}
    assert client.post(url + "/imports", headers=headers, content=bundle["path"].read_bytes()).status_code == 404
    assert client.get("/api/projects", headers={"Authorization": "Bearer " + OTHER_TOKEN}).json()["projects"] == []


def test_concurrent_retry_publishes_once_and_same_bundle_can_target_another_project(client, app, bundle_factory):
    bundle = bundle_factory()
    url = project_url(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(client, url, bundle), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    other = upload(client, project_url(client), bundle)
    assert other.status_code == 201
    assert other.json()["import_id"] != responses[0].json()["import_id"]
    with app.state.repository.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 2
        assert database.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 2
    assert not list((app.state.repository.directory / "staging").iterdir())


def test_failed_publish_rolls_back_head_receipt_and_files(client, app, bundle_factory):
    first = bundle_factory()
    url = project_url(client)
    original = upload(client, url, first).json()
    store = app.state.repository
    with store.connection(write=True) as database:
        database.executescript("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts "
                               "BEGIN SELECT RAISE(ABORT, 'injected persistence failure'); END;")
    second = bundle_factory(content=b"different")
    response = upload(client, url, second)
    assert response.status_code == 503
    assert str(store.directory) not in response.text
    assert client.get(url + "/archive").json()["import_id"] == original["import_id"]
    assert len(client.get(url + "/archives").json()["archives"]) == 1
    assert len(list((store.directory / "snapshots").iterdir())) == 1
    assert not list((store.directory / "staging").iterdir())
    assert not list((store.directory / "uploads").iterdir())
    with store.connection(write=True) as database:
        database.execute("DROP TRIGGER fail_receipt")
    assert upload(client, url, second).status_code == 201


def test_corrupt_upload_does_not_replace_an_existing_snapshot(client, app, bundle_factory):
    bundle = bundle_factory()
    url = project_url(client)
    original = upload(client, url, bundle).json()
    damaged = bundle["path"].read_bytes() + b"changed"
    assert client.post(url + "/imports", headers=bundle["headers"], content=damaged).status_code == 422
    assert client.get(url + "/archive").json()["import_id"] == original["import_id"]
    assert not list((app.state.repository.directory / "uploads").iterdir())


def test_historical_artifacts_remain_scoped_to_their_snapshot(client, bundle_factory):
    first = bundle_factory(content=b"first image")
    second = bundle_factory(content=b"second image")
    url = project_url(client)
    old = upload(client, url, first).json()["import_id"]
    new = upload(client, url, second).json()["import_id"]
    assert new != old
    assert client.get(url + "/artifacts/output").content == second["content"]
    assert client.get(url + "/artifacts/output", params={"import_id": old}).content == first["content"]
    assert client.get(url + "/archive", params={"import_id": old}).json()["archive"] == first["archive"]
    assert client.get(url + "/archive", params={"import_id": str(uuid4())}).status_code == 404
    assert client.get(url + "/artifacts/missing").status_code == 404


def test_persistence_survives_service_restart(tmp_path, bundle_factory):
    directory = tmp_path / "durable-platform"
    bundle = bundle_factory()
    with TestClient(create_app(directory, {"alice": TOKEN}), headers={"Authorization": "Bearer " + TOKEN}) as first:
        url = project_url(first)
        receipt = upload(first, url, bundle).json()
    with TestClient(create_app(directory, {"alice": TOKEN}), headers={"Authorization": "Bearer " + TOKEN}) as second:
        assert upload(second, url, bundle).status_code == 200
        assert upload(second, url, bundle).json() == receipt
        assert second.get(url + "/artifacts/output").content == bundle["content"]


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/javascript"])
def test_active_content_is_downloaded_without_inline_execution(client, bundle_factory, mime):
    url = project_url(client)
    assert upload(client, url, bundle_factory(content=b"<script>example</script>", mime=mime)).status_code == 201
    response = client.get(url + "/artifacts/output")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]


def test_corrupt_stored_object_is_not_served(client, app, bundle_factory):
    bundle = bundle_factory()
    url = project_url(client)
    import_id = upload(client, url, bundle).json()["import_id"]
    sha256 = bundle["archive"]["artifacts"][0]["sha256"]
    (app.state.repository.directory / "snapshots" / import_id / sha256).write_bytes(b"corrupt")
    response = client.get(url + "/artifacts/output")
    assert response.status_code == 503 and response.json()["code"] == "storage_corrupt"


@pytest.mark.parametrize("header,value,status", [
    ("Content-Type", "application/json", 422),
    ("Content-Encoding", "gzip", 422),
    ("Content-Length", str(513 * 1024 * 1024), 413),
    ("Content-Length", "invalid", 422),
    ("Idempotency-Key", "different", 422),
])
def test_invalid_headers_are_rejected_without_publishing(client, app, bundle_factory, header, value, status):
    bundle = bundle_factory()
    url = project_url(client)
    headers = {**bundle["headers"], header: value}
    response = client.post(url + "/imports", headers=headers, content=bundle["path"].read_bytes())
    assert response.status_code == status
    assert client.get(url + "/archive").status_code == 404
    assert not list((app.state.repository.directory / "uploads").iterdir())


def test_chunked_request_is_limited_without_content_length(client, app, bundle_factory, monkeypatch):
    import aica_gateway.app as api

    bundle = bundle_factory()
    url = project_url(client)
    monkeypatch.setattr(api, "BUNDLE_LIMIT", 32)
    response = client.post(url + "/imports", headers=bundle["headers"], content=iter([b"a" * 20, b"b" * 20]))
    assert response.status_code == 413
    assert not list((app.state.repository.directory / "uploads").iterdir())


def test_service_refuses_in_checkout_data_and_concurrent_writers(tmp_path):
    checkout = Path(__file__).resolve().parents[3]
    with pytest.raises(ProtocolError, match="outside the checkout"):
        create_app(checkout / "private-data", {"alice": TOKEN})
    directory = tmp_path / "locked-platform"
    with TestClient(create_app(directory, {"alice": TOKEN})):
        with pytest.raises(ProtocolError, match="already running"):
            with TestClient(create_app(directory, {"alice": TOKEN})):
                pytest.fail("A second writer acquired the same data directory")


def test_input_errors_do_not_echo_submitted_secrets_and_pagination_is_bounded(client):
    response = client.post("/api/projects", json={"title": {"secret": TOKEN}})
    assert response.status_code == 422 and TOKEN not in response.text
    assert client.get("/api/projects", params={"limit": 201}).status_code == 422
    assert client.get("/api/projects/not-a-uuid/archive").status_code == 422
