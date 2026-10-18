import copy
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
from aica_mcp.client import RecorderClient
from aica_mcp.store import Recorder
from aica_mcp.transfer import build_bundle, build_json, read_portable_json, upload_bundle, verify_bundle
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, utc_now


@pytest.fixture
def recorded_project(tmp_path):
    project = tmp_path / "source"
    project.mkdir()
    image = b"\x89PNG\r\n\x1a\nfixture-image-bytes"
    (project / "picture.png").write_bytes(image)
    with_recorder = Recorder(project)
    try:
        credentials = with_recorder.start_session(new_id(), True)
        event = {"event_id": new_id(), "source_id": "fixture", "source_seq": 1,
                 "session_id": credentials["session_id"], "agent_id": None, "type": "artifact.recorded",
                 "timestamp": utc_now(), "causal_parents": [], "payload": {}}
        with_recorder.capture_artifact(credentials["session_token"], event, "picture.png", "image/png")
        exported = with_recorder.flush()
    finally:
        with_recorder.close()
    (project / ".aica" / "endpoint.json").write_text('{"token":"PRIVATE_ENDPOINT_TOKEN"}')
    (project / ".aica" / "self-archive-session.json").write_text('{"token":"PRIVATE_SESSION_TOKEN"}')
    return project, image, exported


@pytest.fixture
def transfer_bundle(recorded_project, tmp_path):
    project, image, exported = recorded_project
    path = tmp_path / "transfer.zip"
    report = build_bundle(project, path, exported["sha256"])
    return path, report


def rewrite_bundle(path, target, change):
    with zipfile.ZipFile(path) as bundle:
        members = {name: bundle.read(name) for name in bundle.namelist()}
    change(members)
    with zipfile.ZipFile(target, "w") as bundle:
        for name, content in members.items():
            bundle.writestr(name, content)


def receipt_for(request):
    payload = request.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        manifest = loads(bundle.read("manifest.json").decode())
    return {"status": "success", "format": "aica-transfer/0.1", "import_id": "import-1",
            "project_id": "server-project", "bundle_sha256": hashlib.sha256(payload).hexdigest(),
            "archive_sha256": manifest["archive"]["sha256"]}


def test_portable_bundle_preserves_snapshot_and_image_without_private_state(recorded_project, transfer_bundle, tmp_path):
    project, image, exported = recorded_project
    path, report = transfer_bundle
    with zipfile.ZipFile(path) as bundle:
        assert bundle.read("aica.json") == (project / "aica.json").read_bytes()
        image_hash = hashlib.sha256(image).hexdigest()
        assert bundle.read("objects/" + image_hash) == image
        assert all(name in {"manifest.json", "aica.json"} or name.startswith("objects/")
                   for name in bundle.namelist())
    assert report["artifact_count"] == 2
    assert report["archive_sha256"] == exported["sha256"]
    assert b"PRIVATE_ENDPOINT_TOKEN" not in path.read_bytes()
    assert b"PRIVATE_SESSION_TOKEN" not in path.read_bytes()
    remote = tmp_path / "another-machine.zip"
    remote.write_bytes(path.read_bytes())
    project.rename(tmp_path / "unavailable-original")
    assert verify_bundle(remote) == {key: value for key, value in report.items() if key != "path"}


def test_equal_bytes_preserve_distinct_artifact_ids_but_share_object(recorded_project, tmp_path):
    project, image, exported = recorded_project
    archive = loads((project / "aica.json").read_text())
    duplicate = copy.deepcopy(archive["artifacts"][0])
    duplicate["id"] = "distinct-instance"
    archive["artifacts"].append(duplicate)
    (project / "aica.json").write_text(dumps(archive))
    report = build_bundle(project, tmp_path / "deduplicated.zip")
    assert report["artifact_count"] == 3 and report["object_count"] == 2


@pytest.mark.parametrize("damage", ["missing", "tampered", "external", "unknown_hash", "private", "escape"])
def test_unavailable_or_unsafe_artifacts_never_produce_a_bundle(recorded_project, tmp_path, damage):
    project, image, exported = recorded_project
    archive = loads((project / "aica.json").read_text())
    artifact = archive["artifacts"][0]
    source = project / artifact["uri"]
    if damage == "missing":
        source.unlink()
    elif damage == "tampered":
        source.write_bytes(b"wrong image")
    elif damage == "external":
        artifact["uri"] = "https://example.invalid/picture.png"
    elif damage == "unknown_hash":
        artifact["sha256"] = None
    elif damage == "private":
        artifact["uri"] = ".aica/endpoint.json"
    else:
        external = tmp_path / "external.png"
        external.write_bytes(image)
        source.unlink()
        source.symlink_to(external)
    (project / "aica.json").write_text(dumps(archive))
    target = tmp_path / "rejected.zip"
    with pytest.raises(ProtocolError):
        build_bundle(project, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".aica-transfer-*"))


