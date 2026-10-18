import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from aica_mcp.approval import ApprovalPages
from aica_mcp.consent import AgentConsent
from aica_mcp.hosts import CLIENTS, config_bundle, normalize_hook
from aica_schema.protocol import ProtocolError, dumps, loads
from test_turns import assert_absent
from test_turns import gate as gate
from test_turns import running as running


def envelope(client, project, name="UserPromptSubmit", prompt="SYNTHETIC_PROMPT", turn="t1"):
    raw = {"session_id": "s1", "cwd": str(project), "hook_event_name": name, "turn_id": turn, "prompt": prompt}
    if client == "claude-code":
        raw["prompt_id"] = turn
    elif client == "cursor":
        raw.update(conversation_id="s1", generation_id=turn, workspace_roots=[str(project)],
                   hook_event_name={"UserPromptSubmit": "beforeSubmitPrompt", "SessionEnd": "sessionEnd"}.get(name, name))
    elif client == "gemini-cli":
        raw["hook_event_name"] = "BeforeAgent" if name == "UserPromptSubmit" else name
    elif client == "github-copilot":
        raw["hook_event_name"] = "userPromptSubmitted" if name == "UserPromptSubmit" else "sessionEnd"
    return raw


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("choice", [True, False])
def test_every_client_browser_gate_records_only_yes_and_observed_identity(gate, client, choice):
    pages = ApprovalPages(gate)
    pages.origin = "http://127.0.0.1:12345"
    bridge = gate.attach(client, 4321)["bridge_id"]
    event = normalize_hook(client, envelope(client, gate.recorder.project, prompt="PRIVATE_DECLINED" if not choice else "ALLOWED"))
    request = pages.for_turn(event, [999, 4321])
    nonce = urlsplit(request["url"]).fragment
    assert not gate.recorder.document["sessions"]
    assert "ALLOWED" not in dumps(request) and "PRIVATE_DECLINED" not in dumps(request)
    assert "是否记录" in pages.view(nonce)["message"]
    assert pages.decide(nonce, choice)["choice"] == choice
    assert pages.status(request["request_id"])["choice"] == choice
    pages.cancel(request["request_id"])
    if choice:
        assert gate.recorder.document["agents"][0]["client"] == client
        assert gate.active(bridge)["turn"]["turn_id"] == event["turn_id"]
        assert gate.recorder.document["harnesses"][0]["config"]["coverage"] == event["coverage"]
    else:
        assert not gate.recorder.document["sessions"]
        gate.recorder.flush()
        assert_absent(gate.recorder.project, "PRIVATE_DECLINED")
        assert "PRIVATE_DECLINED" not in repr(pages.requests) + repr(gate.turns)
    with pytest.raises(ProtocolError):
        pages.decide(nonce, True)


def test_clients_with_same_host_and_turn_cannot_share_authorization(gate):
    a = gate.attach("codex", 123)["bridge_id"]
    b = gate.attach("claude-code", 456)["bridge_id"]
    for client in ("codex", "claude-code"):
        gate.hook(normalize_hook(client, envelope(client, gate.recorder.project)))
    first = gate.pending(a, "s1", "t1")
    second = gate.pending(b, "s1", "t1")
    with pytest.raises(ProtocolError):
        gate.resolve(a, "s1", "t1", second["challenge"], True)
    gate.resolve(a, "s1", "t1", first["challenge"], True)
    gate.resolve(b, "s1", "t1", second["challenge"], False)
    assert len(gate.recorder.document["sessions"]) == 1
    assert gate.active(a)["turn"] and gate.active(b)["turn"] is None


@pytest.mark.parametrize("ending", ["timeout", "detach", "new_turn", "cancel"])
def test_stale_browser_confirmation_never_authorizes(gate, ending):
    pages = ApprovalPages(gate)
    pages.origin = "http://127.0.0.1:12345"
    bridge = gate.attach("cursor", 123)["bridge_id"]
    event = normalize_hook("cursor", envelope("cursor", gate.recorder.project, prompt="PRIVATE_STALE"))
    request = pages.for_turn(event, [123])
    nonce = urlsplit(request["url"]).fragment
    if ending == "timeout":
        pages.requests[request["request_id"]]["created"] -= 121
    elif ending == "detach":
        gate.detach(bridge)
    elif ending == "new_turn":
        event["turn_id"] = "t2"
        gate.hook(event)
    else:
        pages.cancel(request["request_id"])
    try:
        assert not pages.decide(nonce, True)["choice"]
    except ProtocolError:
        pass
    assert not gate.recorder.document["sessions"]
    gate.recorder.flush()
    assert_absent(gate.recorder.project, "PRIVATE_STALE")


