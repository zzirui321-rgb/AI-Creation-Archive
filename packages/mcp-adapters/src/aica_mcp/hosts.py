import os
import shlex
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, require
from aica_schema.recording import recording_contract, require_host_hook, require_recording_policy

from .consent import confirmation_message, control_request
from .host_bridge import discover_bridge

CLIENTS = tuple(recording_contract()["$defs"]["agent_client"]["enum"])


def normalize_hook(client, raw, event_name=None):
    require(client in CLIENTS and isinstance(raw, dict), "Unknown client or invalid Hook envelope")
    name = event_name or raw.get("hook_event_name")
    session = raw.get("session_id") or raw.get("sessionId")
    cwd = raw.get("cwd")
    turn = raw.get("turn_id")
    coverage = "local_hooks"
    if client == "cursor":
        roots = raw.get("workspace_roots", [])
        require(isinstance(roots, list) and len(roots) == 1, "Cursor needs exactly one explicit workspace root", "workspace_ambiguous")
        cwd = roots[0]
        session = raw.get("conversation_id")
        turn = raw.get("generation_id")
        names = {"sessionStart": "SessionStart", "sessionEnd": "SessionEnd", "beforeSubmitPrompt": "UserPromptSubmit",
                 "preToolUse": "PreToolUse", "postToolUse": "PostToolUse", "postToolUseFailure": "PostToolUse",
                 "afterAgentResponse": "AssistantMessage", "stop": "Stop", "subagentStart": "SessionEnd"}
        name = names.get(name)
    elif client == "claude-code":
        turn = raw.get("prompt_id")
        if name == "PostToolUseFailure":
            name = "PostToolUse"
        elif name == "StopFailure":
            name = "Stop"
        elif name == "SubagentStart":
            name = "SessionEnd"
        if raw.get("agent_id") and name != "SessionEnd":
            return None
    elif client == "gemini-cli":
        name = {"SessionStart": "SessionStart", "SessionEnd": "SessionEnd", "BeforeAgent": "UserPromptSubmit",
                "AfterAgent": "SessionEnd"}.get(name)
        turn = None
        coverage = "prompt_only"
    elif client == "github-copilot":
        name = {"sessionStart": "SessionStart", "sessionEnd": "SessionEnd", "userPromptSubmitted": "UserPromptSubmit",
                "agentStop": "SessionEnd", "SessionStart": "SessionStart", "SessionEnd": "SessionEnd",
                "UserPromptSubmit": "UserPromptSubmit", "Stop": "SessionEnd"}.get(name)
        turn = None
        coverage = "prompt_only"
    if name is None:
        return None
    require(isinstance(session, str) and session and isinstance(cwd, str) and cwd, "Hook identity and workspace are required")
    if name == "UserPromptSubmit" and not turn:
        turn = "submission-" + new_id()
        coverage = "prompt_only"
    if name not in {"SessionStart", "SessionEnd"} and not turn:
        return {"client": client, "session_id": session, "cwd": cwd, "hook_event_name": "SessionEnd", "source": "boundary_missing"}
    event = {"client": client, "session_id": session, "cwd": cwd, "hook_event_name": name}
    if turn:
        event["turn_id"] = turn
    if name == "UserPromptSubmit":
        event.update(prompt=raw.get("prompt"), coverage=coverage)
    if name in {"PreToolUse", "PostToolUse"}:
        event.update(tool_name=raw.get("tool_name"), tool_use_id=raw.get("tool_use_id"), tool_input=raw.get("tool_input"))
        if name == "PostToolUse":
            response = raw.get("tool_response", raw.get("tool_output"))
            if raw.get("error") or raw.get("error_message"):
                response = {"isError": True, "error": raw.get("error", raw.get("error_message"))}
            event["tool_response"] = response
    if name in {"Stop", "AssistantMessage"}:
        reply = raw.get("text") if client == "cursor" else raw.get("last_assistant_message")
        if isinstance(reply, str):
            event["last_assistant_message"] = reply
    if raw.get("status") == "aborted":
        event["hook_event_name"] = "Interrupt"
    if isinstance(raw.get("model"), str):
        event["model"] = raw["model"]
    if isinstance(raw.get("agent_model"), dict):
        event["agent_model"] = raw["agent_model"]
    if name in {"SessionStart", "SessionEnd"}:
        event["source"] = raw.get("source", "turn_end" if name == "SessionEnd" else "startup")
    require_host_hook(event)
    return event


