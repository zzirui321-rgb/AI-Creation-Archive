import base64
import hashlib

import pytest
from aica_gateway.app import create_app
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_schema.protocol import dumps, empty_archive
from fastapi.testclient import TestClient

TOKEN = "alice-platform-test-token-0123456789"
OTHER_TOKEN = "bob-platform-test-token-01234567890"


@pytest.fixture
def bundle_factory(tmp_path):
    sequence = 0

    def build(content=None, mime="image/png", title="Imported creation"):
        nonlocal sequence
        sequence += 1
        root = tmp_path / f"source-{sequence}"
        root.mkdir()
        image = content if content is not None else base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=")
        (root / "output.bin").write_bytes(image)
        archive = empty_archive(title)
        archive["artifacts"] = [{"id": "output", "uri": "output.bin", "mime_type": mime,
                                 "sha256": hashlib.sha256(image).hexdigest(), "size": len(image),
                                 "availability": "available", "producer_node_id": None, "producer_run_id": None}]
        archive["graph"]["nodes"] = [{"id": "creation", "kind": "tool", "label": "Captured result",
                                       "branch_id": "main", "provenance": {"mode": "recorded", "evidence": []},
                                       "execution": {"kind": "manual", "tool": "example", "version": None,
                                                     "inputs": {"input": {"kind": "artifact", "artifact_id": "output"}},
                                                     "params": {}, "outputs": []}}]
        raw = (dumps(archive, pretty=True) + "\n").encode("utf-8")
        (root / "aica.json").write_bytes(raw)
        path = tmp_path / f"transfer-{sequence}.zip"
        report = build_bundle(root, path)
        headers = {"Content-Type": CONTENT_TYPE, "Idempotency-Key": "sha256:" + report["bundle_sha256"],
                   "X-AiCA-Bundle-SHA256": report["bundle_sha256"]}
        return {"root": root, "path": path, "report": report, "headers": headers, "content": image,
                "archive": archive, "raw": raw}

    return build


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "platform", {"alice": TOKEN, "bob": OTHER_TOKEN})


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as session:
        yield session


def project_url(client):
    response = client.post("/api/projects", json={"title": "A captured project"})
    assert response.status_code == 201, response.text
    return "/api/projects/" + response.json()["project"]["id"]


def upload(client, url, bundle):
    return client.post(url + "/imports", headers=bundle["headers"], content=bundle["path"].read_bytes())