def test_browser_requires_one_bridge_owned_by_hook_host(gate):
    gate.attach("cursor", 123)
    with pytest.raises(ProtocolError):
        gate.matching_bridge("cursor", [456])
    gate.attach("cursor", 123)
    with pytest.raises(ProtocolError):
        gate.matching_bridge("cursor", [123])
    with pytest.raises(ProtocolError):
        gate.matching_bridge("codex", [123])


@pytest.mark.parametrize("client", ["gemini-cli", "github-copilot"])
def test_partial_clients_cannot_assign_unrelated_reply_or_tool_to_latest_prompt(gate, client):
    event = normalize_hook(client, envelope(client, gate.recorder.project))
    bridge = gate.attach(client)["bridge_id"]
    gate.hook(event)
    pending = gate.pending(bridge, "s1", event["turn_id"])
    gate.resolve(bridge, "s1", event["turn_id"], pending["challenge"], True)
    gate.hook({**event, "hook_event_name": "AssistantMessage", "last_assistant_message": "UNASSOCIATED_PRIVATE"})
    assert "UNASSOCIATED_PRIVATE" not in dumps(gate.recorder.get_events())
    assert not gate.recorder.document["graph"]["nodes"]


def test_cursor_ambiguity_and_claude_child_do_not_inherit(gate):
    raw = envelope("cursor", gate.recorder.project)
    raw["workspace_roots"].append("/another-project")
    with pytest.raises(ProtocolError):
        normalize_hook("cursor", raw)
    raw = envelope("claude-code", gate.recorder.project)
    raw["agent_id"] = "child"
    assert normalize_hook("claude-code", raw) is None


@pytest.mark.parametrize("client", CLIENTS)
def test_generated_native_configs_and_files_do_not_overwrite_existing(tmp_path, client):
    directory = tmp_path / client
    bundle = config_bundle(client, output=directory)
    assert "--per-turn" in dumps(bundle["mcp"])
    assert client in dumps(bundle["mcp"])
    assert "transcript_path" not in dumps(bundle)
    assert not (directory / "recording-policy.json").exists()
    with pytest.raises(FileExistsError):
        config_bundle(client, output=directory)
    if client == "opencode":
        assert (directory / "aica.js").exists()
    elif client in {"codex", "claude-code"}:
        assert bundle["hooks"]["hooks"]["UserPromptSubmit"][0]["hooks"][1]["type"] == "mcp_tool"
        if client == "codex":
            assert bundle["mcp"]["mcp_servers"]["aica"]["tool_timeout_sec"] >= 150


def test_browser_routes_require_nonce_host_origin_and_explicit_yes(running):
    gate = running.turns
    gate.attach("cursor", 123)
    event = normalize_hook("cursor", envelope("cursor", gate.recorder.project, prompt="HTTP_DECLINED_PRIVATE"))
    request = running.approvals.for_turn(event, [123])
    nonce = urlsplit(request["url"]).fragment
    origin = running.approvals.origin
    with httpx.Client(trust_env=False) as transport:
        page = transport.get(origin + "/approval")
        assert page.status_code == 200 and "HTTP_DECLINED_PRIVATE" not in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        for headers in ({}, {"Origin": "https://untrusted.test"}, {"Origin": origin, "Host": "evil.test"}):
            assert transport.post(origin + "/approval/decision", headers=headers, json={"nonce": nonce, "choice": True}).status_code == 403
        assert transport.post(origin + "/approval/view", headers={"Origin": origin}, json={"nonce": "x" * 32}).json()["status"] == "error"
        response = transport.post(origin + "/approval/decision", headers={"Origin": origin}, json={"nonce": nonce, "choice": False})
        assert response.json()["choice"] is False
        assert transport.post(origin + "/approval/decision", headers={"Origin": origin}, json={"nonce": nonce, "choice": True}).json()["status"] == "error"
    running.recorder.flush()
    assert_absent(gate.recorder.project, "HTTP_DECLINED_PRIVATE")


