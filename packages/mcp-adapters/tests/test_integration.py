import asyncio
import base64
import copy
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest
from aica_mcp.capture import checked, extend_graph, register_actor, run_command, start_source
from aica_mcp.client import RecorderClient, RecordingSource
from aica_mcp.extraction import extract_git, extract_log, extract_steps, parse_agent_log
from aica_mcp.transfer import read_portable_json, verify_bundle
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, utc_now, validate_archive
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.fixture
def client(tmp_path):
    instance = RecorderClient(tmp_path)
    yield instance
    try:
        instance.load_endpoint()
        instance.request("shutdown")
    except (OSError, ValueError, httpx.HTTPError):
        if instance.endpoint:
            try:
                os.kill(instance.endpoint["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 5
    while (instance.directory / "endpoint.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)


def observed_command(client):
    argv = [sys.executable, "-c", 'from pathlib import Path; Path("result.txt").write_text("observed output")']
    started = utc_now()
    process = subprocess.run(argv, cwd=client.project, capture_output=True, text=True, check=True)
    step = {"label": "Create an observed file", "client": "integration-test", "started_at": started,
            "ended_at": utc_now(), "state": "succeeded", "inputs": {"argv": argv},
            "outputs": {"exit_code": process.returncode},
            "execution": {"kind": "command", "tool": "python3", "version": None, "inputs": {}, "params": {},
                          "outputs": ["file"], "runtime_ref": "local-observed@1", "argv": argv, "cwd": "."},
            "artifacts": [{"path": "result.txt", "mime_type": "text/plain"}]}
    return step


async def test_mcp_observation_creates_nodes_runs_and_artifact_links(client):
    step = observed_command(client)
    async with AsyncExitStack() as stack:
        session = await connect_mcp(stack, client.project)
        consent = (await session.call_tool("aica_start_session", {"request_id": new_id(), "record": True})).structured_content
        arguments = {"request_id": new_id(), "session_id": consent["session_id"],
                     "session_token": consent["session_token"], "step": step}
        reply = await session.call_tool("aica_record_step", arguments)
        assert not reply.is_error, reply
        recorded = reply.structured_content
        assert not recorded["executed"]
        assert (await session.call_tool("aica_record_step", arguments)).structured_content == recorded
        archive = (await session.call_tool("aica_get_archive", {})).structured_content["archive"]
        assert len(archive["graph"]["nodes"]) == len(archive["runs"]) == 1
        run = archive["runs"][0]
        assert run["state"] == "succeeded" and run["started_at"] == step["started_at"]
        assert archive["agents"][0]["client"] == "integration-test"
        artifact = next(item for item in archive["artifacts"] if item["id"] in recorded["artifact_ids"])
        assert artifact["producer_node_id"] == recorded["node_id"] and artifact["producer_run_id"] == run["id"]
        assert (client.project / artifact["uri"]).read_text() == "observed output"
        assert validate_archive(archive)["valid"]
        followup = observed_command(client)
        followup["depends_on"] = [recorded["node_id"]]
        reply = await session.call_tool("aica_record_step", {**arguments, "request_id": new_id(), "step": followup})
        assert not reply.is_error, reply
        archive = (await session.call_tool("aica_get_archive", {})).structured_content["archive"]
        assert len(archive["graph"]["nodes"]) == len(archive["runs"]) == 2
        assert len(archive["agents"]) == 1
        assert archive["graph"]["edges"][0]["source"] == recorded["node_id"]
        assert archive["graph"]["edges"][0]["target"] == reply.structured_content["node_id"]


async def test_mcp_import_reconnect_and_continue_preserves_images_and_requires_fresh_consent(client):
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC1kAAAAASUVORK5CYII=")
    (client.project / "picture.png").write_bytes(image)
    step = observed_command(client)
    step["model"] = {"provider": "integration-fixture", "name": "declared-model"}
    step["artifacts"].append({"path": "picture.png", "mime_type": "image/png"})
    target = client.project / "imported-project"
    try:
        async with AsyncExitStack() as stack:
            source = await connect_mcp(stack, client.project)
            assert "aica_import_archive" in {tool.name for tool in (await source.list_tools()).tools}
            consent = (await source.call_tool("aica_start_session", {"request_id": new_id(), "record": True})).structured_content
            recorded = await source.call_tool("aica_record_step", {"request_id": new_id(), "session_id": consent["session_id"],
                                              "session_token": consent["session_token"], "step": step})
            assert not recorded.is_error
            exported = await source.call_tool("aica_export_archive", {"portable": True})
            assert not exported.is_error
            before = (await source.call_tool("aica_get_archive", {})).structured_content["archive"]
            arguments = {"request_id": new_id(), "path": "exports/aica.json", "target": target.name, "confirm_import": True}
            reply = await source.call_tool("aica_import_archive", arguments)
            assert not reply.is_error and reply.structured_content["status"] == "success"
            assert not reply.structured_content["recording_database_present"]
            unchanged = (await source.call_tool("aica_get_archive", {})).structured_content["archive"]
            assert unchanged == before
            restored = await connect_mcp(stack, target)
            document = (await restored.call_tool("aica_get_archive", {})).structured_content["archive"]
            for name in ("graph", "variables", "agents", "harnesses", "sessions", "runs"):
                assert document[name] == before[name]
            assert document["snapshot"]["stream_id"] != before["snapshot"]["stream_id"]
            checked = await restored.call_tool("aica_check_references", {})
            assert not checked.is_error and checked.structured_content["all_references_verified"]
            denied = await restored.call_tool("aica_check_consent", {"session_id": consent["session_id"],
                                               "session_token": consent["session_token"], "event_type": "note.recorded"})
            assert denied.is_error
            fresh = (await restored.call_tool("aica_start_session", {"request_id": new_id(), "record": True})).structured_content
            followup = observed_command(RecorderClient(target, autostart=False))
            followup["depends_on"] = [recorded.structured_content["node_id"]]
            added = await restored.call_tool("aica_record_step", {"request_id": new_id(), "session_id": fresh["session_id"],
                                             "session_token": fresh["session_token"], "step": followup})
            assert not added.is_error
            continued = (await restored.call_tool("aica_get_archive", {})).structured_content["archive"]
            assert len(continued["graph"]["nodes"]) == 2 and len(continued["runs"]) == 2
            retry = await source.call_tool("aica_import_archive", arguments)
            assert not retry.is_error and retry.structured_content["already_imported"]
            assert (await restored.call_tool("aica_get_archive", {})).structured_content["archive"] == continued
            again = await restored.call_tool("aica_export_archive", {"portable": True})
            assert not again.is_error
            archive, objects, raw = read_portable_json(target / "exports/aica.json")
            assert image in objects.values() and raw and len(archive["runs"]) == 2
    finally:
        if (target / ".aica/endpoint.json").exists():
            RecorderClient(target, autostart=False).request("shutdown")


def test_step_retry_after_lost_completion_ack_does_not_duplicate_or_rerun(client, monkeypatch):
    step = observed_command(client)
    consent, source = start_source(client, True)
    source.close()
    arguments = {"request_id": new_id(), "session_id": consent["session_id"],
                 "session_token": consent["session_token"], "step": step}
    original = client.send
    lost = False

    def send(name, payload):
        nonlocal lost
        result = original(name, payload)
        if not lost and name == "aica_submit_event" and payload["event"]["type"] == "tool.finished":
            lost = True
            raise httpx.ReadError("Completion acknowledgement lost")
        return result

    monkeypatch.setattr(client, "send", send)
    with pytest.raises(httpx.ReadError):
        client.call("aica_record_step", arguments)
    (client.project / "result.txt").unlink()
    recorded = client.call("aica_record_step", arguments)
    assert not recorded["executed"]
    archive = client.call("aica_get_archive")["archive"]
    assert len(archive["runs"]) == len(archive["graph"]["nodes"]) == 1
    assert len([item for item in archive["artifacts"] if item["producer_run_id"] == recorded["run_id"]]) == 1
    changed = copy.deepcopy(arguments)
    changed["step"]["label"] = "Different content"
    with pytest.raises(ProtocolError, match="different content"):
        client.call("aica_record_step", changed)


def test_step_rejects_denied_consent_before_creating_a_plan(client):
    consent = client.call("aica_start_session", {"request_id": new_id(), "record": False})
    step = observed_command(client)
    with pytest.raises(ProtocolError, match="consent"):
        client.call("aica_record_step", {"request_id": new_id(), "session_id": consent["session_id"],
                                         "session_token": "denied-token-never-authorized", "step": step})
    assert not (client.directory / "outboxes" / "steps").exists()
    assert not client.call("aica_get_archive")["archive"]["graph"]["nodes"]


def test_manifest_recovers_evidence_and_outputs_without_inventing_runs(client):
    (client.project / "story.txt").write_text("A selected story outline")
    (client.project / "result.txt").write_text("An existing result")
    manifest = {"nodes": [{"id": "story", "kind": "prompt", "label": "Story", "branch_id": "main",
                            "provenance": {"mode": "extracted", "evidence": [{"source": "story.txt"}],
                                           "missing": ["original_tool_timestamps", "original_creator"]}}], "edges": [],
                "files": [{"path": "story.txt", "mime_type": "text/plain"},
                          {"path": "result.txt", "mime_type": "text/plain", "producer_node_id": "story"}]}
    (client.project / "steps.json").write_text(dumps(manifest))
    result = extract_steps(client, "steps.json", True)
    archive = client.call("aica_get_archive")["archive"]
    assert result["added_node_count"] == 1 and not archive["runs"]
    assert archive["graph"]["nodes"][0]["provenance"]["mode"] == "extracted"
    assert archive["graph"]["nodes"][0]["provenance"]["evidence"][0]["artifact_id"]
    assert next(item for item in archive["artifacts"] if item["id"] in result["artifact_ids"])["producer_node_id"] == "story"
    assert extract_steps(client, "steps.json", True)["added_node_count"] == 0
    assert len(client.call("aica_get_archive")["archive"]["graph"]["nodes"]) == 1
    manifest["nodes"][0]["provenance"]["mode"] = "recorded"
    (client.project / "steps.json").write_text(dumps(manifest))
    with pytest.raises(ProtocolError, match="Historical nodes"):
        extract_steps(client, "steps.json", True)


def test_real_command_produces_verifiable_archive_and_artifacts(client):
    result = run_command(client, [sys.executable, "-c", 'print("actual execution")'], True)
    assert result["exit_code"] == 0
    archive = loads((client.project / "aica.json").read_text())
    assert validate_archive(archive)["valid"]
    assert archive["runs"][0]["state"] == "succeeded"
    stdout_id = archive["runs"][0]["outputs"]["stdout"]
    artifact = next(item for item in archive["artifacts"] if item["id"] == stdout_id)
    assert (client.project / artifact["uri"]).read_text().strip() == "actual execution"
    assert archive["agents"][0]["harness_id"] == archive["runs"][0]["harness_id"]


def test_cli_bundles_a_live_coordinator_snapshot(client, tmp_path):
    run_command(client, [sys.executable, "-c", 'print("transfer integration")'], True)
    path = tmp_path / "live-transfer.zip"
    process = subprocess.run([sys.executable, "-m", "aica_mcp", "bundle", "--project", str(client.project),
                              "--output", str(path)], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    result = loads(process.stdout)
    verified = verify_bundle(path)
    assert result["bundle_sha256"] == verified["bundle_sha256"]
    assert verified["artifact_count"] >= 3


def test_identical_outputs_across_attempts_keep_both_producers(client):
    for attempt in range(2):
        result = run_command(client, [sys.executable, "-c", 'print("same")'], True)
        assert result["exit_code"] == 0
    archive = client.call("aica_get_archive")["archive"]
    assert len(archive["runs"]) == 2
    assert archive["runs"][0]["outputs"]["stdout"] != archive["runs"][1]["outputs"]["stdout"]
    assert validate_archive(archive)["valid"]


def test_denied_recording_still_runs_requested_command_without_collecting(client):
    marker = client.project / "must-not-exist"
    result = run_command(client, [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('bad')"], False)
    assert result["executed"] and not result["recording"]
    assert marker.exists()
    archive = client.call("aica_get_archive")["archive"]
    assert not archive["graph"]["nodes"] and not archive["runs"] and not archive["agents"]


def test_transport_outbox_survives_acknowledgement_loss(client, monkeypatch):
    session, source = start_source(client, True)
    source.close()
    event = {"event_id": new_id(), "source_id": new_id(), "source_seq": 1,
             "session_id": session["session_id"], "agent_id": None, "type": "message.recorded",
             "timestamp": utc_now(), "causal_parents": [], "payload": {"role": "user", "content": "durable"}}
    real_send = client.send

    def lose_reply(name, arguments):
        result = real_send(name, arguments)
        if name == "aica_submit_event":
            raise httpx.ReadTimeout("Injected lost acknowledgement")
        return result

    monkeypatch.setattr(client, "send", lose_reply)
    with pytest.raises(httpx.ReadTimeout):
        client.call("aica_submit_event", {"session_token": session["session_token"], "event": event})
    connection = client.transport_outbox()
    assert connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 1
    connection.close()
    monkeypatch.setattr(client, "send", real_send)
    results = client.replay_pending()
    assert results[0]["duplicate"]
    connection = client.transport_outbox()
    assert connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
    connection.close()
    matches = [item for item in client.call("aica_get_events")["events"] if item["event"]["event_id"] == event["event_id"]]
    assert len(matches) == 1


def test_denied_content_does_not_enter_source_outbox(client):
    denied = client.call("aica_start_session", {"request_id": new_id(), "record": False})
    source = RecordingSource(client, denied["session_id"], "invalid-token-long-enough")
    try:
        with pytest.raises(ValueError, match="consent"):
            source.emit("message.recorded", {"role": "user", "content": "never persist"})
        assert source.connection.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 0
    finally:
        source.close()


def test_process_kill_recovers_acknowledged_events_without_reexecuting(client):
    session, source = start_source(client, True)
    try:
        agent_id, harness_id = register_actor(source, "Crash test", "test")
        node_id = new_id()
        node = {"id": node_id, "kind": "tool", "label": "External effect", "branch_id": "main",
                "provenance": {"mode": "recorded", "evidence": []}}
        revision = extend_graph(source, [node])
        run = {"id": new_id(), "trace_id": new_id(), "node_id": node_id, "revision_id": revision,
               "session_id": source.session_id, "agent_id": agent_id, "harness_id": harness_id,
               "state": "running", "started_at": utc_now(), "inputs": {}, "outputs": {}, "artifact_ids": [],
               "provenance": {"mode": "recorded", "evidence": []}}
        receipt = checked(source.emit("tool.started", run, agent_id))
        old_pid = client.endpoint["pid"]
        os.kill(old_pid, signal.SIGKILL if os.name != "nt" else signal.SIGTERM)
        archive = checked(client.call("aica_get_archive"))["archive"]
        assert client.endpoint["pid"] != old_pid
        assert archive["runs"][0]["state"] == "unknown"
        assert archive["snapshot"]["commit_seq"] > receipt["commit_seq"]
        assert len(archive["runs"]) == 1
        checked(source.emit("tool.finished", {"run_id": run["id"], "state": "succeeded", "ended_at": utc_now(),
                                              "outputs": {}, "artifact_ids": []}, agent_id))
        assert checked(client.call("aica_get_archive"))["archive"]["runs"][0]["state"] == "succeeded"
    finally:
        source.close()


async def connect_mcp(stack, project):
    root = Path(__file__).resolve().parents[3]
    environment = {"PYTHONPATH": os.pathsep.join(str(root / path) for path in
                                                ("packages/schema/src", "packages/mcp-adapters/src"))}
    parameters = StdioServerParameters(command=sys.executable,
                                      args=["-m", "aica_mcp", "serve", "--project", str(project)], env=environment)
    streams = await stack.enter_async_context(stdio_client(parameters))
    session = await stack.enter_async_context(ClientSession(*streams))
    await session.initialize()
    return session


async def test_two_mcp_clients_share_recorder_and_return_structured_results(client):
    async with AsyncExitStack() as stack:
        first = await connect_mcp(stack, client.project)
        second = await connect_mcp(stack, client.project)
        tools = await first.list_tools()
        assert {tool.name for tool in tools.tools} >= {"aica_submit_event", "aica_start_session"}
        schema = await second.read_resource("aica://schema/0.1")
        assert loads(schema.contents[0].text)["title"] == "AiCA Archive v0.1"
        recording_schema = await first.read_resource("aica://schema/recording/0.1")
        assert "definition.replaced" in loads(recording_schema.contents[0].text)["payloads"]
        results = await asyncio.gather(
            first.call_tool("aica_start_session", {"request_id": new_id(), "record": True}),
            second.call_tool("aica_start_session", {"request_id": new_id(), "record": True}))
        credentials = [item.structured_content for item in results]
        assert all(item["status"] == "success" for item in credentials)
        events = [{"event_id": new_id(), "source_id": new_id(), "source_seq": 1,
                   "session_id": item["session_id"], "agent_id": None, "type": "message.recorded",
                   "timestamp": utc_now(), "causal_parents": [], "payload": {"role": "user", "content": "MCP"}}
                  for item in credentials]
        replies = await asyncio.gather(
            first.call_tool("aica_submit_event", {"session_token": credentials[0]["session_token"], "event": events[0]}),
            second.call_tool("aica_submit_event", {"session_token": credentials[1]["session_token"], "event": events[1]}))
        assert all(not item.is_error for item in replies)
        assert replies[0].structured_content["stream_id"] == replies[1].structured_content["stream_id"]
        invalid = await first.call_tool("aica_start_session", {"request_id": new_id(), "record": "yes"})
        assert invalid.is_error and invalid.structured_content["status"] == "error"
        page = await first.call_tool("aica_get_items", {"collection": "sessions", "limit": 1})
        assert not page.is_error and page.structured_content["total"] == 2
        revision = await second.call_tool("aica_get_revision", {"revision_id": page.structured_content["revision_id"]})
        assert not revision.is_error and revision.structured_content["artifact_id"] is None
        references = await first.call_tool("aica_check_references", {})
        assert not references.is_error and references.structured_content["archive_readable"]
        assert not references.structured_content["execution_checked"]
        absent = await second.call_tool("aica_get_revision", {"revision_id": "absent"})
        assert absent.is_error and absent.structured_content["code"] == "revision_not_found"


def test_authorized_git_extraction_uses_real_parent_relationships(client):
    subprocess.run(["git", "init", str(client.project)], check=True, capture_output=True)
    for number in range(2):
        (client.project / "sample.txt").write_text(str(number))
        subprocess.run(["git", "-C", str(client.project), "add", "sample.txt"], check=True)
        subprocess.run(["git", "-C", str(client.project), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-m", f"Step {number}"],
                       check=True, capture_output=True)
    result = extract_git(client, True)
    assert result["node_count"] == 2
    archive = client.call("aica_get_archive")["archive"]
    assert len(archive["graph"]["edges"]) == 1
    assert all(node["provenance"]["mode"] == "extracted" for node in archive["graph"]["nodes"])
    assert archive["metadata"]["source_mode"] == "extracted"
    status = subprocess.run(["git", "-C", str(client.project), "status", "--porcelain"],
                            check=True, capture_output=True, text=True)
    assert ".aica/" not in status.stdout


@pytest.mark.parametrize("source_format,text", [
    ("codex", '{"type":"response_item","payload":{"type":"function_call","name":"exec_command",'
     '"call_id":"call1","arguments":"{\\"cmd\\":\\"pwd\\"}"}}\n'
     '{"type":"response_item","payload":{"type":"function_call_output","call_id":"call1","output":"/project"}}'),
    ("claude_code", '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"call1",'
     '"name":"Bash","input":{"command":"pwd"}}]}}\n'
     '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"call1","content":"/project"}]}}'),
    ("normalized", '{"type":"tool_call","id":"call1","name":"shell","arguments":{"command":"pwd"}}\n'
     '{"type":"tool_result","call_id":"call1","output":"/project"}'),
])
def test_log_dialects_preserve_evidence_without_inventing_replayability(source_format, text):
    result = parse_agent_log(text, source_format, "provided-export.jsonl", "source-artifact")
    assert len(result["nodes"]) == 1
    node = result["nodes"][0]
    assert node["execution"]["kind"] == "manual"
    assert len(node["provenance"]["evidence"]) == 2
    assert node["extensions"]["aica.observed_result"] == "/project"


def test_log_import_reads_only_explicit_file_and_preserves_source(client):
    source_file = client.project / "provided.jsonl"
    source_file.write_text('{"type":"message","content":"Authorized message"}\n')
    result = extract_log(client, source_file, "normalized", True)
    assert result["node_count"] == 1
    archive = client.call("aica_get_archive")["archive"]
    node = archive["graph"]["nodes"][0]
    assert node["description"] == "Authorized message"
    assert node["provenance"]["evidence"][0]["artifact_id"] == result["artifact_id"]


def test_local_coordinator_rejects_unauthenticated_and_browser_origin_requests(client):
    url = f"http://127.0.0.1:{client.endpoint['port']}/rpc"
    with httpx.Client(trust_env=False) as transport:
        anonymous = transport.post(url, json={"method": "status", "arguments": {}})
        browser = transport.post(url, headers={"Authorization": f"Bearer {client.endpoint['token']}",
                                               "Origin": "http://untrusted.invalid"},
                                 json={"method": "status", "arguments": {}})
    assert anonymous.status_code == browser.status_code == 403


def test_export_has_no_private_outbox_database_reference(client):
    run_command(client, [sys.executable, "-c", "pass"], True)
    archive = client.call("aica_get_archive")["archive"]
    assert ".sqlite3" not in str(archive)
    with sqlite3.connect(client.directory / "recording.sqlite3") as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_continuous_recording_refreshes_snapshot_without_explicit_export(client):
    session, source = start_source(client, True)
    try:
        receipt = checked(source.emit("message.recorded", {"role": "user", "content": "continuous"}))
        deadline = time.monotonic() + 5
        snapshot = loads((client.project / "aica.json").read_text())
        while snapshot["snapshot"]["commit_seq"] < receipt["commit_seq"] and time.monotonic() < deadline:
            time.sleep(0.05)
            snapshot = loads((client.project / "aica.json").read_text())
        assert snapshot["snapshot"]["commit_seq"] >= receipt["commit_seq"]
        assert validate_archive(snapshot)["valid"]
    finally:
        source.close()
