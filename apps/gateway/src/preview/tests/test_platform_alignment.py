import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from preview import PreviewSettings
from preview.integration_server import create_app
from preview.scripts.run_unified import attach_transfer
from test_c_integration import TOKEN, upload, wait_job

ROOT = Path(__file__).resolve().parents[5]


def export(paths, destination):
    for path in paths:
        shutil.copytree(ROOT / path, destination / path, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", "dist"))


def test_generated_runs_and_bytes_match_bd_platform_contract(tmp_path, monkeypatch):
    from preview.scripts.generic_cases import cases

    monkeypatch.setenv("AICA_API_TOKENS", json.dumps({"tester": TOKEN}))
    app = create_app(tmp_path / "runtime", {"tester": TOKEN}, PreviewSettings(provider="local_fallback"))
    attach_transfer(app)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        prefix = upload(client, cases()["self-archive"])
        snapshot = wait_job(client, prefix, "draft")
        response = client.get(prefix + "/transfer", params={"archive_id": snapshot["archive_id"]})
        assert response.status_code == 200
        bundle = tmp_path / "generated.aica.zip"
        bundle.write_bytes(response.content)
    paths = ["apps/gateway/src", "packages/schema/src", "packages/mcp-adapters/src", "packages/runtime/src"]
    checkout = tmp_path / "bd-sources"
    export(paths, checkout)
    inherited = {
        "SYSTEMROOT",
        "WINDIR",
        "PATH",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "COMSPEC",
        "PATHEXT",
        "AICA_TEST_SQLITE_DLL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in inherited}
    environment.update(PYTHONPATH=os.pathsep.join(str(checkout / path) for path in paths), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "apps/gateway/src/preview/scripts/verify_platform_contract.py"),
            "--bundle",
            str(bundle),
            "--data-dir",
            str(tmp_path / "bd-data"),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["archive_valid"] and report["repository_commit_verified"]
    assert report["private_artifact_access_checked"] and report["semantic_input_prepared"]
    assert report["artifact_count"] == len(snapshot["archive"]["artifacts"])
    assert report["run_count"] == len(snapshot["archive"]["runs"])
    assert report["model_calls"] == 0
    assert report["native_replay_available"] is False


def test_bundled_execution_sources_prepare_without_private_git_objects(tmp_path, monkeypatch):
    from preview.scripts.prepare_runtime import prepare

    def unexpected_git(*args, **kwargs):
        raise AssertionError("Bundled source preparation must not require upstream Git objects")

    monkeypatch.setattr(subprocess, "check_output", unexpected_git)
    monkeypatch.setattr(subprocess, "run", unexpected_git)
    destination = tmp_path / "worker"
    prepare(ROOT, destination)
    assert (destination / "packages/runtime/src/aica_runtime/engine.py").is_file()
    assert (destination / "apps/gateway/src/aica_gateway/app.py").is_file()
    marker = (destination / ".aica-runtime-source").read_bytes()
    prepare(ROOT, destination)
    assert (destination / ".aica-runtime-source").read_bytes() == marker
