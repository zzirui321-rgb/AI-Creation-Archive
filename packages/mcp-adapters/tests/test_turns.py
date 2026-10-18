import asyncio
import hashlib
import os
import subprocess
import sys
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from aica_mcp.capture import run_command
from aica_mcp.client import RecorderClient
from aica_mcp.consent import AgentConsent, control_request, enable_turn_policy
from aica_mcp.coordinator import Coordinator
from aica_mcp.hosts import hook_config
from aica_mcp.platform import PlatformSync
from aica_mcp.store import Recorder
from aica_mcp.transfer import build_json
from aica_mcp.turns import TurnGate
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, utc_now, validate_archive
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from test_platform import TOKEN, receipt


@pytest.fixture
def gate(tmp_path):
    recorder = Recorder(tmp_path)
    enable_turn_policy(tmp_path)
    instance = TurnGate(recorder)
    yield instance
    recorder.close()


def hook(gate, name, host="host-1", turn="turn-1", **fields):
    event = {"cwd": str(gate.recorder.project), "session_id": host, "turn_id": turn,
             "hook_event_name": name, **fields}
    result = gate.hook(event)
    return result


def begin(gate, prompt="PUBLIC_YES_CONTENT", host="host-1", turn="turn-1", record=True, bridge=None):
    bridge = bridge or gate.attach()["bridge_id"]
    hook(gate, "UserPromptSubmit", host, turn, prompt=prompt, model="observed-agent-model")
    pending = gate.pending(bridge, host, turn)
    reply = gate.resolve(bridge, host, turn, pending["challenge"], record)
    return bridge, reply


def assert_absent(project, marker):
    needle = marker.encode()
    for path in project.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), path


def test_no_does_not_create_session_event_outbox_artifact_or_marker(gate):
    before = gate.recorder.document["snapshot"]["commit_seq"]
    begin(gate, "PRIVATE_NO_MARKER_7913", record=False)
    hook(gate, "PreToolUse", tool_name="imagegen", tool_use_id="call1", tool_input={"prompt": "PRIVATE_NO_MARKER_7913"})
    hook(gate, "PostToolUse", tool_name="imagegen", tool_use_id="call1", tool_input={}, tool_response="PRIVATE_NO_MARKER_7913")
    hook(gate, "Stop", last_assistant_message="PRIVATE_NO_MARKER_7913")
    gate.recorder.flush()
    assert gate.recorder.document["snapshot"]["commit_seq"] == before
    assert not gate.recorder.document["sessions"]
    assert "PRIVATE_NO_MARKER_7913" not in repr(gate.turns)
    assert not (gate.recorder.directory / "outboxes").exists()
    assert_absent(gate.recorder.project, "PRIVATE_NO_MARKER_7913")


def test_yes_tools_and_final_are_recorded_without_executable_or_image_model_claim(gate):
    begin(gate)
    fields = {"tool_name": "imagegen", "tool_use_id": "call1", "tool_input": {"prompt": "draw", "api_key": "SECRET_NOT_ARCHIVED"}}
    hook(gate, "PreToolUse", **fields)
    hook(gate, "PostToolUse", **fields, tool_response={"result": "observed", "access_token": "SECRET_NOT_ARCHIVED"})
    hook(gate, "Stop", last_assistant_message="FINAL_VISIBLE_REPLY")
    document = gate.recorder.read_archive()["archive"]
    assert validate_archive(document)["valid"]
    assert len(document["graph"]["nodes"]) == len(document["runs"]) == len(document["agents"]) == 1
    assert document["runs"][0]["state"] == "succeeded"
    assert document["agents"][0]["model"] == {"name": "observed-agent-model"}
    assert "model" not in document["graph"]["nodes"][0]["execution"]
    assert document["graph"]["nodes"][0]["execution"]["kind"] == "manual"
    assert document["sessions"][0]["state"] == "closed"
    assert "FINAL_VISIBLE_REPLY" in dumps(gate.recorder.get_events())
    assert "SECRET_NOT_ARCHIVED" not in dumps(gate.recorder.get_events())


def test_yes_no_yes_never_inherits_or_sweeps_no_turn(gate):
    bridge, reply = begin(gate, "FIRST_YES", turn="one")
    assert reply["recording"]
    hook(gate, "Stop", turn="one", last_assistant_message="FIRST_FINISH")
    begin(gate, "MIDDLE_NO_PRIVATE", turn="two", bridge=bridge, record=False)
    hook(gate, "Stop", turn="two", last_assistant_message="MIDDLE_NO_PRIVATE")
    begin(gate, "THIRD_YES", turn="three", bridge=bridge)
    hook(gate, "Stop", turn="three", last_assistant_message="THIRD_FINISH")
    gate.recorder.flush()
    assert len(gate.recorder.document["sessions"]) == 2
    assert_absent(gate.recorder.project, "MIDDLE_NO_PRIVATE")


def test_missing_choice_expires_in_memory_without_writing(gate):
    hook(gate, "UserPromptSubmit", prompt="NEVER_CHOSEN_PRIVATE")
    gate.turns[("codex", "host-1", "turn-1")]["created"] -= 301
    gate.expire()
    hook(gate, "Stop", last_assistant_message="NEVER_CHOSEN_PRIVATE")
    assert not gate.recorder.document["sessions"]
    assert "NEVER_CHOSEN_PRIVATE" not in repr(gate.turns)
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "NEVER_CHOSEN_PRIVATE")


def test_normal_rpc_and_old_session_cannot_write_even_during_yes(tmp_path):
    coordinator = Coordinator(tmp_path)
    old = coordinator.recorder.start_session(new_id(), True)
    enable_turn_policy(tmp_path)
    gate = coordinator.turns
    begin(gate)
    event = {"event_id": new_id(), "source_id": "old-source", "source_seq": 1, "session_id": old["session_id"],
             "agent_id": None, "type": "message.recorded", "timestamp": utc_now(), "causal_parents": [],
             "payload": {"role": "user", "content": "BYPASS_PRIVATE"}}
    try:
        for name, arguments in [("aica_start_session", {"request_id": "BYPASS_PRIVATE", "record": True}),
                                ("aica_start_session", {"request_id": "BYPASS_PRIVATE", "record": False}),
                                ("aica_submit_event", {"session_token": old["session_token"], "event": event}),
                                ("aica_check_consent", {"session_id": old["session_id"], "session_token": old["session_token"],
                                                        "event_type": "tool.finished"})]:
            with pytest.raises(ProtocolError) as error:
                coordinator.dispatch(name, arguments)
            assert error.value.code == "turn_not_authorized"
        with pytest.raises(ProtocolError):
            coordinator.dispatch("aica_confirm_turn", {"host_session_id": "host-1", "turn_id": "turn-1"})
        assert_absent(tmp_path, "BYPASS_PRIVATE")
    finally:
        coordinator.recorder.close()


