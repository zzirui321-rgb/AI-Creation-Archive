import hmac
import os
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aica_schema.protocol import ProtocolError, dumps, loads, require, validate_archive
from aica_schema.recording import recording_contract, require_arguments, require_turn_control

from .approval import ApprovalPages
from .connection import ConnectionPages
from .files import atomic_write
from .importing import import_archive
from .platform import PlatformSync
from .pulling import PlatformPull
from .store import Recorder, error_result
from .sync import BidirectionalSync
from .transfer import build_json
from .turns import TurnGate


class Coordinator:
    def __init__(self, project):
        self.recorder = Recorder(project)
        self.token = secrets.token_urlsafe(32)
        self.control_token = secrets.token_urlsafe(32)
        self.server = None
        self.stop_event = threading.Event()
        self.flush_thread = None
        self.flushed_seq = self.recorder.document["snapshot"]["commit_seq"]
        self.platform = PlatformSync(self.recorder)
        self.turns = TurnGate(self.recorder, self.platform.project_access)
        self.approvals = ApprovalPages(self.turns)
        self.pulls = PlatformPull(self)
        self.connections = ConnectionPages(self)
        self.sync = BidirectionalSync(self)
        self.platform.bidirectional = self.sync

    def control_methods(self):
        methods = {"attach": self.turns.attach, "heartbeat": self.turns.heartbeat, "detach": self.turns.detach,
                   "hook": self.turns.hook, "pending": self.turns.pending, "resolve": self.turns.resolve,
                   "prepare_conversation": self.turns.prepare_conversation,
                   "conversation_submit": self.turns.conversation_submit,
                   "capture": self.turns.capture, "active": self.turns.active,
                   "browser_turn": self.approvals.for_turn, "approval_create": self.approvals.create,
                   "approval_status": self.approvals.status, "approval_cancel": self.approvals.cancel}
        methods.update(pull_prepare=self.pulls.prepare, pull_apply=self.pulls.apply, pull_cancel=self.pulls.cancel)
        methods.update(upload_prepare=self.platform.prepare_upload, upload_apply=self.platform.apply_upload,
                       upload_cancel=self.platform.cancel_upload)
        methods.update(connect_create=self.connections.create, project_access=self.project_access)
        methods["sync_control"] = self.sync.control
        return methods

    def project_access(self, action):
        if action == "status":
            return {"status": "success", **self.platform.project_access()}
        if action == "sync":
            result = self.platform.sync_now()
            return result
        result = self.platform.set_project_access(action)
        with self.recorder.mutex:
            for turn in self.turns.turns.values():
                self.turns.close(turn, "project_access_changed")
        return result

    def control_dispatch(self, name, arguments):
        policy = self.recorder.directory / "recording-policy.json"
        require_turn_control(name, arguments)
        require(name == "import" or name in self.control_methods(), "Coordinator does not support this operation; restart it and reconnect MCP", "unknown_method")
        if name in {"connect_create", "project_access", "sync_control"}:
            result = self.control_methods()[name](**arguments)
            return result
        require(policy.exists(), "Per-turn mode is not enabled", "turn_mode_required")
        if name in {"upload_prepare", "upload_apply", "upload_cancel"}:
            with self.recorder.mutex:
                self.turns.expire()
                self.turns.heartbeat(arguments["bridge_id"])
            result = self.control_methods()[name](**arguments)
            return result
        if name in {"pull_prepare", "pull_apply", "pull_cancel"}:
            result = self.control_methods()[name](**arguments)
            return result
        if name == "import":
            result = import_archive(self.recorder.project, **arguments)
            return result
        with self.recorder.mutex:
            methods = self.control_methods()
            result = methods[name](**arguments)
            if name == "hook" and result.get("export_requested"):
                try:
                    result["export"] = self.export_archive(portable=True)
                    count = result["export"]["artifact_count"]
                    images = sum(item["mime_type"].startswith("image/") for item in self.recorder.document["artifacts"])
                    result["export"]["image_count"] = images
                    result["systemMessage"] = f"AiCA：本轮记录已保存，已导出为 exports/aica.json；档案共含 {count} 个已登记附件。"
                    if not images:
                        result["systemMessage"] += "未登记图片；如本轮生成了图片，请先选择附件，工具目录中的文件不会自动入库。"
                    if self.platform.project_access()["state"] == "active":
                        self.platform.wake_event.set()
                        result["systemMessage"] += "已按项目授权排队自动上传；平台状态中的回执才表示上传成功。"
                    elif self.platform.config_path.exists():
                        result["upload_bridge_id"] = result.pop("bridge_id")
                    else:
                        result["systemMessage"] += "尚未绑定平台，未上传。"
                except (ProtocolError, OSError, sqlite3.Error) as error:
                    result["export"] = error_result(error)
                    result["systemMessage"] = "AiCA：本轮记录已保存，但导出失败：" + result["export"]["message"]
        if name in {"hook", "conversation_submit"} and arguments["event"]["hook_event_name"] in {"UserPromptSubmit", "Stop"}:
            config = self.platform.read_config()
            if config and "sync" in config:
                sync = self.sync.read_state(config)
                if sync["conflict"]:
                    message = "AiCA：两端都有未同步修改，已暂停同步并保留两端。请明确选择保留本地或平台版本，再用当前 conflict_id 解决。"
                    result["systemMessage"] = result.get("systemMessage", "") + "\n" + message
                elif sync["error"]:
                    result["systemMessage"] = result.get("systemMessage", "") + "\nAiCA：双向同步未完成，请查看 aica_sync_control 状态并恢复。"
        return result

    def dispatch(self, name, arguments):
        if name == "status":
            methods = sorted(set(self.control_methods()) & set(recording_contract()["turn_control"]))
            return {"status": "success", "project": str(self.recorder.project), "stream_id": self.recorder.stream_id,
                    "snapshot_error": self.recorder.snapshot_error, "platform_upload_version": 1,
                    "turn_recording_version": 2, "conversation_recording_version": 2,
                    "project_connection_version": 1,
                    "bidirectional_sync_version": 1, "host_routing_version": 1,
                    "project_access": self.platform.project_access()["state"],
                    "turn_control_methods": methods}
        if name == "shutdown":
            result = self.recorder.flush()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return result
        require_arguments(name, arguments)
        require(name not in {"aica_confirm_turn", "aica_select_turn_artifacts", "aica_pull_platform"},
                "Turn confirmation requires the authenticated MCP consent bridge", "turn_not_authorized")
        platform_methods = {"aica_bind_platform": self.platform.bind,
                            "aica_platform_status": self.platform.status,
                            "aica_pause_upload": lambda: self.platform.set_enabled(False),
                            "aica_resume_upload": lambda confirm_upload: self.platform.set_enabled(True, confirm_upload),
                            "aica_sync_platform": self.platform.sync_now}
        if name in platform_methods:
            result = platform_methods[name](**arguments)
            return result
        require(name != "aica_import_archive", "Use the MCP adapter or RecorderClient for local archive import")
        require(name != "aica_record_step", "Use the MCP adapter or RecorderClient for structured step recording")
        methods = {"aica_start_session": self.recorder.start_session,
                   "aica_check_consent": self.recorder.check_consent,
                   "aica_submit_event": self.recorder.append,
                   "aica_get_archive": self.recorder.read_archive,
                   "aica_get_revision": self.recorder.get_revision,
                   "aica_get_items": self.recorder.get_items,
                   "aica_check_references": self.recorder.check_references,
                   "aica_export_archive": self.export_archive,
                   "aica_get_events": self.recorder.get_events,
                   "aica_capture_artifact": self.recorder.capture_artifact,
                   "aica_validate_archive": lambda archive: validate_archive(archive)}
        result = methods[name](**arguments)
        return result

    def export_archive(self, portable=False, output=None):
        require(portable or output is None, "output requires portable=true")
        if not portable:
            result = self.recorder.flush()
            return result
        relative = Path(output or "exports/aica.json")
        target = (self.recorder.project / relative).resolve()
        require(not relative.is_absolute() and ".." not in relative.parts and target.is_relative_to(self.recorder.project),
                "Portable output must be a project-relative path", "unsafe_location")
        require(target != self.recorder.project / "aica.json" and not target.is_relative_to(self.recorder.directory)
                and target.suffix.lower() == ".json", "Choose a JSON export path outside live recorder state", "unsafe_location")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshot = self.recorder.flush()
        result = build_json(self.recorder.project, target, snapshot["sha256"])
        result["snapshot_sha256"] = snapshot["sha256"]
        return result

    def flush_loop(self):
        while not self.stop_event.wait(1):
            with self.recorder.mutex:
                self.turns.expire()
                self.approvals.expire()
                current_seq = self.recorder.document["snapshot"]["commit_seq"]
                if current_seq != self.flushed_seq or self.recorder.snapshot_error:
                    try:
                        self.recorder.flush()
                    except (OSError, sqlite3.Error, ProtocolError) as error:
                        self.recorder.snapshot_error = str(error)
                    else:
                        self.flushed_seq = current_seq

    def serve(self):
        coordinator = self

        class Handler(BaseHTTPRequestHandler):
            def respond(self, content, content_type="application/json", status=200):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(content)

            def valid_host(self):
                valid = self.headers.get("Host") == f"127.0.0.1:{coordinator.server.server_port}"
                return valid

            def do_GET(self):
                resources = {"/approval": ("approval.html", "text/html; charset=utf-8"),
                             "/approval.js": ("approval.js", "text/javascript; charset=utf-8"),
                             "/connect": ("connect.html", "text/html; charset=utf-8"),
                             "/connect.js": ("connect.js", "text/javascript; charset=utf-8")}
                if self.valid_host() and self.path in resources:
                    filename, content_type = resources[self.path]
                    self.respond(Path(__file__).with_name(filename).read_bytes(), content_type)
                else:
                    self.send_error(403)

            def approval_post(self):
                if not self.valid_host() or self.headers.get("Origin") != coordinator.approvals.origin:
                    self.send_error(403)
                else:
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        require(0 < size <= 8192 and self.headers.get("Content-Type", "").split(";")[0] == "application/json", "Invalid confirmation request")
                        body = loads(self.rfile.read(size).decode("utf-8"))
                        fields = {"nonce"} if self.path == "/approval/view" else {"nonce", "choice"}
                        require(isinstance(body, dict) and (set(body) == fields or self.path == "/approval/decision" and set(body) == fields | {"action"}), "Invalid confirmation fields")
                        with coordinator.recorder.mutex:
                            result = coordinator.approvals.view(**body) if self.path == "/approval/view" else coordinator.approvals.decide(**body)
                    except (ProtocolError, OSError, ValueError, sqlite3.Error) as error:
                        result = error_result(error)
                    self.respond(dumps(result).encode("utf-8"))

            def do_POST(self):
                if self.path.startswith("/connect/"):
                    if not self.valid_host() or self.headers.get("Origin") != coordinator.connections.origin:
                        self.send_error(403)
                    else:
                        try:
                            size = int(self.headers.get("Content-Length", "0"))
                            require(0 < size <= 8192 and self.headers.get("Content-Type", "").split(";")[0] == "application/json",
                                    "Invalid connection request")
                            body = loads(self.rfile.read(size).decode("utf-8"))
                            require(isinstance(body, dict), "Invalid connection request")
                            result = coordinator.connections.dispatch(self.path.removeprefix("/connect/"), **body)
                        except (ProtocolError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
                            result = error_result(error)
                        self.respond(dumps(result).encode("utf-8"))
                    return
                if self.path in {"/approval/view", "/approval/decision"}:
                    self.approval_post()
                    return
                control = self.path == "/control"
                expected = f"Bearer {coordinator.control_token if control else coordinator.token}"
                authorized = hmac.compare_digest(self.headers.get("Authorization", ""), expected)
                valid_host = self.headers.get("Host") == f"127.0.0.1:{coordinator.server.server_port}"
                if not authorized or not valid_host or self.headers.get("Origin") or self.path not in {"/rpc", "/control"}:
                    self.send_error(403)
                else:
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                        require(0 < size <= 16 * 1024 * 1024, "Request size must be between 1 byte and 16 MiB")
                        request = loads(self.rfile.read(size).decode("utf-8"))
                        require(isinstance(request, dict) and set(request) == {"method", "arguments"},
                                "RPC requires method and arguments")
                        require(isinstance(request["arguments"], dict), "arguments must be an object")
                        require(isinstance(request["method"], str), "method must be a string")
                        if control:
                            result = coordinator.control_dispatch(request["method"], request["arguments"])
                        elif request["method"] in {"aica_bind_platform", "aica_platform_status", "aica_pause_upload",
                                                 "aica_resume_upload", "aica_sync_platform"}:
                            result = coordinator.dispatch(request["method"], request["arguments"])
                        else:
                            with coordinator.recorder.mutex:
                                result = coordinator.dispatch(request["method"], request["arguments"])
                    except (ProtocolError, OSError, ValueError, sqlite3.Error) as error:
                        result = error_result(error)
                    content = dumps(result).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)

            def log_message(self, format, *arguments):
                pass

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.server.daemon_threads = True
            self.approvals.origin = f"http://127.0.0.1:{self.server.server_port}"
            self.connections.origin = self.approvals.origin
            endpoint = {"port": self.server.server_port, "token": self.token, "pid": os.getpid(),
                        "project": str(self.recorder.project), "stream_id": self.recorder.stream_id}
            atomic_write(self.recorder.directory / "endpoint.json", dumps(endpoint).encode("utf-8"))
            atomic_write(self.recorder.directory / "control.json", dumps({**endpoint, "token": self.control_token}).encode("utf-8"))
            self.flush_thread = threading.Thread(target=self.flush_loop, daemon=True)
            self.flush_thread.start()
            self.platform.start()
            self.server.serve_forever(poll_interval=0.1)
        finally:
            self.stop_event.set()
            self.platform.close()
            if self.flush_thread:
                self.flush_thread.join(timeout=5)
            if self.server:
                self.server.server_close()
            endpoint_path = self.recorder.directory / "endpoint.json"
            if endpoint_path.exists():
                current = loads(endpoint_path.read_text(encoding="utf-8"))
                if current.get("token") == self.token:
                    endpoint_path.unlink()
            control_path = self.recorder.directory / "control.json"
            if control_path.exists() and not control_path.is_symlink():
                current = loads(control_path.read_text(encoding="utf-8"))
                if current.get("token") == self.control_token:
                    control_path.unlink()
            with self.recorder.mutex:
                self.recorder.close()
