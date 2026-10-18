import hashlib
import io
import os
import shutil
import sys
import threading
import time
import zipfile
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from aica_mcp.consent import AgentConsent
from aica_mcp.coordinator import Coordinator
from aica_mcp.credentials import Credentials
from aica_schema.protocol import ProtocolError, dumps, loads

PROJECT = "00000000-0000-4000-8000-000000000001"
TOKEN_ID = "00000000-0000-4000-8000-000000000002"
LOGIN_TOKEN = "login-private-token-" + "a" * 40
UPLOAD_TOKEN = "upload-private-token-" + "b" * 40
PASSWORD = "PASSWORD_MUST_NOT_BE_STORED_8493"


@pytest.fixture
def coordinator(tmp_path, monkeypatch):
    monkeypatch.setenv("AICA_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    monkeypatch.delenv("AICA_UPLOAD_TOKEN", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    instance = Coordinator(project)
    instance.connections.origin = "http://127.0.0.1:12345"
    instance.test_requests = []

    def remote(request):
        instance.test_requests.append((request.method, request.url.path))
        if request.url.path == "/api/auth/login":
            assert loads(request.content.decode())["password"] == PASSWORD
            return httpx.Response(200, json={"status": "success", "access_token": LOGIN_TOKEN, "expires_in": 3600})
        if request.url.path == "/api/projects" and request.method == "GET":
            return httpx.Response(200, json={"status": "success", "projects": [{"id": PROJECT, "title": "Test project"}]})
        if request.url.path == "/api/projects" and request.method == "POST":
            return httpx.Response(201, json={"status": "success", "project": {"id": PROJECT}})
        if request.url.path == "/api/repos":
            assert request.headers["Authorization"] == "Bearer " + LOGIN_TOKEN
            repository = {"id": TOKEN_ID, "title": "Sync work"}
            return httpx.Response(200 if request.method == "GET" else 201,
                                  json={"status": "success", "repositories": [repository], "repository": repository})
        if request.url.path == "/api/mcp/upload-tokens":
            assert request.headers["Authorization"] == "Bearer " + LOGIN_TOKEN
            return httpx.Response(201, json={"status": "success", "access_token": UPLOAD_TOKEN, "id": TOKEN_ID,
                                            "project_id": PROJECT, "expires_in": 3600})
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "success"})
        assert request.headers["Authorization"] == "Bearer " + UPLOAD_TOKEN
        body = request.read()
        with zipfile.ZipFile(io.BytesIO(body)) as bundle:
            manifest = loads(bundle.read("manifest.json").decode())
        return httpx.Response(201, json={"status": "success", "format": "aica-transfer/0.1", "import_id": "fixture-import",
                                        "project_id": PROJECT, "bundle_sha256": hashlib.sha256(body).hexdigest(),
                                        "archive_sha256": manifest["archive"]["sha256"]})

    transport = httpx.MockTransport(remote)
    instance.connections.transport = transport
    instance.platform.transport = transport
    yield instance
    instance.platform.close()
    instance.recorder.close()


def login(coordinator):
    result = coordinator.control_dispatch("connect_create", {"server_url": "https://platform.invalid"})
    nonce = urlsplit(result["url"]).fragment
    coordinator.connections.dispatch("login", nonce, username="test-user", password=PASSWORD)
    return nonce


def connect(coordinator, create=False):
    nonce = login(coordinator)
    result = coordinator.connections.dispatch("bind", nonce, project_id="" if create else PROJECT,
                                               title="My project", confirm_project=True)
    assert result["connected"]
    return result


def event(coordinator, name="UserPromptSubmit", turn="turn-1", **fields):
    return {"cwd": str(coordinator.recorder.project), "session_id": "host-1", "turn_id": turn,
            "client": "codex", "hook_event_name": name, **fields}


def start(coordinator, turn="turn-1", owner=123):
    bridge = coordinator.turns.attach("codex", owner)["bridge_id"]
    result = coordinator.turns.conversation_submit(event(coordinator, turn=turn, prompt="Recorded task"), [owner])
    assert result["recording"] and result.get("decision") != "block"
    return bridge


def test_cancel_and_missing_consent_do_not_change_policy_binding_or_credentials(coordinator):
    nonce = login(coordinator)
    with pytest.raises(ProtocolError, match="Explicit project"):
        coordinator.connections.dispatch("bind", nonce, project_id=PROJECT, confirm_project=False)
    coordinator.connections.dispatch("cancel", nonce)
    assert not coordinator.platform.config_path.exists()
    assert not (coordinator.recorder.directory / "recording-policy.json").exists()
    assert not list(coordinator.recorder.project.parent.glob("credentials/*.json"))
    assert coordinator.test_requests == [("POST", "/api/auth/login")]


