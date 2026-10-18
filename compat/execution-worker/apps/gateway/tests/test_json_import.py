import base64
import copy
import hashlib

import pytest
from aica_schema.protocol import EMBEDDED_EXTENSION, dumps
from aica_schema.transfer import require_transfer
from test_backend_api import execute


def portable_document(bundle):
    path, report, image, document = bundle
    archive = copy.deepcopy(document)
    sha256 = hashlib.sha256(image).hexdigest()
    archive["extensions"][EMBEDDED_EXTENSION] = {"version": "0.1", "encoding": "base64",
                                               "objects": {sha256: base64.b64encode(image).decode()}}
    return archive, image


def post_json(client, url, document, **changes):
    content = dumps(document).encode() if isinstance(document, dict) else document
    sha256 = hashlib.sha256(content).hexdigest()
    headers = {"Content-Type": "application/json", "Idempotency-Key": "sha256:" + sha256,
               "X-AiCA-Bundle-SHA256": sha256, **changes}
    response = client.post(url + "/imports", content=content, headers=headers)
    return response, sha256, content


def test_json_import_restores_real_bytes_is_idempotent_and_can_execute(client, app, bundle_factory, workflow):
    document, image = portable_document(bundle_factory(workflow))
    project = client.post("/api/projects", json={"title": "Single JSON"}).json()["project"]
    url = "/api/projects/" + project["id"]
    imported, sha256, raw = post_json(client, url, document)
    assert imported.status_code == 201, imported.text
    receipt = imported.json()
    require_transfer(receipt, "receipt")
    assert receipt["format"] == "aica-json/0.1" and receipt["archive_sha256"] == sha256
    assert app.state.store.read_blob(project["id"], sha256) == raw
    assert client.get(url + "/artifacts/picture").content == image
    assert EMBEDDED_EXTENSION not in client.get(url + "/archive").json()["archive"]["extensions"]
    assert post_json(client, url, document)[0].json() == receipt
    assert len(client.get(url + "/archives").json()["archives"]) == 1
    assert post_json(client, url, document, Authorization="Bearer bob-development-test-token-1234567890")[0].status_code == 404
    job, payload = execute(client, url)
    assert job["state"] == "succeeded", job
    assert not list((app.state.store.directory / "uploads").iterdir())


@pytest.mark.parametrize("damage", ["missing", "hash", "base64", "size", "orphan", "invalid_json", "invalid_utf8", "request_hash"])
def test_json_import_rejects_incomplete_or_damaged_content_without_publishing(client, app, bundle_factory, workflow, damage):
    document, image = portable_document(bundle_factory(workflow))
    project = client.post("/api/projects", json={"title": "Rejected JSON"}).json()["project"]
    url = "/api/projects/" + project["id"]
    sha256 = document["artifacts"][0]["sha256"]
    objects = document["extensions"][EMBEDDED_EXTENSION]["objects"]
    if damage == "missing":
        del document["extensions"][EMBEDDED_EXTENSION]
    elif damage == "hash":
        objects[sha256] = base64.b64encode(b"tampered").decode()
    elif damage == "base64":
        objects[sha256] = "invalid!"
    elif damage == "size":
        document["artifacts"][0]["size"] += 1
    elif damage == "orphan":
        objects["0" * 64] = ""
    elif damage == "invalid_json":
        document = b'{"metadata":1,"metadata":2}'
    elif damage == "invalid_utf8":
        document = b"\xff"
    headers = {"X-AiCA-Bundle-SHA256": "0" * 64, "Idempotency-Key": "sha256:" + "0" * 64} if damage == "request_hash" else {}
    response, request_hash, raw = post_json(client, url, document, **headers)
    assert response.status_code == 422, response.text
    assert client.get(url + "/archives").json()["archives"] == []
    assert not list((app.state.store.directory / "uploads").iterdir())
    if damage == "missing":
        assert response.json()["code"] == "missing_embedded_artifacts"


def test_json_size_limit_is_enforced_before_reading_the_body(client):
    project = client.post("/api/projects", json={"title": "Too large"}).json()["project"]
    response, sha256, raw = post_json(client, "/api/projects/" + project["id"], b"", **{"Content-Length": str(129 * 1024 * 1024)})
    assert response.status_code == 413