@pytest.mark.parametrize("ending", ["Stop", "Interrupt", "SessionEnd"])
def test_close_rejects_late_new_tool_results_and_artifacts(gate, ending):
    bridge, reply = begin(gate)
    hook(gate, "PreToolUse", tool_name="Bash", tool_use_id="call1", tool_input={"command": "work"})
    hook(gate, ending)
    hook(gate, "PostToolUse", tool_name="Bash", tool_use_id="call1", tool_input={}, tool_response="LATE_PRIVATE")
    with pytest.raises(ProtocolError):
        gate.capture(bridge, "host-1", "turn-1", ["LATE_PRIVATE.png"])
    assert gate.recorder.document["runs"][0]["state"] == "unknown"
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "LATE_PRIVATE")


def test_reused_turn_id_closes_grant_and_discards_steering(gate):
    begin(gate)
    result = hook(gate, "UserPromptSubmit", prompt="STEERING_PRIVATE")
    assert result["state"] == "ambiguous_submission"
    hook(gate, "Stop", last_assistant_message="STEERING_PRIVATE")
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "STEERING_PRIVATE")


def test_concurrent_hosts_and_children_do_not_inherit_consent(gate):
    begin(gate)
    begin(gate, "OTHER_HOST_NO_PRIVATE", host="host-2", record=False)
    hook(gate, "PreToolUse", host="child", tool_name="Bash", tool_use_id="call1", tool_input={"x": "CHILD_PRIVATE"})
    assert gate.turns[("codex", "host-1", "turn-1")]["state"] == "recording"
    hook(gate, "Stop", last_assistant_message="MAIN_FINISH")
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "OTHER_HOST_NO_PRIVATE")
    assert_absent(gate.recorder.project, "CHILD_PRIVATE")


def test_shared_host_identity_is_closed_before_child_agent_runs(gate):
    begin(gate)
    hook(gate, "PreToolUse", tool_name="collaboration.spawn_agent", tool_use_id="spawn", tool_input={"message": "child work"})
    assert gate.turns[("codex", "host-1", "turn-1")]["state"] == "child_scope_unsupported"
    hook(gate, "PreToolUse", tool_name="Bash", tool_use_id="child-tool", tool_input={"command": "SHARED_CHILD_PRIVATE"})
    hook(gate, "Stop", last_assistant_message="SHARED_CHILD_PRIVATE")
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "SHARED_CHILD_PRIVATE")


def test_bridge_disconnect_and_restart_require_new_yes(gate):
    bridge, reply = begin(gate)
    gate.detach(bridge)
    with pytest.raises(ProtocolError):
        gate.capture(bridge, "host-1", "turn-1", ["DISCONNECTED_PRIVATE.png"])
    gate = TurnGate(gate.recorder)
    assert gate.pending(gate.attach()["bridge_id"], "host-1", "turn-1")["state"] == "unavailable"
    assert not gate.recorder.recording_permission


@pytest.fixture
def running(tmp_path):
    enable_turn_policy(tmp_path)
    coordinator = Coordinator(tmp_path)
    thread = threading.Thread(target=coordinator.serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not (tmp_path / ".aica" / "control.json").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    yield coordinator
    coordinator.server.shutdown()
    thread.join(5)
    assert not thread.is_alive()


def test_control_endpoint_uses_separate_credential_and_origin_guard(running):
    client = RecorderClient(running.recorder.project)
    endpoint = client.endpoint
    url = f"http://127.0.0.1:{endpoint['port']}/control"
    with httpx.Client(trust_env=False) as transport:
        denied = transport.post(url, headers={"Authorization": f"Bearer {endpoint['token']}"},
                                json={"method": "resolve", "arguments": {"record": True}})
        assert denied.status_code == 403
        denied = transport.post(url, headers={"Authorization": f"Bearer {running.control_token}", "Origin": "https://untrusted.test"},
                                json={"method": "attach", "arguments": {}})
        assert denied.status_code == 403
    assert control_request(client.project, "attach")["status"] == "success"


async def native_connection(stack, project, callback=None, default_yes=False):
    root = Path(__file__).resolve().parents[3]
    environment = {"PYTHONPATH": os.pathsep.join(str(root / path) for path in ("packages/schema/src", "packages/mcp-adapters/src")),
                   "BROWSER": sys.executable + ' -c "pass" %s'}
    params = StdioServerParameters(command=sys.executable, args=["-m", "aica_mcp", "serve", "--project", str(project), "--codex-turns"],
                                  env=environment)
    if default_yes:
        params.args.append("--default-yes")
    streams = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(*streams, elicitation_callback=callback))
    await session.initialize()
    return session


@pytest.mark.parametrize("action,choice", [("accept", "Yes"), ("accept", "No"), ("decline", None), ("cancel", None)])
async def test_real_stdio_mcp_elicitation_is_the_only_decision_channel(running, action, choice):
    asked = []

    async def confirm(context, params):
        asked.append(params.message)
        return types.ElicitResult(action=action, content={"choice": choice} if choice else None)

    project = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, confirm)
        args = {"host_session_id": "host-1", "turn_id": "turn-1"}
        fake = await session.call_tool("aica_start_session", {"request_id": "forged", "record": True})
        assert fake.is_error
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "host-1", "turn_id": "turn-1",
                        "hook_event_name": "UserPromptSubmit", "prompt": "NATIVE_SYNTHETIC_INPUT"}})
        result = await session.call_tool("aica_confirm_turn", args)
        assert not result.is_error, result
        assert result.structured_content["recording"] == (choice == "Yes")
        duplicate = await session.call_tool("aica_confirm_turn", args)
        assert not duplicate.is_error and len(asked) == 1
        assert "session_token" not in dumps(result.structured_content)
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "host-1", "turn_id": "turn-1",
                        "hook_event_name": "Stop", "last_assistant_message": "NATIVE_SYNTHETIC_OUTPUT"}})
    if choice == "Yes":
        assert len(running.recorder.document["sessions"]) == 1
    else:
        assert not running.recorder.document["sessions"]
        assert_absent(project, "NATIVE_SYNTHETIC_INPUT")
        assert_absent(project, "NATIVE_SYNTHETIC_OUTPUT")


async def test_elicitation_failure_defaults_no_without_creating_a_session(gate):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("UI unavailable")

    bridge = AgentConsent(gate.recorder.project)
    assert not await bridge.ask(SimpleNamespace(session=SimpleNamespace(elicit_form=unavailable), request_id=1), "Synthetic question")
    assert not gate.recorder.document["sessions"]


def test_generated_hooks_never_install_trust_or_capture_transcripts():
    config = hook_config()
    assert len(config["hooks"]["UserPromptSubmit"][0]["hooks"]) == 2
    text = dumps(config)
    assert "aica_confirm_turn" in text
    assert config["hooks"]["UserPromptSubmit"][0]["hooks"][1]["input"]["hook_output"] is True
    assert "transcript_path" not in text
    assert "bypass" not in text
    assert config["hooks"]["Interrupt"][0]["hooks"][0]["timeout"] <= 3