@pytest.mark.parametrize("create", [True, False])
def test_browser_login_binds_without_env_or_project_id_copy_and_keeps_secrets_outside_workspace(coordinator, create):
    connect(coordinator, create)
    config = coordinator.platform.read_config()
    assert config["project_id"] == PROJECT
    assert coordinator.platform.project_access()["state"] == "active"
    assert coordinator.platform.token(config) == UPLOAD_TOKEN
    assert coordinator.platform.credentials.token(config, login=True) == LOGIN_TOKEN
    coordinator.recorder.flush()
    coordinator.platform.close()
    coordinator.recorder.close()
    for path in coordinator.recorder.project.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert not any(secret.encode() in content for secret in (PASSWORD, LOGIN_TOKEN, UPLOAD_TOKEN))
    path = coordinator.platform.credentials.path(config)
    assert PASSWORD not in path.read_text()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_nonce_expires_and_is_consumed_without_exposing_login_token(coordinator):
    nonce = login(coordinator)
    assert LOGIN_TOKEN not in dumps(coordinator.connections.dispatch("view", nonce))
    coordinator.connections.requests[nonce]["created"] -= 601
    with pytest.raises(ProtocolError, match="expired"):
        coordinator.connections.dispatch("projects", nonce)
    connect(coordinator)
    assert not coordinator.connections.requests


def test_project_grant_survives_restart_but_copied_binding_cannot_authorize_other_workspace(coordinator, tmp_path):
    connect(coordinator)
    config = coordinator.platform.read_config()
    saved = coordinator.platform.credentials.read(config)
    restored = Credentials(coordinator.recorder.project, coordinator.recorder.stream_id)
    assert restored.read(config) == saved
    new_stream = Credentials(coordinator.recorder.project, "new-recording-stream")
    assert new_stream.read(config) is None
    other = tmp_path / "other"
    other.mkdir()
    target = Credentials(other, coordinator.recorder.stream_id)
    assert target.read(config) is None
    shutil.copy2(restored.path(config), target.path(config))
    with pytest.raises(ProtocolError, match="another project"):
        target.read(config)


def test_expired_credentials_require_relogin_and_do_not_fall_back_to_environment(coordinator, monkeypatch):
    connect(coordinator)
    config = coordinator.platform.read_config()
    saved = coordinator.platform.credentials.read(config)
    saved["upload_expires_at"] = time.time() - 1
    coordinator.platform.credentials.write(config, saved)
    monkeypatch.setenv("AICA_UPLOAD_TOKEN", "c" * 64)
    with pytest.raises(ProtocolError) as error:
        coordinator.platform.token(config)
    assert error.value.code == "credential_expired"
    assert coordinator.platform.status()["credential_state"] == "expired"


def test_project_recording_auto_registers_generated_file_exports_and_uploads_all_bytes(coordinator):
    connect(coordinator)
    start(coordinator)
    gate = coordinator.turns
    tool = {"tool_name": "imagegen", "tool_use_id": "image-1", "tool_input": {"prompt": "generate"}}
    gate.hook(event(coordinator, "PreToolUse", **tool))
    image = coordinator.recorder.project / "generated.png"
    image.write_bytes(b"generated-image-fixture")
    gate.hook(event(coordinator, "PostToolUse", **tool, tool_response={"path": str(image)}))
    images = [item for item in coordinator.recorder.document["artifacts"] if item["mime_type"] == "image/png"]
    assert len(images) == 1 and images[0]["producer_run_id"]
    result = coordinator.control_dispatch("hook", {"event": event(coordinator, "Stop", last_assistant_message="Completed")})
    assert "upload_bridge_id" not in result and "自动上传" in result["systemMessage"]
    assert (coordinator.recorder.project / "exports/aica.json").exists()
    coordinator.platform.run_once()
    state = coordinator.platform.status()
    assert state["state"] == "synced" and state["receipt"]["project_id"] == PROJECT


async def test_attachment_and_sync_reuse_grant_without_asking_again(coordinator, monkeypatch):
    connect(coordinator)
    bridge = start(coordinator)
    consent = AgentConsent(coordinator.recorder.project)
    consent.bridge_id = bridge

    async def control(name, arguments=None, timeout=5):
        result = coordinator.control_dispatch(name, arguments or {})
        return result

    async def unexpected(*args, **kwargs):
        pytest.fail("Project-authorized actions must not show another consent question")

    monkeypatch.setattr(consent, "control", control)
    monkeypatch.setattr(consent, "ask", unexpected)
    (coordinator.recorder.project / "selected.txt").write_text("User selected this task output")
    result = await consent.select(None, ["selected.txt"])
    assert result["selected"] and result["confirmation_reason"] == "project_authorized"
    result = await consent.upload(None)
    assert result["status"] == "success"