async def test_native_rejection_does_not_fall_back_but_missing_ui_does(gate, monkeypatch):
    asked = []
    monkeypatch.setattr("aica_mcp.hosts.browser_question", lambda *args: asked.append(args) or {"choice": True, "state": "accepted"})
    bridge = AgentConsent(gate.recorder.project)

    async def no(*args, **kwargs):
        return SimpleNamespace(action="accept", content={"choice": "No"})

    async def absent(*args, **kwargs):
        raise RuntimeError("No native confirmation UI")

    assert not await bridge.ask(SimpleNamespace(session=SimpleNamespace(elicit_form=no), request_id=1), "Synthetic")
    assert not asked
    assert await bridge.ask(SimpleNamespace(session=SimpleNamespace(elicit_form=absent), request_id=1), "Synthetic")
    assert len(asked) == 1


def test_cursor_recording_hook_does_not_block_normal_tool_permission(tmp_path):
    event = envelope("cursor", tmp_path, "preToolUse")
    event.update(tool_name="Shell", tool_use_id="c1", tool_input={})
    result = subprocess.run([sys.executable, "-m", "aica_mcp", "agent-hook", "--client", "cursor"],
                            input=dumps(event), capture_output=True, text=True, check=True)
    assert loads(result.stdout) == {"permission": "allow"}


@pytest.mark.parametrize("client", CLIENTS)
async def test_each_client_real_mcp_bridge_browser_no_then_yes(running, client):
    import asyncio
    from contextlib import AsyncExitStack

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    project = running.recorder.project
    async with AsyncExitStack() as stack:
        params = StdioServerParameters(command=sys.executable, args=["-m", "aica_mcp", "serve", "--project", str(project), "--per-turn", "--client", client])
        streams = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(*streams))
        await session.initialize()
        assert {"aica_pull_platform", "aica_sync_platform"} <= {tool.name for tool in (await session.list_tools()).tools}
        assert (await session.call_tool("aica_start_session", {"request_id": "bypass", "record": True})).is_error
        for choice, turn in [(False, "no"), (True, "yes")]:
            event = normalize_hook(client, envelope(client, project, prompt="MCP_NO_PRIVATE" if not choice else "MCP_YES", turn=turn))
            with running.recorder.mutex:
                request = running.approvals.for_turn(event, [os.getpid()])
            nonce = urlsplit(request["url"]).fragment
            with httpx.Client(trust_env=False) as transport:
                result = transport.post(running.approvals.origin + "/approval/decision", headers={"Origin": running.approvals.origin}, json={"nonce": nonce, "choice": choice}).json()
            assert result["choice"] is choice
        archive = await session.call_tool("aica_get_archive", {})
        assert not archive.is_error
        assert len(archive.structured_content["archive"]["sessions"]) == 1
        assert archive.structured_content["archive"]["agents"][0]["client"] == client
        await asyncio.sleep(0)
    running.recorder.flush()
    assert_absent(project, "MCP_NO_PRIVATE")


@pytest.mark.parametrize("legacy", [False, True])
def test_browser_page_yes_no_close_and_untrusted_text(running, tmp_path, monkeypatch, legacy):
    import shutil

    playwright = Path(os.environ.get("AICA_PLAYWRIGHT_MODULE", "missing-playwright-module"))
    chromium = Path(os.environ.get("AICA_CHROMIUM_PATH", "missing-chromium-binary"))
    if not shutil.which("node") or not playwright.exists() or not chromium.exists():
        pytest.skip("Use existing local Playwright and Chromium only; no browser installation")
    if legacy:
        view = running.approvals.view
        decide = running.approvals.decide

        def legacy_view(nonce):
            result = view(nonce)
            result.pop("decision_actions")
            return result

        def legacy_decide(nonce, choice):
            result = decide(nonce, choice)
            return result

        monkeypatch.setattr(running.approvals, "view", legacy_view)
        monkeypatch.setattr(running.approvals, "decide", legacy_decide)
    gate = running.turns
    gate.attach("cursor", 123)
    for choice in ("no", "yes", "close"):
        event = normalize_hook("cursor", envelope("cursor", gate.recorder.project, prompt="<img src=x onerror=alert(1)> UI_" + choice, turn=choice))
        with running.recorder.mutex:
            request = running.approvals.for_turn(event, [123])
        script = tmp_path / "browser.cjs"
        script.write_text('''const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, headless: true});
 const page = await browser.newPage();
 page.on("dialog", () => {throw new Error("Untrusted prompt executed")});
 await page.goto(process.env.CONSENT_URL);
 await page.locator("#no:not([disabled])").waitFor();
 if (await page.locator("#message img").count()) throw new Error("Untrusted HTML rendered");
 if (process.env.CHOICE === "close") await page.goto("about:blank");
 else { await page.locator("#" + process.env.CHOICE).click(); await page.waitForFunction(() => document.querySelector("#status").textContent.includes("可以关闭")); }
 await browser.close();
})().catch(error => {console.error(error.message);process.exit(1)});
''', encoding="utf-8")
        environment = {**os.environ, "PLAYWRIGHT_MODULE": str(playwright), "CHROMIUM_PATH": str(chromium),
                       "CONSENT_URL": request["url"], "CHOICE": choice}
        subprocess.run(["node", str(script)], env=environment, capture_output=True, text=True, check=True, timeout=20)
        result = running.approvals.status(request["request_id"])
        assert result["choice"] is (choice == "yes")
        assert result["state"] != "pending"
        if choice == "close":
            assert result["state"] == ("declined" if legacy else "cancelled")
    assert len(gate.recorder.document["sessions"]) == 1


