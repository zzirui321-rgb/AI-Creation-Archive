import base64
import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from aica_mcp import importing
from aica_mcp.client import RecorderClient
from aica_mcp.files import ProjectLock, read_artifact
from aica_mcp.store import Recorder
from aica_mcp.transfer import build_json, read_portable_json
from aica_schema.protocol import EMBEDDED_EXTENSION, ProtocolError, digest, dumps, loads, new_id


@pytest.fixture
def portable(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    recorder = Recorder(root)
    recorder.close()
    fixture = Path(__file__).resolve().parents[2] / "schema/tests/fixtures/branching.aica.json"
    document = loads(fixture.read_text())
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
    sha256 = hashlib.sha256(image).hexdigest()
    document["artifacts"] = [{"id": "picture", "uri": "objects/" + sha256, "sha256": sha256,
                              "mime_type": "image/png", "size": len(image), "availability": "unverified",
                              "producer_node_id": "character", "producer_run_id": None}]
    document["artifacts"].append({**document["artifacts"][0], "id": "same-image-other-step", "producer_node_id": "panel"})
    document["graph"]["nodes"][1]["execution"]["model"] = "declared-fixture-model"
    document["extensions"][EMBEDDED_EXTENSION] = {"version": "0.1", "encoding": "base64",
                                                 "objects": {sha256: base64.b64encode(image).decode()}}
    path = root / "export.json"
    path.write_text(dumps(document), encoding="utf-8")
    arguments = {"request_id": new_id(), "path": path.name, "target": "restored", "confirm_import": True}
    return root, document, image, arguments


def call_import(root, arguments):
    client = RecorderClient(root, autostart=False)
    result = client.call("aica_import_archive", arguments)
    return result


def test_import_preserves_contract_identity_and_restores_deduplicated_images(portable):
    root, document, image, arguments = portable
    snapshot = (root / "aica.json").read_bytes()
    database = (root / ".aica/recording.sqlite3").read_bytes()
    result = call_import(root, arguments)
    target = root / result["project"]
    restored = loads((target / "aica.json").read_text())
    assert result["status"] == "success" and result["object_count"] == 1 and result["artifact_count"] == 2
    assert not result["executed"] and not result["recording_started_by_import"]
    assert not result["recording_database_present"] and result["requires_new_recording_consent"]
    assert not (target / ".aica/recording.sqlite3").exists()
    for name in ("metadata", "graph", "variables", "interactive_questions", "runs", "sessions", "agents", "harnesses", "history", "snapshot"):
        assert restored[name] == document[name]
    assert EMBEDDED_EXTENSION not in restored["extensions"]
    assert len(list((target / ".aica/artifacts").iterdir())) == 1
    for original, artifact in zip(document["artifacts"], restored["artifacts"]):
        assert artifact["id"] == original["id"] and artifact["uri"] == original["uri"]
        assert artifact["availability"] == original["availability"]
        assert read_artifact(target, artifact) == image
    assert (root / "aica.json").read_bytes() == snapshot
    assert (root / ".aica/recording.sqlite3").read_bytes() == database
    exported = root / "roundtrip.json"
    build_json(target, exported)
    returned, objects, raw = read_portable_json(exported)
    assert objects[document["artifacts"][0]["sha256"]] == image and raw
    assert returned["graph"] == document["graph"]


def test_import_retry_does_not_overwrite_valid_subsequent_work(portable):
    root, document, image, arguments = portable
    call_import(root, arguments)
    path = root / "restored/aica.json"
    continued = loads(path.read_text())
    continued["metadata"]["title"] = "Continued local work"
    path.write_text(dumps(continued))
    current = path.read_bytes()
    retry = call_import(root, arguments)
    assert retry["already_imported"]
    assert path.read_bytes() == current
    changed = {**arguments, "request_id": new_id()}
    with pytest.raises(ProtocolError, match="different content"):
        call_import(root, changed)
    document["metadata"]["title"] = "Different source"
    (root / arguments["path"]).write_text(dumps(document))
    with pytest.raises(ProtocolError, match="different content"):
        call_import(root, arguments)
    assert path.read_bytes() == current


@pytest.mark.parametrize("phase", ["marker", "artifact", "archive", "completion"])
def test_interrupted_import_resumes_without_publishing_partial_archives(portable, monkeypatch, phase):
    root, document, image, arguments = portable
    original = importing.atomic_write
    failed = False

    def interrupted(path, content):
        nonlocal failed
        selected = ((phase == "marker" and path.name == "local-import.json")
                    or (phase == "artifact" and path.parent.name == "artifacts")
                    or (phase == "archive" and path.name == "aica.json")
                    or (phase == "completion" and path.name == "local-import.json" and b'"complete"' in content))
        if selected and not failed:
            failed = True
            raise OSError("Injected write interruption")
        original(path, content)

    monkeypatch.setattr(importing, "atomic_write", interrupted)
    with pytest.raises(ProtocolError, match="interrupted"):
        call_import(root, arguments)
    marker = root / "restored/.aica/imports/local-import.json"
    if phase == "marker":
        assert not (root / "restored").exists()
    else:
        assert loads(marker.read_text())["state"] == "preparing"
    assert (root / "restored/aica.json").exists() == (phase == "completion")
    recovered = call_import(root, arguments)
    assert recovered["status"] == "success"
    assert loads(marker.read_text())["state"] == "complete"


@pytest.mark.parametrize("target", [".", "../escape", "/tmp/escape", ".aica/new", ".git/new", ".AICA/new", ".GIT/new", "nested/../escape", "C:\\escape", "bad:name", "CON", "nul.txt", "com1/data"])
def test_import_rejects_unsafe_targets_before_creating_output(portable, target):
    root, document, image, arguments = portable
    with pytest.raises(ProtocolError):
        call_import(root, {**arguments, "target": target})
    assert not (root / ".aica/outboxes/archive-imports").exists()


def test_import_supports_existing_nested_parent_but_rejects_any_existing_target(portable):
    root, document, image, arguments = portable
    (root / "existing").mkdir()
    with pytest.raises(ProtocolError, match="already exists"):
        call_import(root, {**arguments, "target": "existing"})
    result = call_import(root, {**arguments, "target": "existing/new-project"})
    assert (root / result["archive_path"]).is_file()


def test_import_never_follows_target_or_private_state_symlinks(portable, tmp_path):
    root, document, image, arguments = portable
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProtocolError, match="symlink"):
        call_import(root, {**arguments, "target": "linked/child"})
    target = root / "restored"
    target.mkdir()
    (target / ".aica").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProtocolError, match="symlink"):
        call_import(root, arguments)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("damage", ["hash", "missing", "schema", "cycle"])