async def test_native_artifact_selection_requires_second_ui_confirmation_and_active_turn(running):
    choices = iter(["Yes", "No", "Yes"])

    async def confirm(context, params):
        return types.ElicitResult(action="accept", content={"choice": next(choices)})

    project = running.recorder.project
    (project / "picture.png").write_bytes(b"selected-image-fixture")
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, confirm)
        event = {"cwd": str(project), "session_id": "host-1", "turn_id": "turn-1", "hook_event_name": "UserPromptSubmit",
                 "prompt": "select a picture"}
        control_request(project, "hook", {"event": event})
        reply = await session.call_tool("aica_confirm_turn", {"host_session_id": "host-1", "turn_id": "turn-1"})
        assert not reply.is_error
        reply = await session.call_tool("aica_select_turn_artifacts", {"paths": ["picture.png"]})
        assert not reply.is_error and not reply.structured_content["artifacts"]
        assert reply.structured_content["selected"] is False and "recording" not in reply.structured_content
        reply = await session.call_tool("aica_select_turn_artifacts", {"paths": ["picture.png"]})
        assert not reply.is_error, reply
        artifact = reply.structured_content["artifacts"][0]
        assert (project / artifact["uri"]).read_bytes() == b"selected-image-fixture"
        assert artifact["mime_type"] == "image/png"
        control_request(project, "hook", {"event": {**event, "hook_event_name": "Stop"}})
        reply = await session.call_tool("aica_select_turn_artifacts", {"paths": ["picture.png"]})
        assert reply.is_error


def test_no_turn_creates_no_upload_packet_and_paused_yes_stays_local(gate, monkeypatch):
    monkeypatch.setenv("AICA_UPLOAD_TOKEN", TOKEN)
    bodies = []

    def remote(request):
        bodies.append(request.read())
        return httpx.Response(201, json=receipt(request))

    sync = PlatformSync(gate.recorder, httpx.MockTransport(remote))
    sync.bind("https://platform.invalid", new_id(), True)
    sync.run_once()
    initial = len(bodies)
    assert initial == 0
    begin(gate, "NEVER_UPLOAD_NO_PRIVATE", record=False)
    sync.run_once()
    assert len(bodies) == initial
    sync.set_enabled(False)
    begin(gate, "YES_PAUSED_LOCAL", turn="next")
    hook(gate, "Stop", turn="next", last_assistant_message="local finish")
    sync.run_once()
    assert len(bodies) == initial
    assert "YES_PAUSED_LOCAL" in dumps(gate.recorder.get_events())
    assert_absent(gate.recorder.project, "NEVER_UPLOAD_NO_PRIVATE")


def test_crashed_bridge_lease_expires_and_compaction_does_not_reask(gate):
    bridge, reply = begin(gate)
    result = hook(gate, "SessionStart", source="compact")
    assert result["state"] == "unchanged"
    assert gate.turns[("codex", "host-1", "turn-1")]["state"] == "recording"
    gate.bridges[bridge] -= 11
    gate.expire()
    hook(gate, "Stop", last_assistant_message="AFTER_CRASH_PRIVATE")
    assert gate.recorder.document["sessions"][0]["state"] == "closed"
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "AFTER_CRASH_PRIVATE")


def test_direct_client_checks_before_durable_step_plan_and_transport_outbox(running):
    client = RecorderClient(running.recorder.project)
    arguments = {"request_id": "forged-step", "session_id": "fake", "session_token": "x" * 32,
                 "step": {"label": "DENIED_STEP_PRIVATE", "client": "codex", "execution": {"kind": "manual",
                          "tool": "observed", "version": None, "inputs": {}, "params": {}, "outputs": []},
                          "started_at": utc_now(), "ended_at": utc_now(), "state": "succeeded"}}
    with pytest.raises(ProtocolError):
        client.call("aica_record_step", arguments)
    assert not (client.directory / "outboxes").exists()
    event = {"event_id": new_id(), "source_id": new_id(), "source_seq": 1, "session_id": "fake", "agent_id": None,
             "type": "message.recorded", "timestamp": utc_now(), "causal_parents": [],
             "payload": {"role": "user", "content": "DENIED_STEP_PRIVATE"}}
    reply = client.call("aica_submit_event", {"session_token": "x" * 32, "event": event})
    assert reply["status"] == "error"
    assert not (client.directory / "outboxes").exists()
    assert_absent(client.project, "DENIED_STEP_PRIVATE")


async def test_confirm_without_authenticated_submission_does_not_ask_or_record(running):
    async def unexpected(context, params):
        pytest.fail("A model-supplied identity alone must not trigger a decision")

    async with AsyncExitStack() as stack:
        session = await native_connection(stack, running.recorder.project, unexpected)
        reply = await session.call_tool("aica_confirm_turn", {"host_session_id": "forged", "turn_id": "fake"})
        assert not reply.is_error and not reply.structured_content["recording"]
    assert not running.recorder.document["sessions"]


async def test_command_hook_and_mcp_hook_race_defaults_no_without_content_on_disk(running):
    async def decline(context, params):
        return types.ElicitResult(action="accept", content={"choice": "No"})

    project = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, decline)
        pending = asyncio.create_task(session.call_tool("aica_confirm_turn", {"host_session_id": "host-1", "turn_id": "turn-1"}))
        await asyncio.sleep(0.05)
        event = {"cwd": str(project), "session_id": "host-1", "turn_id": "turn-1", "hook_event_name": "UserPromptSubmit",
                 "prompt": "COMMAND_HOOK_NO_PRIVATE"}
        result = subprocess.run([sys.executable, "-m", "aica_mcp", "codex-hook"], input=dumps(event),
                                capture_output=True, text=True, check=True, cwd=project)
        assert "COMMAND_HOOK_NO_PRIVATE" not in result.stdout + result.stderr
        reply = await pending
        assert not reply.is_error and not reply.structured_content["recording"], reply
    assert not running.recorder.document["sessions"]
    assert_absent(project, "COMMAND_HOOK_NO_PRIVATE")


async def test_real_mcp_client_without_elicitation_support_defaults_no(running):
    project = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project)
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "host-1", "turn_id": "turn-1",
                        "hook_event_name": "UserPromptSubmit", "prompt": "UNSUPPORTED_UI_PRIVATE"}})
        pending = asyncio.create_task(session.call_tool("aica_confirm_turn", {"host_session_id": "host-1", "turn_id": "turn-1"}))
        for attempt in range(100):
            if running.approvals.requests:
                break
            await asyncio.sleep(0.02)
        assert running.approvals.requests
        for request_id in running.approvals.requests:
            running.approvals.cancel(request_id)
        reply = await pending
        assert not reply.is_error and not reply.structured_content["recording"]
    assert not running.recorder.document["sessions"]
    assert_absent(project, "UNSUPPORTED_UI_PRIVATE")


async def test_import_is_separate_native_consent_and_does_not_grant_turn_recording(running):
    project = running.recorder.project
    donor = project / "donor"
    donor.mkdir()
    recorder = Recorder(donor)
    try:
        exported = recorder.flush()
        build_json(donor, project / "history.json", exported["sha256"])
    finally:
        recorder.close()
    arguments = {"request_id": "restore-selected", "path": "history.json", "target": "restored", "confirm_import": True}
    with pytest.raises(ProtocolError):
        RecorderClient(project).call("aica_import_archive", arguments)
    choices = iter(["No", "Yes"])

    async def confirm(context, params):
        assert "单独授权导入" in params.message
        return types.ElicitResult(action="accept", content={"choice": next(choices)})

    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, confirm)
        reply = await session.call_tool("aica_import_archive", arguments)
        assert not reply.is_error and reply.structured_content["imported"] is False
        assert not (project / "restored").exists()
        reply = await session.call_tool("aica_import_archive", arguments)
        assert not reply.is_error, reply
        assert (project / "restored" / "aica.json").exists()
        assert not running.recorder.document["sessions"]
        denied = await session.call_tool("aica_start_session", {"request_id": "not-inherited", "record": True})
        assert denied.is_error


