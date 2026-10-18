import hmac
import os
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aica_schema.protocol import ProtocolError, dumps, loads, require, validate_archive
from aica_schema.recording import require_arguments

from .files import atomic_write
from .platform import PlatformSync
from .store import Recorder, error_result
from .transfer import build_json


class Coordinator:
    def __init__(self, project):
        self.recorder = Recorder(project)
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.stop_event = threading.Event()
        self.flush_thread = None
        self.flushed_seq = self.recorder.document["snapshot"]["commit_seq"]
        self.platform = PlatformSync(self.recorder)

    def dispatch(self, name, arguments):
        if name == "status":
            return {"status": "success", "project": str(self.recorder.project), "stream_id": self.recorder.stream_id,
                    "snapshot_error": self.recorder.snapshot_error, "platform_upload_version": 1}
        if name == "shutdown":
            result = self.recorder.flush()
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return result
        require_arguments(name, arguments)
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
            def do_POST(self):
                expected = f"Bearer {coordinator.token}"
                authorized = hmac.compare_digest(self.headers.get("Authorization", ""), expected)
                valid_host = self.headers.get("Host") == f"127.0.0.1:{coordinator.server.server_port}"
                if not authorized or not valid_host or self.headers.get("Origin") or self.path != "/rpc":
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
                        if request["method"] in {"aica_bind_platform", "aica_platform_status", "aica_pause_upload",
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
            endpoint = {"port": self.server.server_port, "token": self.token, "pid": os.getpid(),
                        "project": str(self.recorder.project), "stream_id": self.recorder.stream_id}
            atomic_write(self.recorder.directory / "endpoint.json", dumps(endpoint).encode("utf-8"))
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
            with self.recorder.mutex:
                self.recorder.close()
