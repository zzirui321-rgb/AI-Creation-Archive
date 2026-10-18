import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from aica_schema.protocol import ProtocolError, digest, dumps, loads, require, utc_now
from aica_schema.recording import require_platform_config

from .files import atomic_write, private_directory
from .transfer import build_bundle, require_upload_url, upload_bundle, verify_bundle


class PlatformSync:
    def __init__(self, recorder, transport=None):
        self.recorder = recorder
        self.project = recorder.project
        self.config_path = self.project / ".aicaconfig"
        self.transport = transport
        self.lock = threading.RLock()
        self.upload_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.active_binding = None

    def read_config(self):
        require(not self.config_path.is_symlink(), "Platform configuration cannot be a symlink", "unsafe_config")
        if not self.config_path.exists():
            return None
        with self.config_path.open("rb") as stream:
            raw = stream.read(16385)
        require(len(raw) <= 16384, "Platform configuration exceeds 16 KiB", "invalid_config")
        config = loads(raw.decode("utf-8"))
        require_platform_config(config)
        require_upload_url(config["server_url"], config["allow_loopback_http"])
        destination = urlsplit(config["server_url"])
        require(destination.path == "", "Platform URL must be an origin without a path", "unsafe_destination")
        return config

    def binding_id(self, config):
        identity = {key: value for key, value in config.items() if key != "auto_upload"}
        value = digest([identity, str(self.project), self.recorder.stream_id])
        return value

    def directory(self, config):
        directory = self.recorder.directory
        for part in ("outboxes", "platform", self.binding_id(config)):
            directory = directory / part
            private_directory(directory)
        return directory

    def read_state(self, config):
        path = self.directory(config) / "state.json"
        require(not path.is_symlink(), "Platform state cannot be a symlink", "unsafe_config")
        if path.exists():
            state = loads(path.read_text(encoding="utf-8"))
            require(isinstance(state, dict) and set(state) == {"last_uploaded_seq", "last_uploaded_at", "pending",
                    "failures", "next_attempt_at", "last_error", "receipt"}
                    and type(state["last_uploaded_seq"]) is int and type(state["failures"]) is int
                    and isinstance(state["next_attempt_at"], (int, float)),
                    "Invalid platform recovery state", "invalid_sync_state")
            return state
        return {"last_uploaded_seq": -1, "last_uploaded_at": 0, "pending": None, "failures": 0,
                "next_attempt_at": 0, "last_error": None, "receipt": None}

    def write_state(self, config, state):
        atomic_write(self.directory(config) / "state.json", dumps(state).encode("utf-8"))

    def authorized(self, config):
        path = self.directory(config) / "authorization.json"
        require(not path.is_symlink(), "Upload authorization cannot be a symlink", "unsafe_config")
        if not path.exists():
            return False
        grant = loads(path.read_text(encoding="utf-8"))
        allowed = isinstance(grant, dict) and grant.get("binding_id") == self.binding_id(config)
        return allowed

    def token(self, config):
        value = os.environ.get(config["token_env"], "")
        require(32 <= len(value) <= 512 and all(33 <= ord(char) <= 126 for char in value),
                "Set the platform token environment variable before starting or reconnecting MCP", "credential_missing")
        return value

    def bind(self, server_url, project_id, confirm_upload, token_env="AICA_UPLOAD_TOKEN", interval_seconds=5,
             allow_loopback_http=False):
        require(confirm_upload is True, "Binding requires approval to upload the current archive and future recordings",
                "upload_not_authorized")
        config = {"version": 1, "server_url": server_url.rstrip("/"), "project_id": project_id,
                  "token_env": token_env, "auto_upload": True, "interval_seconds": interval_seconds,
                  "allow_loopback_http": allow_loopback_http}
        require_platform_config(config)
        require_upload_url(config["server_url"], allow_loopback_http)
        require(urlsplit(config["server_url"]).path == "", "Platform URL must be an origin", "unsafe_destination")
        self.token(config)
        with self.lock:
            require(not self.config_path.is_symlink(), "Platform configuration cannot be a symlink", "unsafe_config")
            grant = {"binding_id": self.binding_id(config), "authorized_at": utc_now()}
            atomic_write(self.directory(config) / "authorization.json", dumps(grant).encode("utf-8"))
            atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
        self.wake_event.set()
        result = self.status()
        result["message"] = "Bound with upload consent; the background worker will upload the current archive and subsequent recorded changes"
        return result

    def status(self):
        with self.lock:
            config = self.read_config()
            if config is None:
                return {"status": "success", "state": "unbound", "auto_upload": False}
            granted = self.authorized(config)
            state = self.read_state(config)
            mode = "waiting"
            if not granted:
                mode = "authorization_required"
            elif not config["auto_upload"]:
                mode = "paused"
            elif self.active_binding == self.binding_id(config):
                mode = "uploading"
            elif state["last_error"]:
                mode = "blocked" if state["failures"] >= 5 else "retry_wait"
            elif state["receipt"]:
                mode = "synced"
            with self.recorder.mutex:
                current_seq = self.recorder.document["snapshot"]["commit_seq"]
            if mode == "synced" and current_seq > state["last_uploaded_seq"]:
                mode = "pending"
            return {"status": "success", "state": mode, "auto_upload": config["auto_upload"],
                    "server_url": config["server_url"], "project_id": config["project_id"],
                    "authorized": granted, "current_seq": current_seq,
                    "last_uploaded_seq": state["last_uploaded_seq"], "pending": state["pending"] is not None,
                    "failures": state["failures"], "next_attempt_at": state["next_attempt_at"],
                    "last_error": state["last_error"], "receipt": state["receipt"]}

    def set_enabled(self, enabled, confirm_upload=False):
        require(not enabled or confirm_upload is True, "Resuming requires upload approval", "upload_not_authorized")
        with self.lock:
            config = self.read_config()
            require(config is not None, "Bind this project to a platform first", "platform_unbound")
            require(self.authorized(config), "Bind again to authorize this destination", "upload_not_authorized")
            if enabled:
                self.token(config)
            config["auto_upload"] = enabled
            atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
        if enabled:
            self.sync_now()
        result = self.status()
        return result

    def sync_now(self):
        with self.lock:
            config = self.read_config()
            require(config is not None, "Bind this project to a platform first", "platform_unbound")
            require(config["auto_upload"] and self.authorized(config), "Resume authorized uploading first",
                    "upload_not_authorized")
            state = self.read_state(config)
            state.update({"failures": 0, "next_attempt_at": 0, "last_error": None})
            self.write_state(config, state)
        self.wake_event.set()
        result = self.status()
        result["message"] = "Queued; inspect platform status for a verified remote receipt"
        return result

    def pending_bundle(self, config, state):
        directory = self.directory(config)
        path = directory / "pending.zip"
        require(not path.is_symlink(), "Pending bundle cannot be a symlink", "unsafe_config")
        if not path.exists():
            require(state["pending"] is None, "Pending bundle is missing; restore it before retrying",
                    "invalid_sync_state")
            with self.recorder.mutex:
                sequence = self.recorder.document["snapshot"]["commit_seq"]
                if sequence <= state["last_uploaded_seq"] or self.stop_event.is_set():
                    return None
                snapshot = self.recorder.flush()
                raw = (self.project / "aica.json").read_bytes()
            with tempfile.TemporaryDirectory(prefix="aica-platform-bundle-") as temporary:
                source = Path(temporary) / "snapshot.zip"
                build_bundle(self.project, source, snapshot["sha256"], raw)
                descriptor, partial_name = tempfile.mkstemp(prefix=".pending-", dir=directory)
                partial = Path(partial_name)
                try:
                    with os.fdopen(descriptor, "wb") as outgoing, source.open("rb") as incoming:
                        shutil.copyfileobj(incoming, outgoing)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    os.replace(partial, path)
                finally:
                    partial.unlink(missing_ok=True)
        report = verify_bundle(path)
        with zipfile.ZipFile(path) as bundle:
            snapshot = loads(bundle.read("aica.json").decode("utf-8"))["snapshot"]
        require(snapshot["stream_id"] == self.recorder.stream_id, "Pending bundle belongs to another recording stream",
                "invalid_sync_state")
        pending = {"seq": snapshot["commit_seq"], "bundle_sha256": report["bundle_sha256"],
                   "archive_sha256": report["archive_sha256"]}
        require(state["pending"] is None or state["pending"] == pending,
                "Pending bundle differs from its durable identity", "invalid_sync_state")
        state["pending"] = pending
        with self.lock:
            self.write_state(config, state)
        return path

    def run_once(self):
        if not self.upload_lock.acquire(blocking=False):
            return
        config = None
        state = None
        try:
            with self.lock:
                config = self.read_config()
                if config is None or not config["auto_upload"] or not self.authorized(config):
                    return
                state = self.read_state(config)
                if state["failures"] >= 5 or time.time() < state["next_attempt_at"]:
                    return
                self.active_binding = self.binding_id(config)
            token = self.token(config)
            path = self.pending_bundle(config, state)
            if path is not None:
                with self.lock:
                    current = self.read_config()
                    allowed = current is not None and current["auto_upload"] and self.authorized(current)
                    if not allowed or self.binding_id(current) != self.binding_id(config) or self.stop_event.is_set():
                        return
                url = config["server_url"] + "/api/projects/" + config["project_id"] + "/imports"
                result = upload_bundle(path, url, token, True, self.transport, config["allow_loopback_http"])
                require(result["receipt"]["project_id"] == config["project_id"],
                        "Receipt identifies another platform project", "invalid_receipt")
                state.update({"last_uploaded_seq": state["pending"]["seq"], "last_uploaded_at": time.time(),
                              "receipt": result["receipt"], "pending": None, "failures": 0, "last_error": None,
                              "next_attempt_at": time.time() + config["interval_seconds"]})
                with self.lock:
                    self.write_state(config, state)
                    path.unlink()
        except (ProtocolError, OSError, ValueError, httpx.HTTPError) as error:
            if config is not None and state is not None:
                state["failures"] += 1
                state["last_error"] = {"code": getattr(error, "code", "sync_failed"),
                                       "message": "Upload not confirmed; inspect destination, credentials and recorded artifacts, then retry"}
                state["next_attempt_at"] = time.time() + min(300, 5 * 2 ** min(state["failures"] - 1, 6))
                with self.lock:
                    self.write_state(config, state)
        finally:
            with self.lock:
                self.active_binding = None
            self.upload_lock.release()

    def loop(self):
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except (ProtocolError, OSError, ValueError):
                pass
            self.wake_event.wait(1)
            self.wake_event.clear()

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=2)