def owner_pids():
    parents = []
    pid = os.getppid()
    if os.name == "nt":
        script = "$n=" + str(pid) + "; for($i=0;$i -lt 32 -and $n -gt 0;$i++){ $n; $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$n); $n=$p.ParentProcessId }"
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                                text=True, timeout=5, check=True)
        parents = [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]
    else:
        while pid > 1 and pid not in parents and len(parents) < 32:
            parents.append(pid)
            result = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=1, check=True)
            value = result.stdout.strip()
            pid = int(value) if value else 0
    return parents


def wait_for_approval(project, request):
    request_id = request["request_id"]
    try:
        opened = webbrowser.open(request["url"], new=2)
        if not opened:
            control_request(project, "approval_cancel", {"request_id": request_id})
            return {"status": "success", "choice": False, "state": "browser_unavailable"}
        deadline = time.monotonic() + 125
        result = {"state": "pending"}
        while result["state"] == "pending" and time.monotonic() < deadline:
            result = control_request(project, "approval_status", {"request_id": request_id})
            if result["state"] == "pending":
                time.sleep(0.25)
        if result["state"] == "pending":
            control_request(project, "approval_cancel", {"request_id": request_id})
            return {"status": "success", "choice": False, "state": "expired"}
        return result
    finally:
        control_request(project, "approval_cancel", {"request_id": request_id})


def browser_question(project, bridge_id, message):
    request = control_request(project, "approval_create", {"bridge_id": bridge_id, "message": message})
    require(request["status"] == "success", "Local confirmation is unavailable")
    result = wait_for_approval(project, request)
    return result


def confirm_turn_upload(project, bridge_id):
    prepared = control_request(project, "upload_prepare", {"bridge_id": bridge_id}, timeout=120)
    require(prepared["status"] == "success", prepared.get("message", "Upload review failed"))
    arguments = {"bridge_id": bridge_id, "prepared_id": prepared["prepared_id"]}
    try:
        reply = browser_question(project, bridge_id, prepared["message"])
        if reply.get("choice") is not True or reply.get("state") != "accepted":
            return "本轮未上传（" + reply.get("state", "unavailable") + "）。"
        result = control_request(project, "upload_apply", arguments, timeout=300)
        require(result["status"] == "success", result.get("message", "Upload failed"))
        return "本轮快照及已登记附件已上传，平台已确认接收。"
    finally:
        control_request(project, "upload_cancel", arguments)