def test_explicit_no_cli_command_still_runs_without_aica_session(running):
    project = running.recorder.project
    client = RecorderClient(project)
    reply = run_command(client, [sys.executable, "-c", 'from pathlib import Path; Path("normal.txt").write_text("NORMAL_UNRECORDED_WORK")'], False)
    assert reply["executed"] and not reply["recording"] and reply["session_id"] is None
    assert (project / "normal.txt").read_text() == "NORMAL_UNRECORDED_WORK"
    assert not running.recorder.document["sessions"]
    assert_absent(running.recorder.directory, "NORMAL_UNRECORDED_WORK")


@pytest.mark.parametrize("action,choice", [("accept", "Yes"), ("accept", "No"), ("decline", None), ("cancel", None)])
async def test_native_hook_wire_output_uses_host_fields_and_preserves_business_status(running, action, choice):
    from jsonschema import Draft202012Validator

    async def confirm(context, params):
        return types.ElicitResult(action=action, content={"choice": choice} if choice else None)

    project = running.recorder.project
    host_output = {"type": "object", "additionalProperties": False, "required": ["systemMessage"],
                   "properties": {"systemMessage": {"type": "string"}}}
    validator = Draft202012Validator(host_output)
    assert not validator.is_valid({"status": "success", "recording": False, "systemMessage": "AiCA"})
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, confirm)
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "wire-host", "turn_id": "wire-turn",
                        "hook_event_name": "UserPromptSubmit", "prompt": "HOOK_WIRE_SYNTHETIC_PRIVATE"}})
        arguments = {"host_session_id": "wire-host", "turn_id": "wire-turn"}
        reply = await session.call_tool("aica_confirm_turn", {**arguments, "hook_output": True})
        assert not reply.is_error
        assert validator.is_valid(reply.structured_content)
        assert loads(reply.content[0].text) == reply.structured_content
        assert reply.meta["aica"]["status"] == "success"
        assert reply.meta["aica"]["recording"] is (choice == "Yes")
        assert "session_token" not in dumps(reply.model_dump(by_alias=True))
        ordinary = await session.call_tool("aica_confirm_turn", arguments)
        assert ordinary.structured_content["status"] == "success"
        assert ordinary.structured_content["recording"] is (choice == "Yes")
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "wire-host", "turn_id": "wire-turn",
                        "hook_event_name": "Stop"}})
    if choice != "Yes":
        running.recorder.flush()
        assert_absent(project, "HOOK_WIRE_SYNTHETIC_PRIVATE")


async def test_native_hook_error_and_missing_submission_do_not_emit_business_keys(running):
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, running.recorder.project)
        missing = await session.call_tool("aica_confirm_turn", {"host_session_id": "missing", "turn_id": "missing", "hook_output": True})
        assert not missing.is_error
        assert set(missing.structured_content) == {"systemMessage"}
        assert missing.meta["aica"]["state"] == "unavailable"
        invalid = await session.call_tool("aica_confirm_turn", {"hook_output": True})
        assert invalid.is_error
        assert set(invalid.structured_content) == {"systemMessage"}
        assert invalid.meta["aica"]["status"] == "error"
        assert not running.recorder.document["sessions"]


@pytest.mark.parametrize("choice,action,reason", [(True, "choose", "browser_accepted"), (False, "choose", "browser_declined"),
                                                 (False, "dismiss", "browser_cancelled"), (False, "timeout", "browser_expired")])
async def test_browser_mode_opens_bound_confirmation_instead_of_native_auto_decline(running, choice, action, reason):
    native_questions = []

    async def native_decline(context, params):
        native_questions.append(params.message)
        return types.ElicitResult(action="decline")

    project = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, native_decline)
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "browser-host", "turn_id": "browser-turn",
                        "hook_event_name": "UserPromptSubmit", "prompt": "BROWSER_ROUTE_SYNTHETIC_PRIVATE"}})
        arguments = {"host_session_id": "browser-host", "turn_id": "browser-turn", "hook_output": True, "confirmation": "browser"}
        waiting = asyncio.create_task(session.call_tool("aica_confirm_turn", arguments))
        for attempt in range(100):
            if running.approvals.requests:
                break
            await asyncio.sleep(0.02)
        assert running.approvals.requests and not waiting.done()
        assert not native_questions
        request = next(iter(running.approvals.requests.values()))
        assert request["state"] == "pending"
        origin = running.approvals.origin
        with httpx.Client(trust_env=False) as transport:
            response = transport.post(origin + "/approval/decision", headers={"Origin": origin},
                                      json={"nonce": request["nonce"], "choice": choice, "action": action})
        assert response.json()["status"] == "success"
        result = await waiting
        assert not result.is_error
        assert set(result.structured_content) == {"systemMessage"}
        assert result.meta["aica"]["recording"] is choice
        assert result.meta["aica"]["confirmation_reason"] == reason
        assert not native_questions
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "browser-host", "turn_id": "browser-turn",
                        "hook_event_name": "Stop"}})
    if not choice:
        running.recorder.flush()
        assert_absent(project, "BROWSER_ROUTE_SYNTHETIC_PRIVATE")


@pytest.mark.parametrize("answer,recording", [("Yes", True), ("No", False), ("decline", True), ("cancel", True), ("unavailable", True), ("timeout", True)])
async def test_native_default_yes_policy_records_only_expected_turns(gate, monkeypatch, answer, recording):
    bridge = AgentConsent(gate.recorder.project, default_yes=True)
    bridge.bridge_id = gate.attach()["bridge_id"]
    seen = []

    async def control(name, arguments=None, timeout=5):
        result = getattr(gate, name)(**arguments)
        return result

    async def respond(message, schema, **kwargs):
        seen.append(schema)
        if answer == "unavailable":
            raise RuntimeError("No native UI")
        if answer == "timeout":
            raise TimeoutError
        reply = SimpleNamespace(action="accept", content={"choice": answer}) if answer in {"Yes", "No"} else SimpleNamespace(action=answer, content=None)
        return reply

    def browser(*args):
        pytest.fail("Default Yes must never open the browser")

    monkeypatch.setattr(bridge, "control", control)
    monkeypatch.setattr("aica_mcp.hosts.browser_question", browser)
    hook(gate, "UserPromptSubmit", prompt="DEFAULT_POLICY_TEST_CONTENT")
    context = SimpleNamespace(session=SimpleNamespace(elicit_form=respond), request_id=1)
    result = await bridge.confirm(context, "host-1", "turn-1", "native-default-yes")
    assert result["recording"] is recording
    assert seen[0]["properties"]["choice"]["default"] == "Yes"
    if answer not in {"Yes", "No"}:
        assert result["confirmation_reason"].startswith("default_yes_")
        assert "默认 Yes" in result["systemMessage"]
    hook(gate, "Stop")
    gate.recorder.flush()
    assert len(gate.recorder.document["sessions"]) == int(recording)
    if not recording:
        assert_absent(gate.recorder.project, "DEFAULT_POLICY_TEST_CONTENT")
    bridge.confirmation = "native-default-yes"
    if answer != "Yes":
        assert not await bridge.ask(context, "Independent artifact/import approval")


