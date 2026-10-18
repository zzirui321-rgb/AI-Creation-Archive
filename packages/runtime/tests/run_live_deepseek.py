import argparse
import json
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from aica_gateway.app import create_app
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_schema.protocol import dumps, new_id
from fastapi.testclient import TestClient
from semantic_fixture import sample_archive


def main():
    parser = argparse.ArgumentParser(description="One real DeepSeek call using synthetic A facts only")
    parser.add_argument("--confirm-external", action="store_true", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    output = Path(arguments.output)
    if output.exists():
        raise SystemExit("Output exists; it was preserved. No model call made.")
    archive, raw = sample_archive()
    with tempfile.TemporaryDirectory(prefix="aica-semantic-live-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "trace.jsonl").write_bytes(raw)
        (source / "aica.json").write_text(dumps(archive), encoding="utf-8")
        bundle = root / "fixture.zip"
        transfer = build_bundle(source, bundle)
        token = secrets.token_urlsafe(32)
        app = create_app(root / "platform", {"synthetic-user": token})
        with TestClient(app, headers={"Authorization": "Bearer " + token}) as client:
            project = client.post("/api/projects", json={"title": "Synthetic semantic verification"})
            project_id = project.json()["project"]["id"]
            url = f"/api/projects/{project_id}"
            imported = client.post(url + "/imports", content=bundle.read_bytes(), headers={
                "Content-Type": CONTENT_TYPE, "Idempotency-Key": "sha256:" + transfer["bundle_sha256"],
                "X-AiCA-Bundle-SHA256": transfer["bundle_sha256"]})
            assert imported.status_code == 201
            import_id = imported.json()["import_id"]
            prepared = client.get(url + "/analysis-input", params={"import_id": import_id}).json()["input"]
            payload = {"request_id": new_id(), "import_id": import_id, "input_sha256": prepared["input_sha256"],
                       "allow_external_processing": True}
            analyzed = client.post(url + "/analyses", json=payload)
            if analyzed.status_code != 201:
                error = analyzed.json()
                print(json.dumps({"status": "error", "http_status": analyzed.status_code,
                                  "code": error.get("code"), "message": error.get("message")}, ensure_ascii=False))
                raise SystemExit(1)
            analysis = analyzed.json()["analysis"]
            assert client.get(url + "/analyses/" + analysis["id"]).json()["analysis"] == analysis
            assert client.post(url + "/analyses", json=payload).json()["analysis"] == analysis
            assert client.get(url + "/archive").json()["archive"] == archive
            report = analysis["result"]
            assert report["requires_review"] and report["executable"] is False
            assert report["analysis"]["steps"]
    evidence = {"mode": "real_model_on_synthetic_facts", "actual_creation_tools_executed": False,
                "tested_at": datetime.now(timezone.utc).isoformat(), "report": report}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(dumps(evidence, pretty=True) + "\n")
    print(json.dumps({"status": "success", "model": report["engine"]["model"], "usage": report["usage"],
                      "steps": len(report["analysis"]["steps"]), "dependencies": len(report["analysis"]["dependencies"]),
                      "variables": len(report["analysis"]["variables"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