def run_hook(client="codex", event_name=None, confirmation="auto"):
    passthrough = {}
    submission = False
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, "Hook envelope exceeds limit")
        envelope = loads(raw.decode("utf-8"))
        submission = (event_name or envelope.get("hook_event_name")) == "UserPromptSubmit"
        if client == "cursor":
            name = event_name or envelope.get("hook_event_name")
            passthrough = {"continue": True} if name == "beforeSubmitPrompt" else {"permission": "allow"} if name in {"preToolUse", "subagentStart"} else {}
        event = normalize_hook(client, envelope, event_name)
        if event is None:
            return passthrough
        project = Path(event["cwd"]).resolve(strict=True)
        policy = project / ".aica" / "recording-policy.json"
        if not policy.exists():
            return passthrough
        require_recording_policy(loads(policy.read_text(encoding="utf-8")))
        if confirmation != "conversation" and event["hook_event_name"] == "UserPromptSubmit":
            access = control_request(project, "project_access", {"action": "status"})
            if access.get("state") in {"active", "paused"}:
                result = control_request(project, "hook", {"event": event, "owner_pids": owner_pids()})
                require(result["status"] == "success", result.get("message", "Recording bridge rejected this event"))
                if client in {"cursor", "github-copilot"}:
                    return passthrough
                if client == "opencode":
                    return {"recording": result.get("recording") is True}
                return {"systemMessage": result["systemMessage"]} if result.get("systemMessage") else {}
        if confirmation == "conversation":
            require(client == "codex", "Conversation confirmation currently requires Codex")
            require(event.get("coverage") != "prompt_only", "Conversation confirmation needs a stable Host turn identity")
            if submission:
                arguments = {"event": event, "owner_pids": owner_pids()}
                bridge = discover_bridge(project, event["session_id"])
                if bridge is not None:
                    arguments["bridge_id"] = bridge
                deadline = time.monotonic() + 2
                result = control_request(project, "conversation_submit", arguments, timeout=5)
                while result.get("code") == "bridge_ambiguous" and time.monotonic() < deadline:
                    time.sleep(0.1)
                    result = control_request(project, "conversation_submit", arguments, timeout=5)
                require(result["status"] == "success", result.get("message", "Recording bridge rejected this event"),
                        result.get("code", "bridge_error"))
                if result.get("decision") == "block":
                    result.pop("systemMessage", None)
                return {key: result[key] for key in ("systemMessage", "decision", "reason", "hookSpecificOutput") if key in result}
        browser = confirmation == "browser" or client not in {"codex", "claude-code"} or event.get("coverage") == "prompt_only"
        if event["hook_event_name"] == "UserPromptSubmit" and browser:
            request = control_request(project, "browser_turn", {"event": event, "owner_pids": owner_pids()})
            require(request["status"] == "success", "No unique connected MCP bridge for this Host process")
            result = wait_for_approval(project, request)
            message = confirmation_message("browser_" + result.get("state", "unavailable"), result.get("choice") is True)
            if result.get("coverage") == "prompt_only" and result.get("choice"):
                message = "AiCA：本轮仅自动记录本次需求；工具与回复缺少可靠关联，未自动采集。附件需另外选择。"
        else:
            timeout = 120 if event["hook_event_name"] == "Stop" else 1 if event["hook_event_name"] in {"Interrupt", "SessionEnd"} else 5
            result = control_request(project, "hook", {"event": event}, timeout)
            require(result["status"] == "success", "Recording bridge rejected this event")
            message = result.get("systemMessage")
            if result.get("upload_bridge_id"):
                try:
                    message += "\n" + confirm_turn_upload(project, result["upload_bridge_id"])
                except (ProtocolError, OSError, ValueError, httpx.HTTPError):
                    message += "\n本地已保存；上传未确认成功，请调用 aica_sync_platform 重新确认并检查回执。"
        if client == "cursor":
            return passthrough
        if client == "github-copilot":
            return {}
        if client == "opencode":
            return {"recording": result.get("choice", result.get("recording")) is True}
        return {"systemMessage": message} if message else {}
    except (ProtocolError, OSError, ValueError, httpx.HTTPError, subprocess.SubprocessError) as error:
        if confirmation == "conversation" and submission:
            code = error.code if isinstance(error, ProtocolError) else type(error).__name__
            recovery = ("当前协调器不支持聊天确认接口；请停止本项目旧协调器，再用同一代码版本重连 MCP 后重新提交原任务。"
                        if code == "unknown_method" else "请检查 MCP 连接、协调器版本和会话关联后重新提交。")
            message = (f"AiCA：确认服务不可用（{code}），原任务已暂停且未授权记录。"
                       + recovery)
            return {"decision": "block", "reason": message}
        if client in {"cursor", "github-copilot", "opencode"}:
            return passthrough
        return {"systemMessage": "AiCA 未接入或采集失败；未经本轮确认的内容不写入档案。"}


def hook_command(client, event=None, confirmation="auto"):
    argv = [sys.executable, "-m", "aica_mcp", "agent-hook", "--client", client, "--confirmation", confirmation]
    if event:
        argv.extend(["--event", event])
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    return command


