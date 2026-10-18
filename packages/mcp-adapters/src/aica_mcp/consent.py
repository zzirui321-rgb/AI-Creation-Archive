import os
import webbrowser
from pathlib import Path

import anyio
import httpx
from aica_schema.protocol import ProtocolError, dumps, loads, require
from aica_schema.recording import require_recording_policy

from .files import atomic_write, private_directory

CONVERSATION_QUESTION = ("AiCA：是否记录本次任务？\n"
                         "Yes：记录并导出到 exports/aica.json。\n"
                         "No：继续任务，不记录、不导出。\n"
                         "请单独回复 Yes 或 No，选择后继续。")


def enable_turn_policy(project):
    directory = Path(project).resolve(strict=True) / ".aica"
    private_directory(directory)
    path = directory / "recording-policy.json"
    require(not path.is_symlink(), "Recording policy cannot be a symlink")
    if path.exists():
        require_recording_policy(loads(path.read_text(encoding="utf-8")))
    else:
        atomic_write(path, dumps({"version": 1, "mode": "per_turn"}).encode("utf-8"))


def control_request(project, method, arguments=None, timeout=5):
    path = Path(project) / ".aica" / "control.json"
    require(not path.is_symlink(), "Control endpoint cannot be a symlink")
    endpoint = loads(path.read_text(encoding="utf-8"))
    require(endpoint.get("project") == str(Path(project).resolve()), "Control endpoint belongs to another project")
    require(type(endpoint.get("port")) is int and 1024 <= endpoint["port"] <= 65535, "Invalid control port")
    require(isinstance(endpoint.get("token"), str) and len(endpoint["token"]) >= 32, "Invalid control credential")
    url = f"http://127.0.0.1:{endpoint['port']}/control"
    with httpx.Client(timeout=timeout, trust_env=False) as transport:
        response = transport.post(url, headers={"Authorization": f"Bearer {endpoint['token']}"},
                                  json={"method": method, "arguments": arguments or {}})
        response.raise_for_status()
        result = loads(response.text)
    return result


def open_connection(project, **arguments):
    result = control_request(project, "connect_create", arguments)
    require(result["status"] == "success", result.get("message", "Connection is unavailable"),
            result.get("code", "connection_unavailable"))
    opened = webbrowser.open(result["url"], new=2)
    return {"status": "success", "connected": False, "browser_opened": opened,
            "url": result["url"], "message": "请在浏览器登录并确认项目授权，完成前不会启用。"}


def confirmation_message(reason, recording):
    if recording:
        if reason.startswith("default_yes_"):
            return "AiCA：本轮按默认 Yes 自动记录（原生确认未完成或不可用），未打开浏览器；附件与上传权限独立。"
        return "AiCA：本轮录制已开启。"
    messages = {"native_no": "原生确认返回 No", "native_declined": "原生客户端未完成确认",
                "native_cancelled": "原生确认已取消", "native_timeout": "原生确认超时",
                "native_invalid_response": "原生确认返回了无效答复", "browser_declined": "本机确认返回 No",
                "browser_cancelled": "本机确认页已关闭", "browser_expired": "本机确认已超时",
                "browser_unavailable": "本机确认页不可用", "browser_browser_unavailable": "浏览器未能打开确认页",
                "browser_connection_lost": "确认连接已断开"}
    message = "AiCA：" + messages.get(reason, "未获得本轮录制授权") + "，本轮未录制。"
    return message