def test_pause_blocks_late_records_and_uploads_resume_requires_existing_grant_and_disconnect_removes_it(coordinator):
    connect(coordinator)
    start(coordinator)
    coordinator.project_access("pause")
    calls = len(coordinator.test_requests)
    result = coordinator.turns.conversation_submit(event(coordinator, turn="private-turn", prompt="PRIVATE_PAUSED_499"), [])
    assert result["project_access"] == "paused" and not result["recording"] and "decision" not in result
    coordinator.platform.run_once()
    assert len(coordinator.test_requests) == calls
    assert "PRIVATE_PAUSED_499" not in dumps(coordinator.recorder.get_events())
    coordinator.project_access("resume")
    assert coordinator.platform.project_access()["state"] == "active"
    result = coordinator.project_access("disconnect")
    assert result["remote_token_revoked"]
    assert not list(coordinator.recorder.project.parent.glob("credentials/*.json"))
    with pytest.raises(ProtocolError, match="Connect"):
        coordinator.project_access("resume")


def test_unrelated_owner_and_changed_grant_cannot_write(coordinator):
    connect(coordinator)
    coordinator.turns.attach("codex", 123)
    with pytest.raises(ProtocolError):
        coordinator.turns.conversation_submit(event(coordinator, prompt="PRIVATE_WRONG_OWNER"), [456])
    assert not coordinator.recorder.document["sessions"]
    start(coordinator, turn="new-turn", owner=789)
    config = coordinator.platform.read_config()
    saved = coordinator.platform.credentials.read(config)
    saved["grant_id"] = "replacement-grant"
    coordinator.platform.credentials.write(config, saved)
    coordinator.turns.hook(event(coordinator, "Stop", turn="new-turn", last_assistant_message="PRIVATE_LATE_432"))
    assert "PRIVATE_LATE_432" not in dumps(coordinator.recorder.get_events())


def test_connection_http_rejects_cross_origin_wrong_host_and_invalid_nonce(coordinator):
    thread = threading.Thread(target=coordinator.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while coordinator.server is None and time.monotonic() < deadline:
        time.sleep(0.01)
    origin = f"http://127.0.0.1:{coordinator.server.server_port}"
    try:
        with httpx.Client(trust_env=False) as client:
            page = client.get(origin + "/connect")
            assert page.status_code == 200 and "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
            for headers in ({}, {"Origin": "https://evil.invalid"}, {"Origin": origin, "Host": "evil.invalid"}):
                assert client.post(origin + "/connect/view", headers=headers, json={"nonce": "missing"}).status_code == 403
            result = client.post(origin + "/connect/view", headers={"Origin": origin}, json={"nonce": "missing"}).json()
            assert result["status"] == "error" and result["code"] == "connection_expired"
    finally:
        coordinator.server.shutdown()
        thread.join(timeout=5)


def test_failed_connection_rolls_back_settings_and_revokes_issued_token(coordinator, monkeypatch):
    nonce = login(coordinator)

    def fail(project):
        raise OSError("Synthetic policy write failure")

    monkeypatch.setattr("aica_mcp.platform.enable_turn_policy", fail)
    with pytest.raises(OSError):
        coordinator.connections.dispatch("bind", nonce, project_id=PROJECT, confirm_project=True)
    assert not coordinator.platform.config_path.exists()
    assert not (coordinator.recorder.directory / "recording-policy.json").exists()
    assert not list(coordinator.recorder.project.parent.glob("credentials/*.json"))
    assert coordinator.test_requests[-1] == ("DELETE", "/api/mcp/upload-tokens/" + TOKEN_ID)


def test_cancel_existing_connection_preserves_binding_and_grant(coordinator):
    connect(coordinator)
    config = coordinator.platform.read_config()
    before = coordinator.platform.credentials.read(config)
    nonce = login(coordinator)
    coordinator.connections.dispatch("cancel", nonce)
    assert coordinator.platform.read_config() == config
    assert coordinator.platform.credentials.read(config) == before


def test_credential_directory_in_workspace_and_symlinks_are_rejected(coordinator, monkeypatch, tmp_path):
    connect(coordinator)
    config = coordinator.platform.read_config()
    monkeypatch.setenv("AICA_CREDENTIALS_DIR", str(coordinator.recorder.project / "private"))
    with pytest.raises(ProtocolError, match="outside"):
        coordinator.platform.credentials.path(config)
    directory = tmp_path / "symlink-credentials"
    directory.symlink_to(tmp_path / "credentials")
    monkeypatch.setenv("AICA_CREDENTIALS_DIR", str(directory))
    with pytest.raises(ProtocolError, match="symlink"):
        coordinator.platform.credentials.read(config)


def test_read_tool_and_preexisting_or_outside_paths_are_not_auto_attached(coordinator, tmp_path):
    connect(coordinator)
    old = coordinator.recorder.project / "old.txt"
    old.write_text("not generated this turn")
    os.utime(old, (1, 1))
    outside = tmp_path / "private-outside.png"
    outside.write_bytes(b"outside")
    start(coordinator)
    for identity, tool_name, path in [("old", "imagegen", old), ("outside", "imagegen", outside), ("read", "Read", old)]:
        fields = {"tool_name": tool_name, "tool_use_id": identity, "tool_input": {}}
        coordinator.turns.hook(event(coordinator, "PreToolUse", **fields))
        coordinator.turns.hook(event(coordinator, "PostToolUse", **fields, tool_response={"path": str(path)}))
    assert not any(item["mime_type"].startswith("image/") or item.get("size") == old.stat().st_size
                   for item in coordinator.recorder.document["artifacts"])


def test_old_project_coordinator_requires_upgrade_even_if_turn_interfaces_match(coordinator):
    from aica_mcp.server import require_turn_coordinator

    status = coordinator.dispatch("status", {})
    require_turn_coordinator(status)
    status.pop("project_connection_version")
    with pytest.raises(ProtocolError) as error:
        require_turn_coordinator(status)
    assert error.value.code == "coordinator_upgrade_required"


def test_opencode_project_hook_preserves_recording_reply_without_browser(coordinator, monkeypatch):
    from aica_mcp.hosts import run_hook

    connect(coordinator)
    coordinator.turns.attach("opencode", 123)
    envelope = event(coordinator, prompt="OpenCode project task")
    envelope["client"] = "opencode"
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(dumps(envelope).encode())))
    monkeypatch.setattr("aica_mcp.hosts.owner_pids", lambda: [123])
    monkeypatch.setattr("aica_mcp.hosts.control_request", lambda project, name, arguments=None: coordinator.control_dispatch(name, arguments or {}))
    result = run_hook("opencode", confirmation="browser")
    assert result == {"recording": True}