async def test_model_cannot_enable_default_yes_in_strict_server(gate):
    bridge = AgentConsent(gate.recorder.project)
    with pytest.raises(ProtocolError, match="Enable --default-yes"):
        await bridge.confirm(None, "host-1", "turn-1", "native-default-yes")
    assert not gate.recorder.document["sessions"]


def test_native_default_yes_configuration_is_explicit_and_limited():
    from aica_mcp.hosts import config_bundle

    result = config_bundle("codex", "native-default-yes")
    assert "--default-yes" in result["mcp"]["mcp_servers"]["aica"]["args"]
    hooks = result["hooks"]["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert hooks[1]["input"]["confirmation"] == "native-default-yes"
    with pytest.raises(ProtocolError):
        config_bundle("cursor", "native-default-yes")


@pytest.mark.parametrize("choice", ["Yes", "No", None])
async def test_default_yes_over_real_stdio_hook(running, choice):
    asked = []

    async def confirm(context, params):
        asked.append(params)
        assert params.requested_schema["properties"]["choice"]["default"] == "Yes"
        reply = types.ElicitResult(action="accept", content={"choice": choice}) if choice else types.ElicitResult(action="decline")
        return reply

    project = running.recorder.project
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, confirm, default_yes=True)
        control_request(project, "hook", {"event": {"cwd": str(project), "session_id": "default-host", "turn_id": "default-turn",
                        "hook_event_name": "UserPromptSubmit", "prompt": "DEFAULT_STDIO_MARKER"}})
        result = await session.call_tool("aica_confirm_turn", {"host_session_id": "default-host", "turn_id": "default-turn",
                                         "hook_output": True, "confirmation": "native-default-yes"})
        assert not result.is_error, result
        assert result.meta["aica"]["recording"] is (choice != "No")
        assert set(result.structured_content) == {"systemMessage"}
        assert len(asked) == 1
    running.recorder.flush()
    if choice == "No":
        assert_absent(project, "DEFAULT_STDIO_MARKER")
    else:
        assert len(running.recorder.document["sessions"]) == 1


def prepare_conversation(gate, host="host-1", turn="turn-1", prompt="CONVERSATION_PRIVATE_MARKER"):
    bridge = gate.attach()["bridge_id"]
    hook(gate, "UserPromptSubmit", host, turn, prompt=prompt)
    gate.prepare_conversation(bridge, host, turn)
    return bridge


@pytest.mark.parametrize("choice", ["Yes", "No", " yes ", "NO"])
def test_conversation_reply_moves_only_its_pending_request(gate, choice):
    bridge = prepare_conversation(gate)
    result = hook(gate, "UserPromptSubmit", turn="answer-turn", prompt=choice)
    assert result["recording"] is (choice.strip().lower() == "yes")
    assert gate.pending(bridge, "host-1", "turn-1")["state"] == "confirmation_answered"
    assert gate.active(bridge)["turn"] == ({"host_session_id": "host-1", "turn_id": "answer-turn"} if result["recording"] else None)
    if result["recording"]:
        assert "CONVERSATION_PRIVATE_MARKER" in dumps(gate.recorder.get_events())
        assert hook(gate, "Stop", turn="answer-turn")["export_requested"] is True
    else:
        hook(gate, "Stop", turn="answer-turn", last_assistant_message="CONVERSATION_PRIVATE_MARKER")
        gate.recorder.flush()
        assert_absent(gate.recorder.project, "CONVERSATION_PRIVATE_MARKER")


@pytest.mark.parametrize("boundary", ["other_session", "embedded_reply", "detached", "new_request"])
def test_conversation_reply_never_inherits_invalid_or_other_session_grants(gate, boundary):
    bridge = prepare_conversation(gate)
    if boundary == "detached":
        gate.detach(bridge)
    host = "different-host" if boundary == "other_session" else "host-1"
    prompt = "Please say No" if boundary == "embedded_reply" else "another task" if boundary == "new_request" else "Yes"
    result = hook(gate, "UserPromptSubmit", host=host, turn="next-turn", prompt=prompt)
    assert result["state"] == "pending" and not result["recording"]
    assert not gate.recorder.document["sessions"]


def test_conversation_wait_has_no_default_or_reply_deadline(gate):
    bridge = prepare_conversation(gate)
    turn = gate.turns[("codex", "host-1", "turn-1")]
    turn["created"] -= 3600
    gate.expire()
    assert gate.pending(bridge, "host-1", "turn-1")["state"] == "pending"
    assert not gate.recorder.document["sessions"]
    result = hook(gate, "UserPromptSubmit", turn="late-turn", prompt="No")
    assert not result["recording"] and "No" in result["systemMessage"]


@pytest.mark.parametrize("choice", ["Yes", "No", None])
async def test_conversation_blocks_then_resumes_and_exports_via_public_mcp(running, choice):
    from aica_mcp.consent import CONVERSATION_QUESTION
    from aica_mcp.transfer import read_portable_json

    async def select(context, params):
        assert "result.png" in params.message
        return types.ElicitResult(action="accept", content={"choice": "Yes"})

    project = running.recorder.project
    exports = project / "exports"
    exports.mkdir()
    target = exports / "aica.json"
    target.write_text("PREVIOUS_EXPORT_UNCHANGED")
    async with AsyncExitStack() as stack:
        session = await native_connection(stack, project, select)
        event = {"cwd": str(project), "session_id": "chat-host", "turn_id": "chat-turn",
                 "hook_event_name": "UserPromptSubmit", "prompt": "CHAT_ORIGINAL_PRIVATE_MARKER"}
        command = [sys.executable, "-m", "aica_mcp", "agent-hook", "--client", "codex", "--confirmation", "conversation"]
        submitted = subprocess.run(command, input=dumps(event), capture_output=True, text=True, check=True, timeout=5)
        output = loads(submitted.stdout)
        assert output["decision"] == "block" and output["reason"] == CONVERSATION_QUESTION
        assert set(output) == {"decision", "reason"}
        assert not running.recorder.document["sessions"]
        turn = running.turns.turns[("codex", "chat-host", "chat-turn")]
        turn["created"] -= 3600
        control_request(project, "hook", {"event": {**event, "hook_event_name": "Stop"}})
        assert turn["state"] == "pending"
        assert target.read_text() == "PREVIOUS_EXPORT_UNCHANGED"
        if choice is None:
            running.recorder.flush()
            assert_absent(project, "CHAT_ORIGINAL_PRIVATE_MARKER")
            return
        event.update(turn_id="chat-answer", prompt=choice)
        answered = subprocess.run(command, input=dumps(event), capture_output=True, text=True, check=True, timeout=5)
        output = loads(answered.stdout)
        assert "decision" not in output and choice in output["systemMessage"]
        assert "CHAT_ORIGINAL_PRIVATE_MARKER" in output["hookSpecificOutput"]["additionalContext"]
        if choice == "Yes":
            (project / "result.png").write_bytes(b"SELECTED_IMAGE_BYTES")
            reply = await session.call_tool("aica_select_turn_artifacts", {"paths": ["result.png"]})
            assert not reply.is_error, reply
            assert reply.structured_content["selected"] is True
            artifact = reply.structured_content["artifacts"][0]
            assert (project / artifact["uri"]).read_bytes() == (project / "result.png").read_bytes()
            assert artifact["sha256"] == hashlib.sha256((project / "result.png").read_bytes()).hexdigest()
        stopped = control_request(project, "hook", {"event": {**event, "hook_event_name": "Stop",
                                  "last_assistant_message": "CHAT_FINAL_PRIVATE_MARKER"}}, timeout=60)
        if choice == "No":
            assert "export" not in stopped
            assert target.read_text() == "PREVIOUS_EXPORT_UNCHANGED"
            running.recorder.flush()
            assert_absent(project, "CHAT_ORIGINAL_PRIVATE_MARKER")
            assert_absent(project, "CHAT_FINAL_PRIVATE_MARKER")
        else:
            assert stopped["export"]["status"] == "success" and stopped["export"]["image_count"] == 1
            archive, objects, raw = read_portable_json(target)
            assert b"SELECTED_IMAGE_BYTES" in objects.values()
            assert len(archive["sessions"]) == 1
            assert "CHAT_ORIGINAL_PRIVATE_MARKER" in dumps(archive) + dumps(running.recorder.get_events())


