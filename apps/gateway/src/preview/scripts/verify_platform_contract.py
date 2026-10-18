"""Verify a B-generated transfer against the actual selected A/C/D platform."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path


def verify(bundle_path, data_dir):
    if os.name == "nt" and os.environ.get("AICA_TEST_SQLITE_DLL"):
        ctypes.WinDLL(os.environ["AICA_TEST_SQLITE_DLL"])
    from aica_gateway.app import create_app
    from aica_mcp.transfer import CONTENT_TYPE, verify_bundle
    from aica_schema.protocol import require_archive
    from fastapi.testclient import TestClient

    report = verify_bundle(bundle_path)
    content = bundle_path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    assert report["bundle_sha256"] == digest
    app = create_app(data_dir, tokens={})

    def checked(response, expected=200):
        assert response.status_code == expected, response.text
        return response.json()

    with TestClient(app) as client:
        password = "synthetic-contract-test-password"
        checked(
            client.post(
                "/api/auth/register",
                json={"username": "b-contract", "password": password, "name": "Synthetic B contract test"},
            ),
            201,
        )
        account = checked(client.post("/api/auth/login", json={"username": "b-contract", "password": password}))
        client.headers["Authorization"] = "Bearer " + account["access_token"]
        project = checked(client.post("/api/projects", json={"title": "B generated Archive contract test"}), 201)[
            "project"
        ]
        prefix = "/api/projects/" + project["id"]
        receipt = checked(
            client.post(
                prefix + "/imports",
                content=content,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "X-AiCA-Bundle-SHA256": digest,
                    "Idempotency-Key": "sha256:" + digest,
                },
            ),
            201,
        )
        snapshot = checked(client.get(prefix + "/archive", params={"import_id": receipt["import_id"]}))
        archive = snapshot["archive"]
        require_archive(archive)
        assert archive["runs"] and any(item["mime_type"] == "image/png" for item in archive["artifacts"])
        assert snapshot["archive_sha256"] == report["archive_sha256"]
        repository = checked(
            client.post("/api/repos", json={"name": "b-contract", "title": "B generated result"}), 201
        )["repository"]
        repository_prefix = "/api/repos/" + repository["id"]
        commit = checked(
            client.post(
                repository_prefix + "/archive-commits",
                json={
                    "expected_head": None,
                    "message": "Synthetic B archive handoff",
                    "project_id": project["id"],
                    "import_id": receipt["import_id"],
                },
            ),
            201,
        )["commit"]
        document = checked(client.get(repository_prefix + "/graph", params={"commit_id": commit["id"]}))["document"]
        assert document == archive
        for artifact in archive["artifacts"]:
            response = client.get(
                repository_prefix + "/artifacts/" + artifact["id"], params={"commit_id": commit["id"]}
            )
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        first_node = archive["graph"]["nodes"][0]["id"]
        prepared = checked(
            client.post(
                prefix + "/semantic-parse-input", json={"import_id": receipt["import_id"], "node_ids": [first_node]}
            )
        )["input"]
        assert prepared["facts"] and prepared["source_node_ids"] == [first_node]
        client.headers.pop("Authorization")
        assert client.get(repository_prefix + "/graph").status_code in {401, 404}
        assert client.get(
            repository_prefix + "/artifacts/" + archive["artifacts"][0]["id"], params={"commit_id": commit["id"]}
        ).status_code in {401, 404}
        paths = app.openapi()["paths"]
        native_replay = "/api/projects/{project_id}/executions" in paths
    return {
        "archive_valid": True,
        "artifact_count": len(archive["artifacts"]),
        "run_count": len(archive["runs"]),
        "repository_commit_verified": True,
        "private_artifact_access_checked": True,
        "semantic_input_prepared": True,
        "model_calls": 0,
        "native_replay_available": native_replay,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.bundle, arguments.data_dir)))