def test_connection_http_login_selection_and_grant(coordinator):
    thread = threading.Thread(target=coordinator.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while coordinator.server is None and time.monotonic() < deadline:
        time.sleep(0.01)
    origin = f"http://127.0.0.1:{coordinator.server.server_port}"
    try:
        nonce = urlsplit(coordinator.connections.create("https://platform.invalid")["url"]).fragment
        with httpx.Client(trust_env=False, headers={"Origin": origin}) as client:
            view = client.post(origin + "/connect/view", json={"nonce": nonce}).json()
            assert view["project"] == coordinator.recorder.project.name
            result = client.post(origin + "/connect/login", json={"nonce": nonce, "username": "test-user", "password": PASSWORD}).json()
            assert result == {"status": "success", "logged_in": True}
            result = client.post(origin + "/connect/projects", json={"nonce": nonce}).json()
            assert result["projects"][0]["id"] == PROJECT
            result = client.post(origin + "/connect/bind", json={"nonce": nonce, "project_id": PROJECT, "confirm_project": True}).json()
            assert result["connected"] and LOGIN_TOKEN not in dumps(result) and UPLOAD_TOKEN not in dumps(result)
            result = client.post(origin + "/connect/bind", json={"nonce": nonce, "project_id": PROJECT, "confirm_project": True}).json()
            assert result["code"] == "connection_expired"
    finally:
        coordinator.server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("existing", [True, False])
def test_bidirectional_connection_registers_explicit_source_file_grant(coordinator, existing):
    nonce = login(coordinator)
    listing = coordinator.connections.dispatch("repositories", nonce)
    assert listing["repositories"][0]["id"] == TOKEN_ID
    result = coordinator.connections.dispatch("bind", nonce, project_id=PROJECT, title="Sync work",
                                               confirm_project=True, sync_enabled=True, repo_id=TOKEN_ID if existing else "")
    assert result["connected"]
    config = coordinator.platform.read_config()
    assert config["sync"] == {"repo_id": TOKEN_ID, "branch": "main"}
    saved = coordinator.platform.credentials.read(config)
    assert saved["sync_files"] is True and saved["sync_repo_id"] == TOKEN_ID
    assert (("POST", "/api/repos") in coordinator.test_requests) is not existing