def test_build_rejects_concurrent_snapshot_change_and_does_not_replace_existing_file(recorded_project, tmp_path):
    project, image, exported = recorded_project
    target = tmp_path / "existing.zip"
    target.write_bytes(b"existing")
    with pytest.raises(ProtocolError, match="Snapshot changed"):
        build_bundle(project, target, "0" * 64)
    with pytest.raises(FileExistsError):
        build_bundle(project, target)
    assert target.read_bytes() == b"existing"
    with pytest.raises(ProtocolError, match="outside private"):
        build_bundle(project, project / ".aica" / "endpoint.json")


@pytest.mark.parametrize("damage", ["extra", "image", "archive", "mapping", "duplicate_id", "traversal"])
def test_corrupt_or_unlisted_bundle_content_is_rejected(transfer_bundle, tmp_path, damage):
    path, report = transfer_bundle

    def change(members):
        manifest = loads(members["manifest.json"].decode())
        if damage == "extra":
            members["endpoint.json"] = b"private"
        elif damage == "image":
            members[manifest["artifacts"][0]["path"]] = b"tampered"
        elif damage == "archive":
            members["aica.json"] += b" "
        elif damage == "mapping":
            manifest["artifacts"].pop()
        elif damage == "duplicate_id":
            manifest["artifacts"].append(manifest["artifacts"][0])
        else:
            manifest["artifacts"][0]["path"] = "../picture.png"
        members["manifest.json"] = dumps(manifest).encode()

    target = tmp_path / "corrupt.zip"
    rewrite_bundle(path, target, change)
    with pytest.raises(ProtocolError):
        verify_bundle(target)


def test_zip_duplicates_compression_symlinks_and_invalid_zip_are_rejected(transfer_bundle, tmp_path):
    path, report = transfer_bundle
    duplicate = tmp_path / "duplicate.zip"
    duplicate.write_bytes(path.read_bytes())
    with pytest.warns(UserWarning), zipfile.ZipFile(duplicate, "a") as bundle:
        bundle.writestr("manifest.json", b"{}")
    with pytest.raises(ProtocolError, match="Duplicate"):
        verify_bundle(duplicate)
    for mode in ("compressed", "symlink", "invalid"):
        target = tmp_path / f"{mode}.zip"
        if mode == "invalid":
            target.write_bytes(b"not a zip")
        else:
            with zipfile.ZipFile(path) as original, zipfile.ZipFile(target, "w") as changed:
                for name in original.namelist():
                    entry = zipfile.ZipInfo(name)
                    if mode == "symlink":
                        entry.external_attr = 0o120777 << 16
                    else:
                        entry.compress_type = zipfile.ZIP_DEFLATED
                    changed.writestr(entry, original.read(name))
        with pytest.raises(ProtocolError):
            verify_bundle(target)


