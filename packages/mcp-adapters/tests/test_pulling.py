import copy
import hashlib
from contextlib import AsyncExitStack
from types import SimpleNamespace

import httpx
import pytest
from aica_mcp.consent import AgentConsent, enable_turn_policy
from aica_mcp.coordinator import Coordinator
from aica_mcp.files import read_artifact
from aica_mcp.pulling import download_archive
from aica_mcp.store import Recorder
from aica_mcp.transfer import build_bundle
from aica_schema.protocol import ProtocolError, dumps, loads, new_id
from aica_schema.recording import require_arguments
from mcp import types
from test_importing import portable as portable
from test_turns import begin, native_connection
from test_turns import running as running


def source():
    return {"server_url": "https://platform.example", "repo_id": new_id(), "commit_id": new_id()}


def configure(root):
    config = {"version": 1, "server_url": "https://platform.example", "project_id": new_id(),
              "token_env": "UPLOAD_TOKEN", "auto_upload": False, "interval_seconds": 5, "allow_loopback_http": False}
    (root / ".aicaconfig").write_text(dumps(config))


@pytest.fixture
def pull_case(portable, monkeypatch):
    root, document, image, arguments = portable
    enable_turn_policy(root)
    coordinator = Coordinator(root)
    configure(root)
    document["metadata"]["project_id"] = coordinator.recorder.document["metadata"]["project_id"]
    document["metadata"]["title"] = "Remote version"
    bridge = coordinator.turns.attach(owner_pid=100)["bridge_id"]
    remote = source()
    body = dumps(document).encode()
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + "D" * 48
        assert request.url.path == f"/api/repos/{remote['repo_id']}/archive"
        assert request.url.params["commit_id"] == remote["commit_id"]
        return httpx.Response(200, content=body)

    monkeypatch.setenv("AICA_DOWNLOAD_TOKEN", "D" * 48)
    coordinator.pulls.transport = httpx.MockTransport(serve)
    arguments = {**remote, "request_id": new_id(),
                 "expected_snapshot_id": coordinator.recorder.document["snapshot"]["snapshot_id"]}
    yield coordinator, bridge, arguments, document, image, requests
    coordinator.recorder.close()


def test_pull_backs_up_restores_and_remains_readable_after_restart(pull_case):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    recorder = coordinator.recorder
    before = copy.deepcopy(recorder.document)
    before_stream = recorder.stream_id
    prepared = coordinator.control_dispatch("pull_prepare", {"bridge_id": bridge, **arguments})
    assert recorder.document == before
    result = coordinator.control_dispatch("pull_apply", {"bridge_id": bridge, "prepared_id": prepared["prepared_id"]})
    assert result["pulled"] and not result["recording"] and not result["auto_upload"] and not result["executed"]
    assert recorder.document["graph"] == remote["graph"]
    assert recorder.stream_id != before_stream
    assert recorder.document["snapshot"]["commit_seq"] == 0
    backup = recorder.project / result["backup_path"]
    assert (backup / "recording.sqlite3").is_file()
    assert loads((backup / "aica.json").read_text())["metadata"]["title"] == before["metadata"]["title"]
    for artifact in recorder.document["artifacts"]:
        assert read_artifact(recorder.project, artifact) == image
    retry = coordinator.control_dispatch("pull_prepare", {"bridge_id": bridge, **arguments})
    assert retry["completed"] and retry["result"]["already_pulled"] and len(requests) == 1
    root = recorder.project
    recorder.close()
    recovered = Recorder(root)
    coordinator.recorder = recovered
    assert recovered.document["graph"] == remote["graph"] and recovered.snapshot_error is None
    assert not recovered.connection.execute("SELECT * FROM sessions").fetchall()
    assert recovered.document["metadata"]["title"] == "Remote version"


@pytest.mark.parametrize("boundary", ["changed", "other_recorder", "expired", "wrong_bridge", "upload_busy"])
def test_pull_rejects_races_before_replacing_project(pull_case, boundary):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    prepared = coordinator.pulls.prepare(bridge, **arguments)
    backup = coordinator.recorder.project / ".aica/backups"
    apply_bridge = bridge
    if boundary == "changed":
        coordinator.recorder.document["snapshot"]["snapshot_id"] = new_id()
    elif boundary == "other_recorder":
        other = coordinator.turns.attach()["bridge_id"]
        begin(coordinator.turns, host="other", bridge=other)
    elif boundary == "expired":
        coordinator.pulls.prepared[prepared["prepared_id"]]["created"] -= 301
    elif boundary == "wrong_bridge":
        apply_bridge = coordinator.turns.attach()["bridge_id"]
    else:
        coordinator.platform.upload_lock.acquire()
    before = copy.deepcopy(coordinator.recorder.document)
    try:
        with pytest.raises(ProtocolError):
            coordinator.pulls.apply(apply_bridge, prepared["prepared_id"])
    finally:
        if boundary == "upload_busy":
            coordinator.platform.upload_lock.release()
    assert coordinator.recorder.document == before and not backup.exists()