def test_missing_browser_cancels_without_persisting_prompt(running, monkeypatch):
    from aica_mcp.hosts import wait_for_approval

    running.turns.attach("cursor", 123)
    event = normalize_hook("cursor", envelope("cursor", running.recorder.project, prompt="NO_BROWSER_PRIVATE"))
    request = running.approvals.for_turn(event, [123])
    monkeypatch.setattr("aica_mcp.hosts.webbrowser.open", lambda *args, **kwargs: False)
    assert wait_for_approval(running.recorder.project, request)["choice"] is False
    assert not running.recorder.document["sessions"]
    assert "NO_BROWSER_PRIVATE" not in repr(running.approvals.requests) + repr(running.turns.turns)


@pytest.mark.parametrize("record", [True, False])
def test_opencode_plugin_correlates_parent_ids_and_ignores_reasoning(tmp_path, record):
    import shutil

    if not shutil.which("node"):
        pytest.skip("Existing Node runtime unavailable")
    captured = tmp_path / "events.jsonl"
    stub = tmp_path / "capture-hook"
    stub.write_text(f'#!{sys.executable}\nimport sys\nfrom pathlib import Path\nwith Path({str(captured)!r}).open("a") as stream:\n    stream.write(sys.stdin.read() + "\\n")\nprint({dumps({"recording": record})!r})\n', encoding="utf-8")
    stub.chmod(0o700)
    source = Path(__file__).resolve().parents[1] / "src" / "aica_mcp" / "opencode-plugin.mjs"
    plugin = tmp_path / "aica.mjs"
    plugin.write_text(source.read_text(encoding="utf-8").replace('"AICA_PYTHON_EXECUTABLE"', dumps(str(stub))), encoding="utf-8")
    runner = tmp_path / "run.mjs"
    runner.write_text('''import {AicaPlugin} from "./aica.mjs";
const plugin = await AicaPlugin({directory: process.cwd()});
await plugin["chat.message"]({sessionID:"s1",model:{providerID:"fixture",modelID:"test-model"}}, {message:{id:"u1",role:"user"},parts:[{type:"text",text:"VISIBLE_USER"},{type:"reasoning",text:"PRIVATE_THOUGHT"},{type:"text",text:"SYNTHETIC_PRIVATE",synthetic:true}]});
const emit = async (type, properties) => plugin.event({event:{type,properties}});
await emit("message.updated", {info:{id:"a1",role:"assistant",sessionID:"s1",parentID:"u1"}});
await emit("message.part.updated", {part:{id:"text1",type:"text",messageID:"a1",sessionID:"s1",text:"VISIBLE_REPLY"}});
await emit("message.part.updated", {part:{id:"thought1",type:"reasoning",messageID:"a1",sessionID:"s1",text:"PRIVATE_THOUGHT"}});
await emit("message.part.updated", {part:{id:"unknown",type:"text",messageID:"unassociated",sessionID:"s1",text:"UNKNOWN_PRIVATE"}});
for (const status of ["running","completed","completed"]) await emit("message.part.updated",{part:{id:"tool1",type:"tool",callID:"c1",tool:"bash",messageID:"a1",sessionID:"s1",state:{status,input:{command:"fixture"},output:"RESULT"}}});
await emit("message.updated", {info:{id:"a1",role:"assistant",sessionID:"s1",parentID:"u1",time:{completed:1}}});
await plugin["chat.message"]({sessionID:"s1"}, {message:{id:"u2",role:"user"},parts:[{type:"text",text:"SECOND_USER"}]});
await emit("message.updated", {info:{id:"old",role:"assistant",sessionID:"s1",parentID:"u1"}});
await emit("message.part.updated", {part:{id:"old-text",type:"text",messageID:"old",sessionID:"s1",text:"LATE_PRIVATE"}});
await emit("session.idle", {sessionID:"s1"});
await plugin.dispose();
''', encoding="utf-8")
    subprocess.run(["node", str(runner)], cwd=tmp_path, capture_output=True, text=True, check=True, timeout=20)
    events = [loads(line) for line in captured.read_text(encoding="utf-8").splitlines()]
    expected = ["UserPromptSubmit", "PreToolUse", "PostToolUse", "AssistantMessage", "UserPromptSubmit", "Stop", "SessionEnd"] if record else ["UserPromptSubmit", "UserPromptSubmit"]
    assert [event["hook_event_name"] for event in events] == expected
    assert events[0]["agent_model"] == {"name": "test-model", "provider": "fixture"}
    assert all(marker not in dumps(events) for marker in ("PRIVATE_THOUGHT", "SYNTHETIC_PRIVATE", "UNKNOWN_PRIVATE", "LATE_PRIVATE"))


