import os
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, utc_now
from aica_schema.recording import require_platform_config, require_project_connection

from .consent import enable_turn_policy
from .credentials import Credentials
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
        self.reviews = {}
        self.credentials = Credentials(self.project, recorder.stream_id)
        self.bidirectional = None

    def per_turn(self):
        result = ((self.recorder.directory / "recording-policy.json").exists()
                  and self.project_access()["state"] != "active")
        return result

    def project_access(self):
        config = self.read_config()
        saved = self.credentials.read(config) if config else None
        return {"state": "active" if saved["enabled"] else "paused", "grant_id": saved["grant_id"]} if saved else {"state": "unconfigured"}

    def connect(self, config, credential):
        require_platform_config(config)
        require_project_connection(credential)
        require_upload_url(config["server_url"], config["allow_loopback_http"])
        with self.lock:
            previous = self.credentials.read(config)
            grant_path = self.directory(config) / "authorization.json"
            policy_path = self.recorder.directory / "recording-policy.json"
            paths = (grant_path, policy_path, self.config_path)
            for path in paths:
                require(not path.is_symlink(), "Connection settings cannot be symlinks", "unsafe_config")
            originals = {path: path.read_bytes() if path.exists() else None for path in paths}
            self.credentials.write(config, credential)
            try:
                grant = {"binding_id": self.binding_id(config), "authorized_at": utc_now()}
                atomic_write(grant_path, dumps(grant).encode("utf-8"))
                atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
                enable_turn_policy(self.project)
            except BaseException:
                if previous:
                    self.credentials.write(config, previous)
                else:
                    self.credentials.delete(config)
                for path, content in originals.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, content)
                raise
        self.wake_event.set()

    def set_project_access(self, action):
        remote_revoked = False
        with self.lock:
            config = self.read_config()
            saved = self.credentials.read(config) if config else None
            require(saved is not None, "Connect this project first", "project_not_authorized")
            if action == "disconnect":
                self.credentials.delete(config)
                (self.directory(config) / "authorization.json").unlink(missing_ok=True)
            else:
                require(action in {"pause", "resume"}, "Unknown project access action")
                if action == "resume":
                    self.credentials.token(config)
                saved["enabled"] = action == "resume"
                self.credentials.write(config, saved)
            config["auto_upload"] = action == "resume"
            atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
            for identifier, item in list(self.reviews.items()):
                self.cancel_upload(item["bridge_id"], identifier)
        self.wake_event.set()
        if action == "resume":
            self.sync_now()
        elif action == "disconnect" and saved["login_expires_at"] > time.time():
            try:
                url = config["server_url"] + "/api/mcp/upload-tokens/" + saved["upload_token_id"]
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                    response = client.delete(url, headers={"Authorization": "Bearer " + saved["login_token"]})
                    remote_revoked = response.status_code == 200
            except httpx.HTTPError:
                pass
        return {"status": "success", "project_access": self.project_access()["state"],
                "remote_token_revoked": remote_revoked if action == "disconnect" else None,
                "message": "已停止后续录制与新上传；已发出的请求无法撤回。" if action != "resume" else "已恢复项目授权。"}

    def prepare_upload(self, bridge_id):
        with self.lock:
            for identifier, item in list(self.reviews.items()):
                if time.monotonic() - item["created"] > 300:
                    self.cancel_upload(item["bridge_id"], identifier)
            require(len(self.reviews) < 4, "Finish or cancel a pending upload first", "upload_busy")
            config = self.read_config()
            require(config is not None, "Bind this project to a platform first", "platform_unbound")
            self.token(config)
            state = self.read_state(config)
            with self.recorder.mutex:
                snapshot_id = self.recorder.document["snapshot"]["snapshot_id"]
                if state["pending"] is None:
                    snapshot = self.recorder.flush()
                    raw = (self.project / "aica.json").read_bytes()
            temporary = tempfile.TemporaryDirectory(prefix="aica-upload-review-")
            path = Path(temporary.name) / "snapshot.zip"
            try:
                if state["pending"] is None:
                    report = build_bundle(self.project, path, snapshot["sha256"], raw)
                else:
                    pending = self.directory(config) / "pending.zip"
                    require(not pending.is_symlink(), "Pending upload cannot be a symlink", "unsafe_config")
                    shutil.copyfile(pending, path)
                    report = verify_bundle(path)
                    require(report["bundle_sha256"] == state["pending"]["bundle_sha256"],
                            "Pending upload was changed", "invalid_sync_state")
                    with zipfile.ZipFile(path) as bundle:
                        raw = bundle.read("aica.json")
            except BaseException:
                temporary.cleanup()
                raise
            document = loads(raw.decode())
            identifier = new_id()
            self.reviews[identifier] = {"bridge_id": bridge_id, "config": config, "binding_id": self.binding_id(config),
                                        "created": time.monotonic(), "temporary": temporary, "path": path,
                                        "snapshot_id": snapshot_id,
                                        "seq": document["snapshot"]["commit_seq"], "report": report}
            images = sum(item["mime_type"].startswith("image/") for item in document["artifacts"])
            message = (f"是否上传本轮结束后的完整 AiCA 档案？\n目标：{config['server_url']} / {config['project_id']}\n"
                       f"{len(document['graph']['nodes'])} 个节点，{len(document['artifacts'])} 个已登记附件（{images} 张图片）。\n"
                       "包含此前已记录但尚未上传的内容；只上传此次快照，下一轮需重新确认。No 不上传。")
            if state["pending"] is not None:
                message = (f"上次上传未取得确认回执。是否重试同一份快照（序号 {document['snapshot']['commit_seq']}）？\n"
                           f"目标：{config['server_url']} / {config['project_id']}\n"
                           f"{len(document['artifacts'])} 个附件（{images} 张图片）。不含此快照之后的新修改；新修改需要另行确认上传。")
            return {"status": "success", "prepared_id": identifier, "message": message,
                    "node_count": len(document["graph"]["nodes"]), "artifact_count": len(document["artifacts"]),
                    "image_count": images, "archive_sha256": report["archive_sha256"]}

    def cancel_upload(self, bridge_id, prepared_id):
        with self.lock:
            item = self.reviews.get(prepared_id)
            require(item is None or item["bridge_id"] == bridge_id, "Upload belongs to another connection", "not_authorized")
            if item:
                self.reviews.pop(prepared_id)
                item["temporary"].cleanup()
        return {"status": "success", "uploaded": False}

    def apply_upload(self, bridge_id, prepared_id):
        require(self.upload_lock.acquire(blocking=False), "An upload is already in flight", "upload_busy")
        try:
            with self.lock:
                item = self.reviews.get(prepared_id)
                require(item is not None and item["bridge_id"] == bridge_id and time.monotonic() - item["created"] < 300,
                        "Upload confirmation expired", "not_authorized")
                config = self.read_config()
                require(config is not None and self.binding_id(config) == item["binding_id"],
                        "Platform binding changed; review the upload again", "upload_conflict")
                with self.recorder.mutex:
                    require(self.recorder.document["snapshot"]["snapshot_id"] == item["snapshot_id"],
                            "Archive changed after review; confirm the new snapshot", "upload_conflict")
                token = self.token(config)
                url = config["server_url"] + "/api/projects/" + config["project_id"] + "/imports"
                state = self.read_state(config)
                pending = {"seq": item["seq"], "bundle_sha256": item["report"]["bundle_sha256"],
                           "archive_sha256": item["report"]["archive_sha256"]}
                require(verify_bundle(item["path"])["bundle_sha256"] == pending["bundle_sha256"],
                        "Reviewed upload bytes changed", "upload_conflict")
                require(state["pending"] is None or state["pending"] == pending,
                        "Another snapshot is pending; review it before retrying", "upload_conflict")
                path = self.directory(config) / "pending.zip"
                atomic_write(path, item["path"].read_bytes())
                state["pending"] = pending
                self.write_state(config, state)
                try:
                    result = upload_bundle(path, url, token, True, self.transport, config["allow_loopback_http"])
                except (ProtocolError, OSError, ValueError, httpx.HTTPError):
                    state["failures"] += 1
                    state["last_error"] = {"code": "upload_unconfirmed", "message": "Confirm a retry of the saved snapshot"}
                    self.write_state(config, state)
                    raise
                require(result["receipt"]["project_id"] == config["project_id"],
                        "Receipt identifies another platform project", "invalid_receipt")
                state.update(last_uploaded_seq=item["seq"], last_uploaded_at=time.time(), receipt=result["receipt"],
                             failures=0, last_error=None, next_attempt_at=0, pending=None)
                self.write_state(config, state)
                path.unlink()
                self.cancel_upload(bridge_id, prepared_id)
                result.update(uploaded=True, auto_upload=False)
                return result
        finally:
            self.upload_lock.release()

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
        if self.credentials.read(config) is not None:
            value = self.credentials.token(config)
            return value
        value = os.environ.get(config["token_env"], "")
        require(32 <= len(value) <= 512 and all(33 <= ord(char) <= 126 for char in value),
                "Set the platform token environment variable before starting or reconnecting MCP", "credential_missing")
        return value

    def bind(self, server_url, project_id, confirm_upload, token_env="AICA_UPLOAD_TOKEN", interval_seconds=5,
             allow_loopback_http=False):
        require(confirm_upload is True, "Binding requires approval to upload the current archive and future recordings",
                "upload_not_authorized")
        config = {"version": 1, "server_url": server_url.rstrip("/"), "project_id": project_id,
                  "token_env": token_env, "auto_upload": not self.per_turn(), "interval_seconds": interval_seconds,
                  "allow_loopback_http": allow_loopback_http}
        require_platform_config(config)
        require_upload_url(config["server_url"], allow_loopback_http)
        require(urlsplit(config["server_url"]).path == "", "Platform URL must be an origin", "unsafe_destination")
        self.token(config)
        with self.lock:
            require(not self.config_path.is_symlink(), "Platform configuration cannot be a symlink", "unsafe_config")
            if not self.per_turn():
                grant = {"binding_id": self.binding_id(config), "authorized_at": utc_now()}
                atomic_write(self.directory(config) / "authorization.json", dumps(grant).encode("utf-8"))
            atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
        self.wake_event.set()
        result = self.status()
        result["message"] = "Bound with upload consent; the background worker will upload the current archive and subsequent recorded changes"
        if self.per_turn():
            result["message"] = "Destination saved; each snapshot requires separate upload confirmation"
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
            if self.per_turn():
                mode = "synced" if state["receipt"] and current_seq == state["last_uploaded_seq"] else "confirmation_required"
            access = self.project_access()
            if access["state"] == "paused":
                mode = "paused"
            saved = self.credentials.read(config)
            sync_state = self.bidirectional.read_state(config) if self.bidirectional and "sync" in config else None
            if sync_state:
                mode = "paused" if sync_state["paused"] or not config["auto_upload"] or access["state"] != "active" else sync_state["status"]
            return {"status": "success", "state": mode, "auto_upload": config["auto_upload"] and not self.per_turn(),
                    "sync": sync_state,
                    "project_access": access["state"],
                    "credential_state": "expired" if saved and saved["upload_expires_at"] <= time.time() else "saved" if saved else "environment",
                    "server_url": config["server_url"], "project_id": config["project_id"],
                    "authorized": granted, "current_seq": current_seq,
                    "last_uploaded_seq": state["last_uploaded_seq"], "pending": state["pending"] is not None,
                    "failures": state["failures"], "next_attempt_at": state["next_attempt_at"],
                    "last_error": state["last_error"], "receipt": state["receipt"]}

    def set_enabled(self, enabled, confirm_upload=False):
        require(not enabled or not self.per_turn(), "Each turn requires aica_sync_platform confirmation", "upload_confirmation_required")
        require(not enabled or confirm_upload is True, "Resuming requires upload approval", "upload_not_authorized")
        with self.lock:
            config = self.read_config()
            require(config is not None, "Bind this project to a platform first", "platform_unbound")
            require(not enabled or self.authorized(config), "Bind again to authorize this destination", "upload_not_authorized")
            if enabled:
                self.token(config)
            config["auto_upload"] = enabled
            atomic_write(self.config_path, dumps(config, pretty=True).encode("utf-8"))
            if not enabled:
                for identifier, item in list(self.reviews.items()):
                    self.cancel_upload(item["bridge_id"], identifier)
        if enabled:
            self.sync_now()
        result = self.status()
        return result

    def sync_now(self):
        require(not self.per_turn(), "Use the MCP consent bridge to confirm this snapshot", "upload_confirmation_required")
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
        config = self.read_config()
        if self.bidirectional and config and "sync" in config:
            self.bidirectional.run_once(config)
            return
        if self.per_turn():
            return
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
        with self.lock:
            for identifier, item in list(self.reviews.items()):
                self.cancel_upload(item["bridge_id"], identifier)