@pytest.mark.parametrize("choice", ["Yes", "No"])
async def test_pull_requires_separate_confirmation(pull_case, monkeypatch, choice):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    consent = AgentConsent(coordinator.recorder.project)
    consent.bridge_id = bridge

    async def control(name, arguments=None, timeout=5):
        result = coordinator.control_dispatch(name, arguments or {})
        assert result["status"] == "success"
        return result

    async def ask(message, schema, related_request_id):
        assert "覆盖当前项目" in message and "未上传修改" in message and "备份" in message
        assert coordinator.recorder.document["metadata"]["title"] != "Remote version"
        return types.ElicitResult(action="accept", content={"choice": choice})

    monkeypatch.setattr(consent, "control", control)
    context = SimpleNamespace(session=SimpleNamespace(elicit_form=ask), request_id=1)
    before = copy.deepcopy(coordinator.recorder.document)
    result = await consent.pull(context, **arguments)
    assert result["pulled"] is (choice == "Yes")
    assert not coordinator.pulls.prepared
    if choice == "No":
        assert coordinator.recorder.document == before
        assert not (coordinator.recorder.directory / "backups").exists()


@pytest.mark.parametrize("failure", ["redirect", "forbidden", "corrupt", "missing_objects", "hash", "transport"])
def test_invalid_download_never_changes_local_archive(portable, monkeypatch, failure):
    root, document, image, arguments = portable
    before = (root / "aica.json").read_bytes()
    monkeypatch.setenv("AICA_DOWNLOAD_TOKEN", "D" * 48)
    remote = source()

    def serve(request):
        if failure == "transport":
            raise httpx.ConnectError("Offline")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://elsewhere.example"})
        if failure == "forbidden":
            return httpx.Response(403)
        if failure == "corrupt":
            return httpx.Response(200, content=b"invalid json")
        changed = copy.deepcopy(document)
        if failure == "missing_objects":
            changed["extensions"] = {}
        return httpx.Response(200, content=dumps(changed).encode())

    if failure == "hash":
        remote["expected_sha256"] = "0" * 64
    with pytest.raises((ProtocolError, ValueError)):
        download_archive(**remote, transport=httpx.MockTransport(serve))
    assert (root / "aica.json").read_bytes() == before


def test_zip_download_checks_all_objects(portable, monkeypatch):
    root, document, image, arguments = portable
    bundle = root / "remote.zip"
    artifact_path = root / document["artifacts"][0]["uri"]
    artifact_path.parent.mkdir()
    artifact_path.write_bytes(image)
    build_bundle(root, bundle, archive_bytes=dumps(document).encode())
    content = bundle.read_bytes()
    monkeypatch.setenv("AICA_DOWNLOAD_TOKEN", "D" * 48)
    result = download_archive(**source(), expected_sha256=hashlib.sha256(content).hexdigest(),
                              transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)))
    assert list(result["objects"].values()) == [image]


def test_pull_contract_does_not_accept_credentials_or_arbitrary_urls():
    arguments = {**source(), "request_id": new_id(), "expected_snapshot_id": new_id()}
    require_arguments("aica_pull_platform", arguments)
    for changed in ({"token": "SECRET"}, {"repo_id": "../../secret"}, {"commit_id": "latest"}):
        with pytest.raises(ProtocolError):
            require_arguments("aica_pull_platform", {**arguments, **changed})


def test_pull_pauses_upload_configuration_and_invalidates_current_grant(pull_case):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    root = coordinator.recorder.project
    config = {"version": 1, "server_url": "https://platform.example", "project_id": new_id(),
              "token_env": "UPLOAD_TOKEN", "auto_upload": True, "interval_seconds": 5, "allow_loopback_http": False}
    (root / ".aicaconfig").write_text(dumps(config))
    begin(coordinator.turns, bridge=bridge)
    arguments["expected_snapshot_id"] = coordinator.recorder.document["snapshot"]["snapshot_id"]
    prepared = coordinator.pulls.prepare(bridge, **arguments)
    result = coordinator.pulls.apply(bridge, prepared["prepared_id"])
    assert loads((root / ".aicaconfig").read_text())["auto_upload"] is False
    assert loads((root / result["backup_path"] / "aicaconfig.json").read_text()) == config
    assert not coordinator.recorder.connection.execute("SELECT * FROM sessions").fetchall()
    assert coordinator.turns.active(bridge)["turn"] is None


def test_bad_download_hash_and_credentials_leave_no_backup(pull_case, monkeypatch):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    monkeypatch.delenv("AICA_DOWNLOAD_TOKEN")
    before = copy.deepcopy(coordinator.recorder.document)
    with pytest.raises(ProtocolError) as error:
        coordinator.pulls.prepare(bridge, **arguments)
    assert error.value.code == "credential_missing" and not requests
    assert coordinator.recorder.document == before
    assert not (coordinator.recorder.directory / "backups").exists()