def test_explicit_upload_streams_verified_bytes_and_checks_receipt(transfer_bundle):
    path, report = transfer_bundle
    calls = []

    def remote(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer platform-test-token"
        assert request.headers["idempotency-key"] == "sha256:" + report["bundle_sha256"]
        assert request.headers["content-length"] == str(report["size"])
        assert request.read() == path.read_bytes()
        return httpx.Response(201, json=receipt_for(request))

    transport = httpx.MockTransport(remote)
    for attempt in range(2):
        result = upload_bundle(path, "https://platform.invalid/imports", "platform-test-token", True, transport)
        assert result["status"] == "success" and result["receipt"]["project_id"] == "server-project"
        assert "platform-test-token" not in dumps(result)
    assert len(calls) == 2


@pytest.mark.parametrize("url,confirmed,token", [
    ("https://platform.invalid/imports", False, "token"),
    ("http://platform.invalid/imports", True, "token"),
    ("https://user:pass@platform.invalid/imports", True, "token"),
    ("https://platform.invalid/imports?token=private", True, "token"),
    ("https://platform.invalid/imports#fragment", True, "token"),
    ("https://platform.invalid/imports", True, None),
])
def test_invalid_upload_authorization_never_contacts_server(transfer_bundle, url, confirmed, token):
    path, report = transfer_bundle

    def forbidden(request):
        pytest.fail("Must not contact the server")

    with pytest.raises(ProtocolError):
        upload_bundle(path, url, token, confirmed, httpx.MockTransport(forbidden))


@pytest.mark.parametrize("failure", ["redirect", "unauthorized", "pending", "hash", "html", "oversize", "timeout"])
def test_unconfirmed_upload_never_reports_success_or_leaks_server_body(transfer_bundle, failure):
    path, report = transfer_bundle
    calls = []

    def remote(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("platform-test-token", request=request)
        if failure in {"redirect", "unauthorized", "pending"}:
            code = {"redirect": 307, "unauthorized": 401, "pending": 202}[failure]
            return httpx.Response(code, headers={"Location": "https://another.invalid/"}, text="private server text")
        if failure == "html":
            return httpx.Response(200, text="private server text")
        if failure == "oversize":
            return httpx.Response(200, content=b"x" * 65537)
        receipt = receipt_for(request)
        receipt["bundle_sha256"] = "0" * 64
        return httpx.Response(201, json=receipt)

    with pytest.raises(ProtocolError) as failure_info:
        upload_bundle(path, "https://platform.invalid/imports", "platform-test-token", True,
                      httpx.MockTransport(remote))
    assert len(calls) == 1
    assert "platform-test-token" not in str(failure_info.value)
    assert "private server text" not in str(failure_info.value)


def test_cli_offline_bundle_and_verification_do_not_start_coordinator(recorded_project, tmp_path):
    project, image, exported = recorded_project
    endpoint = (project / ".aica" / "endpoint.json").read_bytes()
    path = tmp_path / "cli.zip"
    for command in (["bundle", "--project", str(project), "--output", str(path), "--offline"],
                    ["verify-bundle", str(path)]):
        process = subprocess.run([sys.executable, "-m", "aica_mcp", *command], capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        assert loads(process.stdout)["status"] == "success"
    assert (project / ".aica" / "endpoint.json").read_bytes() == endpoint


def test_self_contained_json_survives_source_removal_and_excludes_private_state(recorded_project, tmp_path):
    project, image, exported = recorded_project
    path = tmp_path / "aica.json"
    report = build_json(project, path, exported["sha256"])
    project.rename(tmp_path / "unavailable-source")
    archive, objects, raw = read_portable_json(path)
    assert objects[hashlib.sha256(image).hexdigest()] == image
    assert report["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["artifact_count"] == len(archive["artifacts"]) == 2
    assert b"PRIVATE_ENDPOINT_TOKEN" not in raw and b"PRIVATE_SESSION_TOKEN" not in raw


def test_portable_json_does_not_overwrite_live_snapshot_or_accept_missing_files(recorded_project, tmp_path):
    project, image, exported = recorded_project
    snapshot = (project / "aica.json").read_bytes()
    with pytest.raises(ProtocolError, match="outside live"):
        build_json(project, project / "aica.json")
    with pytest.raises(ProtocolError, match="Snapshot changed"):
        build_json(project, tmp_path / "changed.json", "0" * 64)
    artifact = loads(snapshot.decode())["artifacts"][0]
    (project / artifact["uri"]).unlink()
    with pytest.raises(ProtocolError):
        build_json(project, tmp_path / "missing.json")
    assert not (tmp_path / "missing.json").exists()
    assert (project / "aica.json").read_bytes() == snapshot


def test_cli_portable_export_uses_the_project_coordinator_and_rejects_unsafe_output(recorded_project):
    project, image, exported = recorded_project
    root = Path(__file__).resolve().parents[3]
    sources = ("packages/schema/src", "packages/mcp-adapters/src")
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(str(root / path) for path in sources)}
    command = [sys.executable, "-m", "aica_mcp", "export", "--project", str(project), "--portable"]
    try:
        process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
        report = loads(process.stdout)
        archive, objects, raw = read_portable_json(project / "exports" / "aica.json")
        assert report["portable"] and objects[hashlib.sha256(image).hexdigest()] == image
        snapshot = (project / "aica.json").read_bytes()
        for output in ("aica.json", ".aica/endpoint.json", "../outside.json"):
            rejected = subprocess.run([*command, "--output", output], env=environment, capture_output=True, text=True, timeout=30)
            assert rejected.returncode == 1
        assert (project / "aica.json").read_bytes() == snapshot
    finally:
        client = RecorderClient(project, autostart=False)
        client.request("shutdown")