class AgentConsent:
    def __init__(self, project, client="codex", default_yes=False):
        self.project = project
        self.client = client
        self.default_yes = default_yes
        self.bridge_id = None
        self.active_turn = None
        self.confirmation_reason = "not_confirmed"
        self.confirmation = "auto"
        self.lock = anyio.Lock()

    async def control(self, name, arguments=None, timeout=5):
        result = await anyio.to_thread.run_sync(lambda: control_request(self.project, name, arguments, timeout))
        require(result["status"] == "success", result.get("message", "Consent bridge failed"), result.get("code", "bridge_error"))
        return result

    async def start(self):
        result = await self.control("attach", {"client": self.client, "owner_pid": os.getppid()})
        self.bridge_id = result["bridge_id"]

    async def heartbeat(self):
        previous_bridge = None
        while True:
            await anyio.sleep(2)
            try:
                if self.bridge_id is None:
                    if previous_bridge:
                        await self.control("detach", {"bridge_id": previous_bridge})
                    await self.start()
                    previous_bridge = None
                else:
                    await self.control("heartbeat", {"bridge_id": self.bridge_id})
            except (ProtocolError, OSError, ValueError, httpx.HTTPError):
                previous_bridge = self.bridge_id or previous_bridge
                self.bridge_id = None
                self.active_turn = None

    async def ask(self, context, message, confirmation=None, default_yes=False):
        from mcp.shared.exceptions import MCPError

        mode = confirmation or self.confirmation
        default_yes = default_yes and mode == "native-default-yes"
        schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["Yes", "No"], "default": "Yes" if default_yes else "No",
                  "title": "请选择"}}, "required": ["choice"]}
        browser = mode == "browser"
        approved = False
        if not browser:
            try:
                with anyio.fail_after(10 if default_yes else 120):
                    reply = await context.session.elicit_form(message, schema, related_request_id=context.request_id)
                approved = reply.action == "accept" and reply.content == {"choice": "Yes"}
                reasons = {"decline": "native_declined", "cancel": "native_cancelled"}
                self.confirmation_reason = "native_yes" if approved else "native_no" if reply.action == "accept" and reply.content == {"choice": "No"} else reasons.get(reply.action, "native_invalid_response")
            except TimeoutError:
                self.confirmation_reason = "native_timeout"
            except (MCPError, RuntimeError):
                self.confirmation_reason = "native_unavailable"
                browser = mode not in {"native-default-yes", "conversation"}
        if default_yes and self.confirmation_reason in {"native_declined", "native_cancelled", "native_timeout", "native_unavailable"}:
            approved = True
            self.confirmation_reason = "default_yes_" + self.confirmation_reason
        if browser:
            from .hosts import browser_question

            try:
                reply = await anyio.to_thread.run_sync(lambda: browser_question(self.project, self.bridge_id, message))
                approved = reply.get("choice") is True and reply.get("state") == "accepted"
                self.confirmation_reason = "browser_" + reply.get("state", "unavailable")
            except (ProtocolError, OSError, ValueError, httpx.HTTPError):
                self.confirmation_reason = "browser_unavailable"
        return approved

    async def confirm(self, context, host_session_id, turn_id, confirmation="auto"):
        require(confirmation != "native-default-yes" or self.default_yes,
                "Enable --default-yes in the MCP startup configuration first", "default_yes_not_enabled")
        async with self.lock:
            self.confirmation = confirmation
            arguments = {"bridge_id": self.bridge_id, "host_session_id": host_session_id, "turn_id": turn_id}
            pending = {"state": "unavailable"}
            for attempt in range(40):
                pending = await self.control("pending", arguments)
                if pending["state"] != "unavailable":
                    break
                await anyio.sleep(0.05)
            if pending["state"] != "pending":
                return {"status": "success", "recording": pending["state"] == "recording", "state": pending["state"],
                        "systemMessage": "AiCA：本轮已按项目授权记录。" if pending.get("project_authorized") else
                                         "AiCA：本轮无待确认的新提交，未开启新的采集。"}
            self.active_turn = None
            require(confirmation != "conversation", "Conversation confirmation must use the blocking command Hook", "host_hook_required")
            policy_message = "默认 Yes；10 秒未选择、关闭或客户端无原生确认能力时自动记录，明确选择 No 不记录。不打开浏览器。\n" if confirmation == "native-default-yes" else "Yes 记录本轮，No 不记录。\n"
            approved = await self.ask(context, f"项目 {Path(self.project).name}：是否将本轮需求、AI 可见回复、支持的工具调用记录到 AiCA？\n"
                                      + policy_message + "附件另行选择；上传权限独立。\n"
                                      f"本轮请求预览：{pending['preview']}", confirmation, default_yes=True)
            reason = self.confirmation_reason
            result = await self.control("resolve", {**arguments, "challenge": pending["challenge"], "record": approved})
            if result["recording"]:
                self.active_turn = {"host_session_id": host_session_id, "turn_id": turn_id}
            result["confirmation_reason"] = reason
            result["systemMessage"] = confirmation_message(reason, result["recording"])
            return result

    async def select(self, context, paths, tool_use_id=None):
        active = await self.control("active", {"bridge_id": self.bridge_id})
        require(active.get("turn") is not None, "No unique authorized turn on this connection", "turn_not_authorized")
        turn = active["turn"]
        current = await self.control("pending", {"bridge_id": self.bridge_id, **turn})
        require(current["state"] == "recording", "This turn has ended", "turn_not_authorized")
        mode = "auto" if current.get("conversation") else self.confirmation
        if current.get("project_authorized"):
            approved = True
            self.confirmation_reason = "project_authorized"
        else:
            approved = await self.ask(context, "将以下文件加入本轮 AiCA 档案？\n" + "\n".join(paths), mode)
        if not approved:
            reason = self.confirmation_reason
            unavailable = reason not in {"native_no", "browser_declined"}
            return {"status": "error" if unavailable else "success", "selected": False, "artifacts": [],
                    "code": "artifact_confirmation_unavailable" if unavailable else "artifact_declined",
                    "confirmation_reason": reason, "message": "附件未采集：" + reason + "。直接复制到 .aica/artifacts 不构成登记；需成功确认并调用 AiCA 采集。"}
        arguments = {"bridge_id": self.bridge_id, **turn, "paths": paths}
        if tool_use_id is not None:
            arguments["tool_use_id"] = tool_use_id
        result = await self.control("capture", arguments, timeout=60)
        result["selected"] = True
        result["confirmation_reason"] = self.confirmation_reason
        return result

    async def upload(self, context):
        access = await self.control("project_access", {"action": "status"})
        if access["state"] != "unconfigured":
            require(access["state"] == "active", "Project uploading is paused", "project_paused")
            result = await self.control("project_access", {"action": "sync"})
            return result
        prepared = await self.control("upload_prepare", {"bridge_id": self.bridge_id}, timeout=120)
        arguments = {"bridge_id": self.bridge_id, "prepared_id": prepared["prepared_id"]}
        try:
            approved = await self.ask(context, prepared["message"], "auto")
            if not approved:
                return {"status": "success", "uploaded": False, "confirmation_reason": self.confirmation_reason}
            result = await self.control("upload_apply", arguments, timeout=300)
            return result
        finally:
            await self.control("upload_cancel", arguments)

    async def import_archive(self, context, **arguments):
        approved = await self.ask(context, f"单独授权导入历史文件 {arguments['path']} 到新目录 {arguments['target']}？\n"
                                 "这会恢复所选档案及附件，不授予本轮录制权限。")
        if not approved:
            return {"status": "success", "imported": False}
        result = await self.control("import", arguments, timeout=300)
        return result

    async def pull(self, context, **arguments):
        prepared = await self.control("pull_prepare", {"bridge_id": self.bridge_id, **arguments}, timeout=130)
        if prepared.get("completed"):
            return prepared["result"]
        identity = {"bridge_id": self.bridge_id, "prepared_id": prepared["prepared_id"]}
        try:
            approved = await self.ask(context,
                f"从 {arguments['server_url']} 拉取作品 {arguments['repo_id']} 的版本 {arguments['commit_id']}，覆盖当前项目？\n"
                f"本地 {prepared['local_node_count']} 节点；远端 {prepared['remote_node_count']} 节点、"
                f"{prepared['remote_artifact_count']} 附件。\n"
                "将先备份本地数据库与归档；本地未上传修改也会被替换。结束当前录制授权并暂停自动上传。"
                "不合并、不执行归档内容。", "auto")
            if not approved:
                return {"status": "success", "pulled": False, "confirmation_reason": self.confirmation_reason}
            result = await self.control("pull_apply", identity, timeout=130)
            self.active_turn = None
            return result
        finally:
            await self.control("pull_cancel", identity)

    async def close(self):
        if self.bridge_id:
            await self.control("detach", {"bridge_id": self.bridge_id})
        self.active_turn = None
