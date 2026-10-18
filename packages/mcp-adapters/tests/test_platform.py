import asyncio
import base64
import hashlib
import io
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest
from aica_mcp.client import RecorderClient
from aica_mcp.platform import PlatformSync
from aica_mcp.store import Recorder
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, utc_now
from aica_schema.recording import require_arguments
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mock_platform import PROJECT_ID, MockPlatform

TOKEN = "local-platform-contract-token-0123456789"


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_UPLOAD_TOKEN", TOKEN)
    project = tmp_path / "project"
    project.mkdir()
    recorder = Recorder(project)
    recorder.start_session(new_id(), True)
    yield recorder
    recorder.close()


def receipt(request):
    body = request.read()
    with zipfile.ZipFile(io.BytesIO(body)) as bundle:
        manifest = loads(bundle.read("manifest.json").decode())
    return {"status": "success", "format": "aica-transfer/0.1", "import_id": "mock-import",
            "project_id": request.url.path.split("/")[3], "bundle_sha256": hashlib.sha256(body).hexdigest(),
            "archive_sha256": manifest["archive"]["sha256"]}


def add_message(recorder, text="observable user message"):
    credentials = recorder.start_session(new_id(), True)
    event = {"event_id": new_id(), "source_id": new_id(), "source_seq": 1,
             "session_id": credentials["session_id"], "agent_id": None, "type": "message.recorded",
             "timestamp": utc_now(), "causal_parents": [], "payload": {"role": "user", "content": text}}
    result = recorder.append(event=event, session_token=credentials["session_token"])
    assert result["status"] == "success"


def bind(sync, url="https://platform.invalid", project_id=PROJECT_ID):
    result = sync.bind(url, project_id, True, interval_seconds=1, allow_loopback_http=url.startswith("http://"))
    assert result["authorized"] and result["auto_upload"]


def test_binding_requires_consent_and_credentials_and_never_uploads_by_itself(recorder, monkeypatch):
    calls = []
    sync = PlatformSync(recorder, httpx.MockTransport(lambda request: calls.append(request)))
    sync.run_once()
    assert sync.status()["state"] == "unbound"
    with pytest.raises(ProtocolError):
        sync.bind("https://platform.invalid", PROJECT_ID, False)
    monkeypatch.delenv("AICA_UPLOAD_TOKEN")
    with pytest.raises(ProtocolError, match="environment"):
        bind(sync)
    assert not sync.config_path.exists() and not calls


def test_config_and_grant_contain_no_token_and_copying_config_does_not_authorize(recorder, tmp_path):
    sync = PlatformSync(recorder)
    bind(sync)
    assert TOKEN not in sync.config_path.read_text()
    grant = sync.directory(sync.read_config()) / "authorization.json"
    assert TOKEN not in grant.read_text()
    other = tmp_path / "other"
    other.mkdir()
    shutil.copy2(sync.config_path, other / ".aicaconfig")
    with_recorder = Recorder(other)
    try:
        other_sync = PlatformSync(with_recorder)
        other_sync.run_once()
        assert other_sync.status()["state"] == "authorization_required"
    finally:
        with_recorder.close()


@pytest.mark.parametrize("url", ["http://platform.invalid", "https://u:p@platform.invalid",
                                 "https://platform.invalid?token=secret", "https://platform.invalid/subpath",
                                 "https://platform.invalid#fragment", "https://platform.invalid\\bad"])
def test_unsafe_or_non_origin_destinations_are_rejected(recorder, url):
    sync = PlatformSync(recorder)
    with pytest.raises((ProtocolError, ValueError)):
        bind(sync, url)
    assert not sync.config_path.exists()


def test_private_network_http_is_allowed_with_explicit_flag(recorder):
    sync = PlatformSync(recorder)
    bind(sync, "http://10.19.51.6:8000")
    assert sync.read_config()["server_url"] == "http://10.19.51.6:8000"


def test_success_uploads_snapshot_once_and_next_change_creates_a_new_bundle(recorder):
    bodies = []

    def remote(request):
        bodies.append(request.read())
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return httpx.Response(201, json=receipt(request))

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    sync.run_once()
    assert sync.status()["state"] == "synced" and len(bodies) == 1
    assert TOKEN.encode() not in bodies[0]
    with zipfile.ZipFile(io.BytesIO(bodies[0])) as bundle:
        assert ".aicaconfig" not in bundle.namelist()
    sync.sync_now()
    sync.run_once()
    assert len(bodies) == 1
    add_message(recorder)
    assert sync.status()["state"] == "pending"
    sync.sync_now()
    sync.run_once()
    assert len(bodies) == 2 and bodies[0] != bodies[1]


