import base64
import hashlib

import pytest
from aica_runtime.graph.materials import read_material
from aica_schema.protocol import ProtocolError, dumps, empty_archive
from aica_schema.transfer import require_transfer
from test_platform_api import commit, login, repository


def test_scoped_mcp_upload_receipts_embedded_bytes_and_revocation(client, bundle_factory):
    owner = login(client, "mcp-owner")
    other = login(client, "mcp-other")
    project = client.post("/api/projects", headers=owner, json={"title": "MCP"}).json()["project"]["id"]
    issued = client.post("/api/mcp/upload-tokens", headers=owner, json={"project_id": project})
    assert issued.status_code == 201
    token = issued.json()
    authorization = {"Authorization": "Bearer " + token["access_token"]}
    assert client.get("/api/auth/me", headers=authorization).status_code == 401
    assert client.post("/api/mcp/upload-tokens", headers=other, json={"project_id": project}).status_code == 404
    bundle = bundle_factory()
    headers = {**bundle["headers"], **authorization}
    url = f"/api/projects/{project}/imports"
    first = client.post(url, headers=headers, content=bundle["path"].read_bytes())
    again = client.post(url, headers=headers, content=bundle["path"].read_bytes())
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json() == again.json()
    require_transfer(first.json(), "receipt")
    raw = bundle["archive"]
    raw["extensions"]["aica.embedded_artifacts"] = {
        "version": "0.1", "encoding": "base64",
        "objects": {raw["artifacts"][0]["sha256"]: base64.b64encode(bundle["content"]).decode()}}
    content = dumps(raw).encode()
    digest = hashlib.sha256(content).hexdigest()
    result = client.post(url, content=content, headers={**authorization, "Content-Type": "application/json",
                         "Idempotency-Key": "sha256:" + digest, "X-AiCA-Bundle-SHA256": digest})
    assert result.status_code == 201, result.text
    require_transfer(result.json(), "receipt")
    image = client.get(f"/api/projects/{project}/artifacts/output?import_id={result.json()['import_id']}", headers=owner)
    assert image.content == bundle["content"]
    assert client.get(f"/api/projects/{project}/artifacts/output", headers=other).status_code == 404
    assert client.delete(f"/api/mcp/upload-tokens/{token['id']}", headers=owner).status_code == 200
    assert client.post(url, headers=headers, content=bundle["path"].read_bytes()).status_code == 401


def test_large_json_keeps_original_bytes_and_exceeds_previous_limits(client):
    owner = login(client, "large-json")
    project = client.post("/api/projects", headers=owner, json={"title": "Large JSON"}).json()["project"]["id"]
    archive = empty_archive("Large archive")
    archive["extensions"]["large_text"] = "x" * (17 * 1024 * 1024)
    raw = dumps(archive).encode()
    response = client.post(f"/api/projects/{project}/archive-json", headers={**owner, "Content-Type": "application/json"}, content=raw)
    assert response.status_code == 201, response.text
    stored = client.app.state.repository.archive_bytes("user:" + client.get("/api/auth/me", headers=owner).json()["user"]["id"], project, response.json()["import_id"])
    assert stored == raw
    repo = repository(client, owner, name="large-document")
    result = commit(client, owner, repo, document={"modules": [{"id": "large", "prompt": "normal"}], "ui": {"large_text": "x" * (6 * 1024 * 1024)}})
    assert result.status_code == 201, result.text


def test_large_material_uses_marked_excerpt_after_full_hash_verification():
    content = ("a long creative brief " * 10000).encode()
    artifact = {"id": "brief", "mime_type": "text/plain", "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content)}
    material, budget_bytes = read_material(artifact, lambda *_: content, 24 * 1024)
    assert material["selection"] == "partial_head_middle_tail_not_complete"
    assert material["omitted_characters"] > 0
    assert len(budget_bytes) <= 24 * 1024
    with pytest.raises(ProtocolError, match="hash mismatch"):
        read_material(artifact, lambda *_: content + b"corrupt", 24 * 1024)