def test_bad_archive_is_rejected_before_creating_a_target(portable, damage):
    root, document, image, arguments = portable
    if damage == "hash":
        document["extensions"][EMBEDDED_EXTENSION]["objects"][document["artifacts"][0]["sha256"]] = base64.b64encode(b"wrong").decode()
    elif damage == "missing":
        document["extensions"].pop(EMBEDDED_EXTENSION)
    elif damage == "schema":
        document["aica_version"] = "9.0"
    else:
        document["graph"]["edges"].append({"id": "cycle", "source": "panel", "target": "story", "kind": "control"})
    (root / arguments["path"]).write_text(dumps(document))
    with pytest.raises(ProtocolError):
        call_import(root, arguments)
    assert not (root / "restored").exists()
    assert not (root / ".aica/outboxes/archive-imports").exists()


def test_retries_reject_corrupted_objects_and_changed_unfinished_archive(portable):
    root, document, image, arguments = portable
    call_import(root, arguments)
    artifact = root / "restored/.aica/artifacts" / document["artifacts"][0]["sha256"]
    artifact.write_bytes(b"modified")
    with pytest.raises(ProtocolError, match="different content"):
        call_import(root, arguments)
    assert artifact.read_bytes() == b"modified"
    artifact.write_bytes(image)
    marker = root / "restored/.aica/imports/local-import.json"
    state = loads(marker.read_text())
    state["state"] = "preparing"
    marker.write_text(dumps(state))
    archive = root / "restored/aica.json"
    continued = loads(archive.read_text())
    continued["metadata"]["title"] = "Changed during unfinished import"
    archive.write_text(dumps(continued))
    current = archive.read_bytes()
    with pytest.raises(ProtocolError, match="changed during import"):
        call_import(root, arguments)
    assert archive.read_bytes() == current


def test_import_requires_approval_and_selected_nonprivate_source(portable):
    root, document, image, arguments = portable
    for changed in ({**arguments, "confirm_import": False}, {**arguments, "path": "../outside.json"},
                    {**arguments, "path": ".aica/recording.sqlite3"}):
        with pytest.raises(ProtocolError):
            call_import(root, changed)
    assert not (root / "restored").exists()


def test_import_serializes_competing_requests(portable):
    root, document, image, arguments = portable
    call_import(root, arguments)
    lock = ProjectLock(root / ".aica/outboxes/archive-imports" / (digest("restored") + ".lock"))
    lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call_import, root, arguments)
            with pytest.raises(ProtocolError, match="already running"):
                future.result()
    finally:
        lock.close()


def test_cli_import_is_idempotent_and_does_not_start_recording(portable):
    root, document, image, arguments = portable
    command = [sys.executable, "-m", "aica_mcp", "import", "--project", str(root), "--path", "export.json",
               "--target", "cli-restored", "--confirm-import"]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert not loads(first.stdout)["recording_started_by_import"]
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert loads(second.stdout)["already_imported"]
    denied = subprocess.run(command[:-1], capture_output=True, text=True)
    assert denied.returncode == 1 and "--confirm-import" in denied.stderr