def test_lost_ack_restart_reuses_original_bytes_even_after_new_records(recorder):
    bodies = []

    def remote(request):
        bodies.append(request.read())
        if len(bodies) == 1:
            raise httpx.ReadTimeout(TOKEN, request=request)
        return httpx.Response(200, json=receipt(request))

    transport = httpx.MockTransport(remote)
    first = PlatformSync(recorder, transport)
    bind(first)
    first.run_once()
    assert first.status()["pending"] and first.status()["state"] == "retry_wait"
    first.run_once()
    assert len(bodies) == 1
    add_message(recorder, "new content after the uncertain upload")
    second = PlatformSync(recorder, transport)
    second.sync_now()
    second.run_once()
    assert bodies[0] == bodies[1]
    assert second.status()["state"] == "pending"
    second.sync_now()
    second.run_once()
    assert len(bodies) == 3 and bodies[2] != bodies[1]
    assert second.status()["state"] == "synced"


def test_failed_uploads_stop_after_five_attempts_and_manual_retry_resets_budget(recorder):
    calls = []

    def remote(request):
        calls.append(request.read())
        return httpx.Response(401, text="private response " + TOKEN)

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    config = sync.read_config()
    for attempt in range(7):
        state = sync.read_state(config)
        state["next_attempt_at"] = 0
        sync.write_state(config, state)
        sync.run_once()
    assert len(calls) == 5 and sync.status()["state"] == "blocked"
    assert len(set(calls)) == 1
    assert TOKEN not in dumps(sync.status()) and "private response" not in dumps(sync.status())
    sync.sync_now()
    sync.run_once()
    assert len(calls) == 6 and sync.status()["failures"] == 1


def test_wrong_receipt_does_not_delete_pending_bundle(recorder):
    def remote(request):
        body = receipt(request)
        body["project_id"] = new_id()
        return httpx.Response(201, json=body)

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    sync.run_once()
    assert sync.status()["last_error"]["code"] == "invalid_receipt"
    assert sync.status()["pending"] and (sync.directory(sync.read_config()) / "pending.zip").exists()


def test_missing_registered_image_blocks_upload_before_network(recorder):
    image = recorder.project / "picture.png"
    image.write_bytes(b"fixture image bytes")
    credentials = recorder.start_session(new_id(), True)
    event = {"event_id": new_id(), "source_id": new_id(), "source_seq": 1,
             "session_id": credentials["session_id"], "agent_id": None, "type": "artifact.recorded",
             "timestamp": utc_now(), "causal_parents": [], "payload": {}}
    response = recorder.capture_artifact(credentials["session_token"], event, "picture.png", "image/png")
    artifact = response["artifact"]
    (recorder.project / artifact["uri"]).unlink()
    calls = []
    sync = PlatformSync(recorder, httpx.MockTransport(lambda request: calls.append(request)))
    bind(sync)
    sync.run_once()
    assert not calls and sync.status()["last_uploaded_seq"] == -1 and sync.status()["last_error"]


def test_orphaned_bundle_is_recovered_and_tampered_bundle_is_rejected(recorder):
    bodies = []

    def remote(request):
        bodies.append(request.read())
        raise httpx.ReadTimeout("lost acknowledgement", request=request)

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    sync.run_once()
    config = sync.read_config()
    state = sync.read_state(config)
    state.update({"pending": None, "failures": 0, "next_attempt_at": 0})
    sync.write_state(config, state)
    sync.run_once()
    assert bodies[0] == bodies[1]
    pending = sync.directory(config) / "pending.zip"
    pending.write_bytes(b"corrupt")
    sync.sync_now()
    sync.run_once()
    assert len(bodies) == 2 and sync.status()["last_error"]


def test_pause_is_persistent_and_cannot_be_undone_by_editing_destination(recorder):
    calls = []
    sync = PlatformSync(recorder, httpx.MockTransport(lambda request: calls.append(request)))
    bind(sync)
    sync.set_enabled(False)
    replacement = PlatformSync(recorder, sync.transport)
    replacement.run_once()
    assert not calls and replacement.status()["state"] == "paused"
    with pytest.raises(ProtocolError):
        replacement.set_enabled(True)
    config = sync.read_config()
    config["server_url"] = "https://another.invalid"
    config["auto_upload"] = True
    sync.config_path.write_text(dumps(config))
    replacement.run_once()
    assert not calls and replacement.status()["state"] == "authorization_required"