def test_conversation_export_failure_preserves_previous_export(running, monkeypatch):
    prepare_conversation(running.turns)
    hook(running.turns, "UserPromptSubmit", turn="answer-turn", prompt="Yes")
    target = running.recorder.project / "exports" / "aica.json"
    target.parent.mkdir()
    target.write_text("PREVIOUS_BYTES")

    def fail(**kwargs):
        raise OSError("export disk error")

    monkeypatch.setattr(running, "export_archive", fail)
    result = running.control_dispatch("hook", {"event": {"cwd": str(running.recorder.project), "session_id": "host-1", "turn_id": "answer-turn",
                                      "hook_event_name": "Stop", "last_assistant_message": "SAVED_REPLY"}})
    assert result["export"]["status"] == "error"
    assert "导出失败" in result["systemMessage"] and target.read_text() == "PREVIOUS_BYTES"
    assert "SAVED_REPLY" in dumps(running.recorder.get_events())


def test_conversation_configuration_has_only_blocking_command_hooks():
    from aica_mcp.hosts import config_bundle

    bundle = config_bundle("codex", "conversation")
    assert "--default-yes" not in bundle["mcp"]["mcp_servers"]["aica"]["args"]
    commands = bundle["hooks"]["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert len(commands) == 1 and commands[0]["type"] == "command"
    assert "--confirmation conversation" in commands[0]["command"]
    assert commands[0]["timeout"] == 10
    assert bundle["hooks"]["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 600
    with pytest.raises(ProtocolError):
        config_bundle("claude-code", "conversation")


async def test_conversation_capability_check_preserves_policy_on_old_coordinator(tmp_path, monkeypatch):
    from aica_mcp.server import serve_stdio

    class OldClient:
        def __init__(self, project, autostart=True):
            self.project = project

        def request(self, name):
            return {"status": "success", "turn_recording_version": 2}

    monkeypatch.setattr("aica_mcp.server.RecorderClient", OldClient)
    with pytest.raises(ProtocolError) as error:
        await serve_stdio(tmp_path, True, "codex", True)
    assert error.value.code == "coordinator_upgrade_required"
    assert not (tmp_path / ".aica" / "recording-policy.json").exists()


def test_strict_yes_exports_at_normal_stop(gate):
    begin(gate)
    result = hook(gate, "Stop")
    assert result["export_requested"]
    assert not (gate.recorder.project / "exports").exists()


def test_conversation_same_turn_reply_and_strict_pending_yes_are_separate(gate):
    bridge = prepare_conversation(gate)
    reply = hook(gate, "UserPromptSubmit", prompt="Yes")
    assert reply["recording"] and gate.active(bridge)["turn"]["turn_id"] == "turn-1"
    hook(gate, "Stop")
    hook(gate, "UserPromptSubmit", turn="strict-task", prompt="STRICT_PRIVATE")
    gate.pending(bridge, "host-1", "strict-task")
    result = hook(gate, "UserPromptSubmit", turn="strict-answer", prompt="Yes")
    assert result["state"] == "pending" and not result["recording"]
    assert len(gate.recorder.document["sessions"]) == 1


def test_conversation_missing_turn_identity_never_opens_browser_or_submits(gate, monkeypatch):
    import io

    from aica_mcp.hosts import run_hook

    event = {"cwd": str(gate.recorder.project), "session_id": "host-1", "hook_event_name": "UserPromptSubmit", "prompt": "MISSING_TURN_PRIVATE"}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))

    def unexpected(*args, **kwargs):
        pytest.fail("Missing turn identity must not call control RPC or open a browser")

    monkeypatch.setattr("aica_mcp.hosts.control_request", unexpected)
    result = run_hook("codex", confirmation="conversation")
    assert result["decision"] == "block" and "原任务已暂停" in result["reason"]
    assert "systemMessage" not in result
    assert not gate.turns and not gate.recorder.document["sessions"]


def test_conversation_prepare_rejects_prompt_only_coverage(gate):
    bridge = gate.attach()["bridge_id"]
    hook(gate, "UserPromptSubmit", prompt="PROMPT_ONLY_PRIVATE", coverage="prompt_only")
    with pytest.raises(ProtocolError, match="stable Host turn"):
        gate.prepare_conversation(bridge, "host-1", "turn-1")
    assert not gate.recorder.document["sessions"]


@pytest.mark.parametrize("boundary", ["ambiguous_bridge", "different_bridge", "wrong_workspace", "wrong_client"])
def test_blocking_conversation_requires_unique_matching_connection(gate, boundary):
    bridge = gate.attach(owner_pid=100)["bridge_id"]
    event = {"cwd": str(gate.recorder.project), "session_id": "host-1", "turn_id": "turn-1",
             "hook_event_name": "UserPromptSubmit", "prompt": "PRIVATE_ORIGINAL"}
    if boundary == "ambiguous_bridge":
        gate.attach(owner_pid=100)
    elif boundary == "different_bridge":
        gate.hook(event)
        gate.prepare_conversation(bridge, "host-1", "turn-1")
        gate.bridge_profiles[bridge]["owner_pid"] = 200
        gate.attach(owner_pid=100)
        event.update(turn_id="answer", prompt="Yes")
    elif boundary == "wrong_workspace":
        event["cwd"] = str(gate.recorder.project.parent)
    else:
        event["client"] = "claude-code"
    with pytest.raises(ProtocolError):
        gate.conversation_submit(event, [100])
    assert not gate.recorder.document["sessions"]


def test_pending_conversation_interrupt_and_detach_discard_the_original(gate):
    for action in ("Interrupt", "detach"):
        bridge = prepare_conversation(gate, turn=action)
        if action == "detach":
            gate.detach(bridge)
        else:
            hook(gate, action, turn=action)
        result = hook(gate, "UserPromptSubmit", turn=action + "-answer", prompt="Yes")
        assert not result["recording"] and "resume_prompt" not in result
    assert not gate.recorder.document["sessions"]