@pytest.mark.parametrize("existing_policy", [True, False])
async def test_unsupported_live_coordinator_does_not_change_recording_policy(tmp_path, monkeypatch, existing_policy):
    from aica_mcp.consent import enable_turn_policy
    from aica_mcp.server import serve_stdio

    if existing_policy:
        enable_turn_policy(tmp_path)
    policy = tmp_path / ".aica" / "recording-policy.json"
    original = policy.read_bytes() if existing_policy else None

    class OldClient:
        def __init__(self, project, autostart=True):
            self.project = project

        def request(self, name):
            return {"status": "success", "turn_recording_version": 1}

    monkeypatch.setattr("aica_mcp.server.RecorderClient", OldClient)
    with pytest.raises(ProtocolError) as error:
        await serve_stdio(tmp_path, True, "claude-code")
    assert error.value.code == "coordinator_upgrade_required"
    assert policy.exists() is existing_policy
    if existing_policy:
        assert policy.read_bytes() == original


@pytest.mark.parametrize("state", ["declined", "cancelled", "expired", "browser_unavailable"])
async def test_browser_confirmation_reports_reason_and_never_calls_native_ui(gate, monkeypatch, state):
    bridge = AgentConsent(gate.recorder.project)
    bridge.confirmation = "browser"
    monkeypatch.setattr("aica_mcp.hosts.browser_question", lambda *args: {"choice": False, "state": state})

    async def unexpected(*args, **kwargs):
        pytest.fail("Explicit browser preference must not call a native UI that silently declines")

    assert not await bridge.ask(SimpleNamespace(session=SimpleNamespace(elicit_form=unexpected), request_id=1), "Synthetic")
    assert bridge.confirmation_reason == "browser_" + state
    assert not gate.recorder.document["sessions"]


def test_browser_hook_config_uses_the_mcp_connection_instead_of_parent_pid_guessing():
    from aica_mcp.hosts import hook_config

    config = hook_config("codex", confirmation="browser")
    command, mcp = config["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert "--confirmation auto" in command["command"]
    assert mcp["type"] == "mcp_tool" and mcp["input"]["confirmation"] == "browser"
    assert mcp["input"]["hook_output"] is True


def test_closing_or_timeout_action_cannot_approve_recording(gate):
    pages = ApprovalPages(gate)
    pages.origin = "http://127.0.0.1:12345"
    bridge = gate.attach("codex", 123)["bridge_id"]
    request = pages.create(bridge, "Synthetic question")
    nonce = urlsplit(request["url"]).fragment
    for action in ("dismiss", "timeout"):
        with pytest.raises(ProtocolError):
            pages.decide(nonce, True, action)
    assert pages.decide(nonce, False, "dismiss")["state"] == "cancelled"
    assert not gate.recorder.document["sessions"]