def test_missing_pending_bytes_are_not_rebuilt_after_an_uncertain_upload(recorder):
    calls = []

    def remote(request):
        calls.append(request.read())
        raise httpx.ReadTimeout("unknown outcome", request=request)

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    sync.run_once()
    (sync.directory(sync.read_config()) / "pending.zip").unlink()
    sync.sync_now()
    sync.run_once()
    assert len(calls) == 1 and sync.status()["last_error"]["code"] == "invalid_sync_state"


def test_symlink_configuration_cannot_replace_another_file(recorder, tmp_path):
    source = tmp_path / "private-file"
    source.write_text("private-original")
    sync = PlatformSync(recorder)
    sync.config_path.symlink_to(source)
    with pytest.raises(ProtocolError):
        bind(sync)
    assert source.read_text() == "private-original"


def test_rebinding_during_upload_keeps_old_receipt_out_of_the_new_target(recorder):
    entered, release = threading.Event(), threading.Event()
    seen_projects = []

    def remote(request):
        result = receipt(request)
        seen_projects.append(result["project_id"])
        entered.set()
        assert release.wait(5)
        return httpx.Response(201, json=result)

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    thread = threading.Thread(target=sync.run_once)
    thread.start()
    next_project = new_id()
    try:
        assert entered.wait(5)
        bind(sync, project_id=next_project)
        assert sync.status()["project_id"] == next_project and sync.status()["receipt"] is None
    finally:
        release.set()
        thread.join(timeout=5)
    assert sync.status()["receipt"] is None
    sync.run_once()
    assert seen_projects == [PROJECT_ID, next_project]
    assert sync.status()["receipt"]["project_id"] == next_project


def test_inflight_network_does_not_block_recording_and_pause_prevents_next_upload(recorder):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def remote(request):
        calls.append(request.read())
        entered.set()
        assert release.wait(5)
        return httpx.Response(201, json=receipt(request))

    sync = PlatformSync(recorder, httpx.MockTransport(remote))
    bind(sync)
    thread = threading.Thread(target=sync.run_once)
    thread.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        sync.set_enabled(False)
        add_message(recorder, "recording while upload is waiting")
        assert time.monotonic() - started < 1
        sync.run_once()
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(timeout=5)
    sync.run_once()
    assert sync.status()["state"] == "paused" and len(calls) == 1
    sync.set_enabled(True, True)
    sync.run_once()
    assert len(calls) == 2


@pytest.mark.parametrize("tool,args", [("aica_bind_platform", {"server_url": "https://platform.invalid",
                                  "project_id": PROJECT_ID, "confirm_upload": True, "token": TOKEN}),
                                      ("aica_resume_upload", {"confirm_upload": False})])
def test_shared_tool_contract_never_accepts_inline_credentials_or_implicit_resume(tool, args):
    with pytest.raises(ProtocolError):
        require_arguments(tool, args)


def test_old_coordinator_gets_an_explicit_upgrade_error_instead_of_an_unknown_rpc(recorder, monkeypatch):
    client = RecorderClient(recorder.project, autostart=False)
    calls = []

    def old_coordinator(name, arguments):
        calls.append(name)
        return {"status": "success"}

    monkeypatch.setattr(client, "send", old_coordinator)
    result = client.call("aica_platform_status")
    assert result["code"] == "coordinator_upgrade_required" and calls == ["status"]


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError("Background synchronization did not reach the expected state")


def test_real_http_ack_loss_and_coordinator_restart_keep_bundle_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_UPLOAD_TOKEN", TOKEN)
    platform = MockPlatform(tmp_path / "remote", TOKEN)
    platform.lose_next_ack = True
    platform.start()
    project = tmp_path / "project"
    project.mkdir()
    client = RecorderClient(project)
    try:
        client.call("aica_start_session", {"request_id": new_id(), "record": True})
        client.call("aica_bind_platform", {"server_url": platform.url, "project_id": PROJECT_ID,
                                          "confirm_upload": True, "allow_loopback_http": True, "interval_seconds": 1})
        wait_for(lambda: len(platform.requests) == 1)
        wait_for(lambda: client.call("aica_platform_status")["last_error"])
        first_hash = platform.requests[0]
        client.request("shutdown")
        wait_for(lambda: not (project / ".aica/endpoint.json").exists())
        client = RecorderClient(project)
        client.call("aica_sync_platform")
        status = wait_for(lambda: (result if (result := client.call("aica_platform_status"))["state"] == "synced" else None))
        assert platform.requests[:2] == [first_hash, first_hash]
        assert len(platform.receipts) == 1 and status["receipt"]["bundle_sha256"] == first_hash
        command = [sys.executable, "-m", "aica_mcp", "platform", "pause", "--project", str(project)]
        process = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert process.returncode == 0 and loads(process.stdout)["state"] == "paused"
    finally:
        client.request("shutdown")
        wait_for(lambda: not (project / ".aica/endpoint.json").exists())
        platform.close()


