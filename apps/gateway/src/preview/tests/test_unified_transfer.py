import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from preview import PreviewSettings
from preview.integration_server import create_app
from preview.scripts.run_unified import attach_transfer
from test_c_integration import TOKEN, upload, wait_job


def test_runtime_transfer_preserves_archive_and_all_verified_bytes(tmp_path, monkeypatch):
    from preview.scripts.generic_cases import cases

    monkeypatch.setenv(
        "AICA_API_TOKENS", json.dumps({"tester": TOKEN, "other": "another-authenticated-user-token-123456"})
    )
    app = create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback"))
    attach_transfer(app)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        url = upload(client, cases()["self-archive"])
        snapshot = wait_job(client, url, "draft")
        endpoint = url + "/transfer?archive_id=" + snapshot["archive_id"]
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == response.headers["X-AiCA-SHA256"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            raw = bundle.read("aica.json")
            assert hashlib.sha256(raw).hexdigest() == manifest["archive"]["sha256"]
            assert json.loads(raw) == snapshot["archive"]
            assert len(manifest["artifacts"]) == len(snapshot["archive"]["artifacts"])
            for record in manifest["artifacts"]:
                data = bundle.read(record["path"])
                assert hashlib.sha256(data).hexdigest() == record["sha256"]
                assert len(data) == record["size"]
        for header in ("", TOKEN, "Bearer wrong-token", "Bearer another-authenticated-user-token-123456"):
            assert client.get(endpoint, headers={"Authorization": header}).status_code in {401, 403, 404}
        item = snapshot["archive"]["artifacts"][0]
        _, path = app.state.store.artifact("tester", url.split("/")[-1], item["id"])
        path.write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="hash|corrupt"):
            app.state.store.read_blob(url.split("/")[-1], item["sha256"])