def test_pull_never_sends_credentials_to_another_origin(pull_case):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    with pytest.raises(ProtocolError) as error:
        coordinator.pulls.prepare(bridge, **{**arguments, "server_url": "https://elsewhere.example"})
    assert error.value.code == "unsafe_destination" and not requests


def test_pull_rejects_another_project_before_any_replacement(pull_case, monkeypatch):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    remote["metadata"]["project_id"] = new_id()
    coordinator.pulls.transport = httpx.MockTransport(lambda request: httpx.Response(200, content=dumps(remote).encode()))
    before = copy.deepcopy(coordinator.recorder.document)
    with pytest.raises(ProtocolError) as error:
        coordinator.pulls.prepare(bridge, **arguments)
    assert error.value.code == "project_mismatch" and coordinator.recorder.document == before
    assert not (coordinator.recorder.directory / "backups").exists()


def test_snapshot_write_failure_recovers_committed_pull_without_losing_backup(pull_case, monkeypatch):
    import aica_mcp.store as store

    coordinator, bridge, arguments, remote, image, requests = pull_case
    prepared = coordinator.pulls.prepare(bridge, **arguments)
    original = store.atomic_write

    def fail_after_commit(path, data):
        if path == coordinator.recorder.project / "aica.json" and coordinator.recorder.document["metadata"]["title"] == "Remote version":
            raise OSError("Disk temporarily unavailable")
        original(path, data)

    monkeypatch.setattr(store, "atomic_write", fail_after_commit)
    with pytest.raises(ProtocolError) as error:
        coordinator.pulls.apply(bridge, prepared["prepared_id"])
    assert error.value.code == "pull_recovery_required"
    root = coordinator.recorder.project
    assert len(list((root / ".aica/backups").glob("*/recording.sqlite3"))) == 1
    monkeypatch.setattr(store, "atomic_write", original)
    retry = coordinator.pulls.prepare(bridge, **arguments)
    assert retry["completed"] and retry["result"]["already_pulled"]
    assert not coordinator.recorder.snapshot_error
    assert loads((root / "aica.json").read_text())["graph"] == remote["graph"]


def test_ordinary_session_cannot_bypass_pull_confirmation(pull_case):
    coordinator, bridge, arguments, remote, image, requests = pull_case
    with pytest.raises(ProtocolError) as error:
        coordinator.dispatch("aica_pull_platform", arguments)
    assert error.value.code == "turn_not_authorized" and not requests


@pytest.mark.parametrize("corrupt", [False, True])
def test_raw_platform_archive_fetches_pinned_objects_once(portable, monkeypatch, corrupt):
    root, document, image, arguments = portable
    document["extensions"] = {}
    remote = source()
    monkeypatch.setenv("AICA_DOWNLOAD_TOKEN", "D" * 48)
    calls = []

    def serve(request):
        assert request.url.params["commit_id"] == remote["commit_id"]
        calls.append(request.url.path)
        if request.url.path.endswith("/archive"):
            return httpx.Response(200, content=dumps(document).encode())
        assert request.url.path.endswith("/artifacts/picture")
        return httpx.Response(200, content=b"bad" if corrupt else image)

    if corrupt:
        with pytest.raises(ProtocolError) as error:
            download_archive(**remote, transport=httpx.MockTransport(serve))
        assert error.value.code == "transfer_mismatch"
    else:
        result = download_archive(**remote, transport=httpx.MockTransport(serve))
        assert list(result["objects"].values()) == [image]
    assert len(calls) == 2


@pytest.mark.parametrize("choice", ["Yes", "No"])
async def test_pull_over_real_stdio_mcp(running, portable, monkeypatch, choice):
    root, remote, image, arguments = portable
    remote["metadata"]["project_id"] = running.recorder.document["metadata"]["project_id"]
    monkeypatch.setenv("AICA_DOWNLOAD_TOKEN", "D" * 48)
    configure(running.recorder.project)
    running.pulls.transport = httpx.MockTransport(lambda request: httpx.Response(200, content=dumps(remote).encode()))
    arguments = {**source(), "request_id": new_id(),
                 "expected_snapshot_id": running.recorder.document["snapshot"]["snapshot_id"]}

    async def decide(context, params):
        assert "覆盖当前项目" in params.message
        return types.ElicitResult(action="accept", content={"choice": choice})

    async with AsyncExitStack() as stack:
        session = await native_connection(stack, running.recorder.project, decide)
        listing = await session.list_tools()
        assert "aica_pull_platform" in {tool.name for tool in listing.tools}
        result = await session.call_tool("aica_pull_platform", arguments)
        assert not result.is_error, result
        assert result.structured_content["pulled"] is (choice == "Yes")
        archive = await session.call_tool("aica_get_archive", {})
        assert not archive.is_error
        if choice == "Yes":
            assert archive.structured_content["archive"]["graph"] == remote["graph"]
            for artifact in archive.structured_content["archive"]["artifacts"]:
                assert read_artifact(running.recorder.project, artifact) == image