async def test_conversation_attachment_both_confirmation_channels_unavailable_is_an_error(gate, monkeypatch):
    bridge = prepare_conversation(gate)
    hook(gate, "UserPromptSubmit", turn="answer", prompt="Yes")
    consent = AgentConsent(gate.recorder.project)
    consent.bridge_id = bridge

    async def control(name, arguments=None, timeout=5):
        assert name in {"active", "pending"}
        result = getattr(gate, name)(**arguments)
        return result

    async def unavailable(*args, **kwargs):
        raise RuntimeError("UI unavailable")

    def unexpected(*args):
        return {"choice": False, "state": "unavailable"}

    monkeypatch.setattr(consent, "control", control)
    monkeypatch.setattr("aica_mcp.hosts.browser_question", unexpected)
    context = SimpleNamespace(session=SimpleNamespace(elicit_form=unavailable), request_id=1)
    result = await consent.select(context, ["picture.png"])
    assert result["status"] == "error" and result["selected"] is False
    assert result["confirmation_reason"] == "browser_unavailable"
    assert not gate.recorder.document["artifacts"]


def test_conversation_export_without_artifacts_reports_the_gap(running):
    prepare_conversation(running.turns)
    hook(running.turns, "UserPromptSubmit", turn="answer-turn", prompt="Yes")
    result = running.control_dispatch("hook", {"event": {"cwd": str(running.recorder.project), "session_id": "host-1",
                                      "turn_id": "answer-turn", "hook_event_name": "Stop"}})
    assert result["export"]["image_count"] == 0
    assert "未登记图片" in result["systemMessage"]


@pytest.mark.parametrize("restart", [False, True])
async def test_heartbeat_recovers_without_restoring_recording_consent(gate, monkeypatch, restart):
    bridge, grant = begin(gate)
    assert grant["recording"]
    consent = AgentConsent(gate.recorder.project)
    consent.bridge_id = bridge
    consent.active_turn = {"host_session_id": "host-1", "turn_id": "turn-1"}
    recovered = asyncio.Event()
    calls = []
    failed = False
    attach_attempts = 0

    async def pause(seconds):
        await asyncio.sleep(0)

    async def control(name, arguments=None, timeout=5):
        nonlocal failed, attach_attempts
        calls.append(name)
        if name == "heartbeat" and not failed:
            failed = True
            if restart:
                gate.detach(bridge)
                raise ProtocolError("Reconnect the consent bridge", "bridge_expired")
            raise httpx.ConnectError("Temporary control connection failure")
        if name == "attach":
            attach_attempts += 1
            assert consent.active_turn is None and consent.bridge_id is None
            if attach_attempts == 1:
                raise httpx.ConnectError("Coordinator not ready yet")
        result = getattr(gate, name)(**(arguments or {}))
        if name == "heartbeat" and consent.bridge_id != bridge:
            recovered.set()
        return result

    monkeypatch.setattr(consent, "control", control)
    monkeypatch.setattr("aica_mcp.consent.anyio", SimpleNamespace(sleep=pause))
    task = asyncio.create_task(consent.heartbeat())
    try:
        await asyncio.wait_for(recovered.wait(), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls.count("attach") == 2 and "detach" in calls
    assert consent.bridge_id != bridge and consent.bridge_id in gate.bridges
    assert gate.active(consent.bridge_id)["turn"] is None
    assert gate.turns[("codex", "host-1", "turn-1")]["state"] == "connection_lost"
    assert len(gate.recorder.document["sessions"]) == 1


@pytest.mark.parametrize("failures", [1, 100])
def test_conversation_waits_briefly_for_bridge_startup(gate, monkeypatch, failures):
    import io

    from aica_mcp.hosts import run_hook

    event = {"cwd": str(gate.recorder.project), "session_id": "host-1", "turn_id": "turn-1",
             "hook_event_name": "UserPromptSubmit", "prompt": "STARTUP_PENDING"}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))
    clock = [0]
    calls = []

    def pause(seconds):
        clock[0] += seconds

    def control(project, method, arguments, timeout=5):
        calls.append(method)
        if len(calls) <= failures:
            return {"status": "error", "code": "bridge_ambiguous", "message": "No unique connected MCP bridge"}
        return gate.conversation_submit(arguments["event"], arguments["owner_pids"])

    gate.attach(owner_pid=100)
    monkeypatch.setattr("aica_mcp.hosts.owner_pids", lambda: [100])
    monkeypatch.setattr("aica_mcp.hosts.control_request", control)
    monkeypatch.setattr("aica_mcp.hosts.time", SimpleNamespace(monotonic=lambda: clock[0], sleep=pause))
    result = run_hook("codex", confirmation="conversation")
    assert result["decision"] == "block" and len(calls) > 1
    assert clock[0] <= 3
    assert not gate.recorder.document["sessions"]
    if failures == 1:
        assert "Yes" in result["reason"] and len(gate.turns) == 1
    else:
        assert "bridge_ambiguous" in result["reason"] and not gate.turns


def test_conversation_transport_failure_is_not_replayed(gate, monkeypatch):
    import io

    from aica_mcp.hosts import run_hook

    event = {"cwd": str(gate.recorder.project), "session_id": "host-1", "turn_id": "turn-1",
             "hook_event_name": "UserPromptSubmit", "prompt": "DO_NOT_REPLAY"}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))
    calls = []

    def control(*args, **kwargs):
        calls.append(args)
        assert kwargs.get("timeout", 0) >= 5
        raise httpx.ReadTimeout("Reply lost")

    monkeypatch.setattr("aica_mcp.hosts.owner_pids", lambda: [100])
    monkeypatch.setattr("aica_mcp.hosts.control_request", control)
    result = run_hook("codex", confirmation="conversation")
    assert result["decision"] == "block" and len(calls) == 1
    assert "ReadTimeout" in result["reason"]
    assert not gate.turns and not gate.recorder.document["sessions"]


async def test_live_bridge_recovers_after_coordinator_restart(tmp_path, monkeypatch):
    import anyio

    enable_turn_policy(tmp_path)
    consent = AgentConsent(tmp_path)
    instances = []
    task = None

    async def pause(seconds):
        await asyncio.sleep(0.01)

    async def launch():
        coordinator = Coordinator(tmp_path)
        thread = threading.Thread(target=coordinator.serve, daemon=True)
        instances.append((coordinator, thread))
        thread.start()
        async with asyncio.timeout(3):
            while not (tmp_path / ".aica" / "control.json").exists():
                await asyncio.sleep(0.01)
        return coordinator, thread

    monkeypatch.setattr("aica_mcp.consent.anyio", SimpleNamespace(sleep=pause, to_thread=anyio.to_thread))
    try:
        original, thread = await launch()
        await consent.start()
        previous_bridge = consent.bridge_id
        begin(original.turns, bridge=previous_bridge)
        task = asyncio.create_task(consent.heartbeat())
        await asyncio.to_thread(original.server.shutdown)
        await asyncio.to_thread(thread.join, 3)
        assert not thread.is_alive()
        replacement, replacement_thread = await launch()
        async with asyncio.timeout(3):
            while consent.bridge_id is None or consent.bridge_id == previous_bridge:
                await asyncio.sleep(0.01)
        assert consent.bridge_id in replacement.turns.bridges
        assert previous_bridge not in replacement.turns.bridges
        assert replacement.turns.active(consent.bridge_id)["turn"] is None
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for coordinator, thread in instances:
            if thread.is_alive():
                await asyncio.to_thread(coordinator.server.shutdown)
                await asyncio.to_thread(thread.join, 3)
                assert not thread.is_alive()