def hook_config(client="codex", confirmation="auto", surface="cli"):
    require(client in CLIENTS, "Unknown Agent client")
    require(confirmation != "conversation" or client == "codex", "Conversation confirmation currently requires Codex")
    require(confirmation != "native-default-yes" or client in {"codex", "claude-code"},
            "native-default-yes requires a client with an MCP confirmation Hook; other clients are not adapted yet")
    if client in {"codex", "claude-code"}:
        events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
        events.extend(["Interrupt"] if client == "codex" else ["PostToolUseFailure", "StopFailure", "SubagentStart"])
        hooks = {name: [{"hooks": [{"type": "command", "command": hook_command(client, confirmation="conversation" if confirmation == "conversation" else "auto"),
                                    "timeout": 600 if name == "Stop" else 3 if name in {"Interrupt", "SessionEnd"} else 10}]}]
                 for name in events}
        if confirmation == "conversation":
            hooks["UserPromptSubmit"][0]["hooks"][0]["additionalContextLimit"] = 400000
            return {"hooks": hooks}
        turn_field = "turn_id" if client == "codex" else "prompt_id"
        hooks["UserPromptSubmit"][0]["hooks"].append({"type": "mcp_tool", "server": "aica", "tool": "aica_confirm_turn",
            "input": {"host_session_id": "${session_id}", "turn_id": "${" + turn_field + "}", "hook_output": True,
                      "confirmation": confirmation if confirmation in {"browser", "native-default-yes"} else "auto"}, "timeout": 130})
        return {"hooks": hooks}
    if client == "cursor":
        events = ["sessionStart", "sessionEnd", "beforeSubmitPrompt", "preToolUse", "postToolUse", "postToolUseFailure",
                  "afterAgentResponse", "stop", "subagentStart"]
        return {"version": 1, "hooks": {name: [{"command": hook_command(client, confirmation="browser"),
                                               "timeout": 600 if name == "stop" else 130 if name == "beforeSubmitPrompt" else 10}] for name in events}}
    if client == "gemini-cli":
        return {"hooks": {name: [{"hooks": [{"type": "command", "name": "aica-" + name,
                 "command": hook_command(client, confirmation="browser"), "timeout": 600000 if name == "AfterAgent" else 130000 if name == "BeforeAgent" else 10000}]}]
                 for name in ["SessionStart", "BeforeAgent", "AfterAgent", "SessionEnd"]}}
    if client == "github-copilot":
        events = ["SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"] if surface == "vscode" else ["sessionStart", "userPromptSubmitted", "agentStop", "sessionEnd"]
        timeout_key = "timeout" if surface == "vscode" else "timeoutSec"
        result = {"hooks": {name: [{"type": "command", "command": hook_command(client, name, "browser"),
                  timeout_key: 600 if name in {"Stop", "agentStop"} else 130 if name in {"UserPromptSubmit", "userPromptSubmitted"} else 10}] for name in events}}
        if surface != "vscode":
            result["version"] = 1
        return result
    return {}


def config_bundle(client, confirmation="auto", surface="cli", output=None):
    command = [sys.executable, "-m", "aica_mcp", "serve", "--project", ".", "--per-turn", "--client", client]
    if confirmation == "native-default-yes":
        command.append("--default-yes")
    stdio = {"command": command[0], "args": command[1:]}
    hooks = hook_config(client, confirmation, surface)
    plugin = None
    if client == "codex":
        stdio["tool_timeout_sec"] = 150
        mcp = {"mcp_servers": {"aica": stdio}}
    elif client == "opencode":
        mcp = {"mcp": {"aica": {"type": "local", "command": command, "enabled": True}}}
        plugin = Path(__file__).with_name("opencode-plugin.mjs").read_text(encoding="utf-8").replace('"AICA_PYTHON_EXECUTABLE"', dumps(sys.executable))
    else:
        if client in {"claude-code", "gemini-cli", "github-copilot"}:
            stdio["type"] = "stdio" if client != "github-copilot" else "local"
        if client == "github-copilot":
            stdio["tools"] = ["*"]
        mcp = {"mcpServers": {"aica": stdio}}
        if client == "github-copilot" and surface == "vscode":
            mcp = {"servers": {"aica": {"type": "stdio", "command": command[0], "args": command[1:]}}}
    result = {"status": "success", "client": client, "surface": surface, "mcp": mcp, "hooks": hooks,
              "coverage": "prompt_only" if client in {"gemini-cli", "github-copilot"} else "local_hooks",
              "activation": "Merge only AiCA entries into native configuration; review client trust and restart/reconnect."}
    if output:
        directory = Path(output).resolve()
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        for name, value in (("mcp.json", mcp), ("hooks.json", hooks)):
            path = directory / name
            path.write_text(dumps(value, pretty=True) + "\n", encoding="utf-8")
            path.chmod(0o600)
        if plugin:
            path = directory / "aica.js"
            path.write_text(plugin, encoding="utf-8")
            path.chmod(0o600)
        result["output"] = str(directory)
    elif plugin:
        result["plugin"] = plugin
    return result