async def test_native_mcp_uploads_observed_image_automatically_and_supports_pause_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_UPLOAD_TOKEN", TOKEN)
    platform = MockPlatform(tmp_path / "remote", TOKEN)
    platform.start()
    project = tmp_path / "source"
    project.mkdir()
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
    (project / "创作图.png").write_bytes(image)
    root = Path(__file__).resolve().parents[3]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(str(root / path)
                   for path in ["packages/schema/src", "packages/mcp-adapters/src"])}
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "aica_mcp", "serve", "--project", str(project)],
                                      env=environment)
    try:
        async with AsyncExitStack() as stack:
            streams = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(*streams))
            await session.initialize()
            assert {"aica_pull_platform", "aica_sync_platform"} <= {tool.name for tool in (await session.list_tools()).tools}
            credentials = (await session.call_tool("aica_start_session", {"request_id": new_id(), "record": True})).structured_content
            step = {"label": "Observed image", "client": "mock-contract-fixture", "started_at": utc_now(),
                    "ended_at": utc_now(), "state": "succeeded", "inputs": {"prompt": "Synthetic fixture"},
                    "outputs": {}, "execution": {"kind": "manual", "tool": "fixture.image", "version": "1",
                    "inputs": {}, "params": {}, "outputs": ["image"]},
                    "artifacts": [{"path": "创作图.png", "mime_type": "image/png"}]}
            arguments = {"request_id": new_id(), "session_id": credentials["session_id"],
                         "session_token": credentials["session_token"], "step": step}
            recorded = (await session.call_tool("aica_record_step", arguments)).structured_content
            assert recorded["status"] == "success"
            bound = await session.call_tool("aica_bind_platform", {"server_url": platform.url, "project_id": PROJECT_ID,
                         "confirm_upload": True, "allow_loopback_http": True, "interval_seconds": 1})
            assert not bound.is_error
            client = RecorderClient(project, autostart=False)
            await asyncio.to_thread(wait_for, lambda: client.call("aica_platform_status")["state"] == "synced")
            paused = await session.call_tool("aica_pause_upload", {})
            assert paused.structured_content["state"] == "paused"
            count = len(platform.requests)
            second = await session.call_tool("aica_record_step", {**arguments, "request_id": new_id()})
            assert not second.is_error
            await asyncio.sleep(1.3)
            assert len(platform.requests) == count
            await session.call_tool("aica_resume_upload", {"confirm_upload": True})
            await asyncio.to_thread(wait_for, lambda: client.call("aica_platform_status")["state"] == "synced")
            assert len(platform.receipts) == 2
            third = await session.call_tool("aica_record_step", {**arguments, "request_id": new_id()})
            assert not third.is_error
            await asyncio.to_thread(wait_for, lambda: client.call("aica_platform_status")["state"] == "synced")
            assert len(platform.receipts) == 3
            artifact_id = recorded["artifact_ids"][0]
        client.request("shutdown")
        await asyncio.to_thread(wait_for, lambda: not (project / ".aica/endpoint.json").exists())
        project.rename(tmp_path / "original-not-available")
        with httpx.Client(trust_env=False, headers={"Authorization": "Bearer " + TOKEN}) as reader:
            archive = reader.get(platform.url + f"/api/projects/{PROJECT_ID}/archive").json()["archive"]
            assert len(archive["graph"]["nodes"]) == 3
            response = reader.get(platform.url + f"/api/projects/{PROJECT_ID}/artifacts/{artifact_id}")
            assert response.status_code == 200 and response.content == image
    finally:
        if (project / ".aica/endpoint.json").exists():
            RecorderClient(project, autostart=False).request("shutdown")
            await asyncio.to_thread(wait_for, lambda: not (project / ".aica/endpoint.json").exists())
        platform.close()