@pytest.mark.parametrize("missing", ["conversation_submit", "attach", "capture"])
def test_version_numbers_do_not_hide_missing_control_interfaces(running, monkeypatch, missing):
    from aica_mcp.server import require_turn_coordinator
    from aica_schema.recording import recording_contract

    status = running.dispatch("status", {})
    require_turn_coordinator(status)
    assert status["conversation_recording_version"] == 2
    contract = recording_contract()
    contract["turn_control"].pop(missing)
    monkeypatch.setattr("aica_mcp.coordinator.recording_contract", lambda: contract)
    status = running.dispatch("status", {})
    assert missing not in status["turn_control_methods"]
    with pytest.raises(ProtocolError) as error:
        require_turn_coordinator(status)
    assert error.value.code == "coordinator_upgrade_required"
    assert not running.recorder.document["sessions"]


def test_unknown_confirmation_method_reports_one_actionable_error(gate, monkeypatch):
    import io

    from aica_mcp.hosts import run_hook

    event = {"cwd": str(gate.recorder.project), "session_id": "host-1", "turn_id": "turn-1",
             "hook_event_name": "UserPromptSubmit", "prompt": "UNSUPPORTED_METHOD"}
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))
    monkeypatch.setattr("aica_mcp.hosts.owner_pids", lambda: [100])
    calls = []

    def control(*args, **kwargs):
        calls.append(args)
        return {"status": "error", "code": "unknown_method", "message": "Unknown turn control operation"}

    monkeypatch.setattr("aica_mcp.hosts.control_request", control)
    result = run_hook("codex", confirmation="conversation")
    assert set(result) == {"decision", "reason"} and result["decision"] == "block"
    assert "unknown_method" in result["reason"] and "旧协调器" in result["reason"]
    assert len(calls) == 1 and not gate.recorder.document["sessions"]


def test_conversation_hook_waits_for_local_coordinator_write(tmp_path, monkeypatch):
    import io

    from aica_mcp.hosts import run_hook

    enable_turn_policy(tmp_path)
    coordinator = Coordinator(tmp_path)
    submit = coordinator.turns.conversation_submit

    def slow_submit(event, owner_pids):
        time.sleep(1.2)
        result = submit(event, owner_pids)
        return result

    monkeypatch.setattr(coordinator.turns, 'conversation_submit', slow_submit)
    coordinator.turns.attach(owner_pid=100)
    event = {'cwd': str(tmp_path), 'session_id': 'fixture-host', 'turn_id': 'fixture-turn',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'Fixture prompt'}
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))
    monkeypatch.setattr('aica_mcp.hosts.owner_pids', lambda: [100])
    thread = threading.Thread(target=coordinator.serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not (tmp_path / '.aica/control.json').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        result = run_hook('codex', confirmation='conversation')
        assert result['decision'] == 'block' and 'Yes' in result['reason']
        assert 'ReadTimeout' not in result['reason']
        assert not coordinator.recorder.document['sessions']
    finally:
        coordinator.server.shutdown()
        thread.join(timeout=5)


def test_control_dispatch_rejects_missing_runtime_handler(tmp_path, monkeypatch):
    coordinator = Coordinator(tmp_path)
    enable_turn_policy(tmp_path)
    monkeypatch.setattr(coordinator, 'control_methods', lambda: {})
    try:
        with pytest.raises(ProtocolError) as error:
            coordinator.control_dispatch('conversation_submit', {'event': {}, 'owner_pids': [100]})
        assert error.value.code == 'unknown_method'
    finally:
        coordinator.platform.close()
        coordinator.recorder.close()


def test_host_selected_bridge_handles_shared_daemon_and_unrelated_cli_parent(gate):
    wrong = gate.attach(owner_pid=900)['bridge_id']
    correct = gate.attach(owner_pid=900)['bridge_id']
    event = {'cwd': str(gate.recorder.project), 'session_id': 'host-1', 'turn_id': 'turn-1',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE_ROUTED_TASK'}
    result = gate.conversation_submit(event, [100], bridge_id=correct)
    assert result['decision'] == 'block' and not gate.recorder.document['sessions']
    assert gate.turns[('codex', 'host-1', 'turn-1')]['bridge_id'] == correct
    with pytest.raises(ProtocolError):
        gate.prepare_conversation(wrong, 'host-1', 'turn-1')
    event.update(turn_id='answer', prompt='Yes')
    assert gate.conversation_submit(event, [100], bridge_id=correct)['recording']
    assert gate.turns[('codex', 'host-1', 'answer')]['bridge_id'] == correct


def test_host_bridge_selection_does_not_accept_other_client_or_expired_id(gate):
    event = {'cwd': str(gate.recorder.project), 'session_id': 'host-1', 'turn_id': 'turn-1',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE_ROUTED_TASK'}
    other = gate.attach(client='claude-code', owner_pid=100)['bridge_id']
    for bridge in (other, new_id()):
        with pytest.raises(ProtocolError) as error:
            gate.conversation_submit(event, [100], bridge_id=bridge)
        assert error.value.code == 'bridge_expired'
    assert not gate.turns and not gate.recorder.document['sessions']


def test_conversation_hook_uses_verified_host_connection_before_pid_matching(gate, monkeypatch):
    import io

    from aica_mcp.hosts import run_hook

    gate.attach(owner_pid=900)
    bridge = gate.attach(owner_pid=900)['bridge_id']
    event = {'cwd': str(gate.recorder.project), 'session_id': new_id(), 'turn_id': 'turn-1',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE_ROUTED_TASK'}
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(dumps(event).encode())))
    monkeypatch.setattr('aica_mcp.hosts.owner_pids', lambda: [100])
    monkeypatch.setattr('aica_mcp.hosts.discover_bridge', lambda *args: bridge)
    monkeypatch.setattr('aica_mcp.hosts.control_request', lambda project, method, arguments, timeout: gate.conversation_submit(**arguments))
    result = run_hook('codex', confirmation='conversation')
    assert result['decision'] == 'block' and 'Yes' in result['reason']
    assert not gate.recorder.document['sessions']


def test_authenticated_control_dispatch_accepts_host_routed_connection(running):
    running.turns.attach(owner_pid=900)
    correct = running.turns.attach(owner_pid=900)['bridge_id']
    event = {'cwd': str(running.recorder.project), 'session_id': 'host-1', 'turn_id': 'turn-1',
             'hook_event_name': 'UserPromptSubmit', 'prompt': 'PRIVATE_ROUTED_TASK'}
    result = control_request(running.recorder.project, 'conversation_submit',
                             {'event': event, 'owner_pids': [100], 'bridge_id': correct})
    assert result['status'] == 'success' and result['decision'] == 'block'
    assert not running.recorder.document['sessions']
