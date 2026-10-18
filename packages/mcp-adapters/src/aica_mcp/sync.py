import copy
import mimetypes
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from uuid import UUID

import httpx
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, require_archive, utc_now

from .files import atomic_write, file_hash, private_directory
from .pulling import download_archive
from .transfer import build_bundle, upload_bundle, verify_bundle


def archive_hash(document):
    value = copy.deepcopy(document)
    value.pop("snapshot", None)
    segments = {item["artifact_id"] for item in value.get("history", []) if item["kind"] == "events"}
    value["artifacts"] = [item for item in value["artifacts"] if item["id"] not in segments]
    value["history"] = [item for item in value.get("history", []) if item["kind"] != "events"]
    for artifact in value["artifacts"]:
        artifact.pop("uri", None)
        artifact.pop("locations", None)
    result = digest(value)
    return result


class BidirectionalSync:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.platform = coordinator.platform
        self.recorder = coordinator.recorder
        self.next_poll = 0

    def directory(self, config):
        directory = self.platform.directory(config)
        return directory

    def read_state(self, config):
        path = self.directory(config) / "sync.json"
        require(not path.is_symlink(), "Sync state cannot be a symlink", "unsafe_config")
        if path.exists():
            require(path.stat().st_size <= 2 * 1024 * 1024, "Sync state exceeds its size limit")
            value = loads(path.read_text(encoding="utf-8"))
            require(isinstance(value, dict) and set(value) == {"version", "head", "local_hash", "files", "status",
                    "paused", "failures", "retry_at", "error", "pending", "incoming", "conflict"}
                    and value["version"] == 1 and isinstance(value["files"], dict)
                    and type(value["paused"]) is bool and type(value["failures"]) is int,
                    "Invalid sync state; preserve it for recovery", "invalid_sync_state")
            return value
        return {"version": 1, "head": None, "local_hash": None, "files": {}, "status": "ready", "paused": False,
                "failures": 0, "retry_at": 0, "error": None, "pending": None, "incoming": None, "conflict": None}

    def save(self, config, state, control=False):
        with self.platform.lock:
            path = self.directory(config) / "sync.json"
            if path.exists() and not control:
                state["paused"] = self.read_state(config)["paused"]
            atomic_write(path, dumps(state).encode())

    def safe_path(self, name):
        require(isinstance(name, str) and 0 < len(name) <= 512 and "\\" not in name,
                "Invalid registered file path", "unsafe_sync_path")
        relative = Path(name)
        require(not relative.is_absolute() and not any(part.startswith(".") for part in relative.parts)
                and relative.as_posix() not in {"aica.json", "exports/aica.json"},
                "Registered file points outside the writable project files", "unsafe_sync_path")
        path = self.recorder.project
        for part in relative.parts:
            path = path / part
            require(not path.is_symlink(), "Sync file or parent cannot be a symlink", "unsafe_sync_path")
        require(path.resolve().is_relative_to(self.recorder.project) and (not path.exists() or path.is_file()),
                "Registered path is not a project file", "unsafe_sync_path")
        return path

    def file_map(self, document):
        result = {}
        artifacts = {item["id"]: item for item in document["artifacts"]}
        for artifact in document["artifacts"]:
            for name in [artifact["uri"], *artifact.get("locations", [])]:
                if not name.startswith((".aica/", "http://", "https://", "data:")):
                    self.safe_path(name)
                    result[name] = artifact
        for name, identifier in document.get("extensions", {}).get("aica.sync.files", {}).items():
            self.safe_path(name)
            require(identifier is None or identifier in artifacts, "Registered file references an unknown artifact")
            result[name] = artifacts[identifier] if identifier is not None else None
        require(len(result) <= 4096, "Too many registered files")
        return result

    def file_hashes(self, names):
        result = {}
        for name in names:
            path = self.safe_path(name)
            require(not path.exists() or path.stat().st_size <= 64 * 1024 * 1024, "Sync file exceeds 64 MiB")
            result[name] = file_hash(path) if path.exists() else None
        return result

    def local(self, state):
        with self.recorder.mutex:
            document = self.recorder.read_archive()["archive"]
            files = self.file_map(document)
            hashes = self.file_hashes(set(files) | set(state["files"]))
            return document, archive_hash(document), hashes

    def request(self, config, method, path, body=None):
        token = self.platform.credentials.token(config, login=True)
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False, transport=self.platform.transport) as client:
            with client.stream(method, config["server_url"] + "/api" + path,
                               headers={"Authorization": "Bearer " + token}, json=body) as response:
                require(response.status_code != 409, "Platform version changed during sync", "sync_conflict")
                require(response.status_code in {200, 201}, f"Sync request rejected (HTTP {response.status_code})", "sync_rejected")
                raw = bytearray()
                for block in response.iter_bytes():
                    raw.extend(block)
                    require(len(raw) <= 2 * 1024 * 1024, "Sync response exceeds its size limit")
        result = loads(raw.decode())
        require(isinstance(result, dict) and result.get("status") == "success", "Invalid sync response")
        return result

    def remote_head(self, config):
        settings = config["sync"]
        result = self.request(config, "GET", "/repos/" + settings["repo_id"] + "/branches")
        matches = [item for item in result["branches"] if item["name"] == settings["branch"]]
        require(len(matches) == 1, "Sync branch is missing", "sync_branch_missing")
        head = str(UUID(matches[0]["head"])) if matches[0]["head"] else None
        return head

    def conflict(self, config, state, head, local_hash, hashes, reason):
        identifier = digest([head, local_hash, hashes])
        state.update(status="conflict", conflict={"id": identifier, "remote_head": head, "local_hash": local_hash,
                                                 "files": hashes, "reason": reason}, error=None)
        self.save(config, state)

    def control(self, action="status", choice=None, conflict_id=None):
        acquired = action in {"resume", "resolve"}
        if acquired:
            require(self.platform.upload_lock.acquire(blocking=False), "Sync is applying a version; retry this control after it finishes", "sync_busy")
        try:
            with self.platform.lock:
                config = self.platform.read_config()
                require(config is not None and "sync" in config, "Connect and enable automatic bidirectional sync first", "sync_unconfigured")
                state = self.read_state(config)
                if action == "pause":
                    state["paused"] = True
                elif action == "resume":
                    state.update(paused=False, failures=0, retry_at=0, error=None)
                elif action == "resolve":
                    require(choice in {"local", "remote"} and state["conflict"] and state["conflict"]["id"] == conflict_id,
                            "Read the current conflict before choosing a version", "sync_conflict")
                    state["conflict"]["choice"] = choice
                    state.update(paused=False, failures=0, retry_at=0)
                else:
                    require(action == "status", "Unknown sync action")
                if action != "status":
                    self.save(config, state, control=True)
                    self.next_poll = 0
                    self.platform.wake_event.set()
                return {"status": "success", "sync": {**state, "repo_id": config["sync"]["repo_id"], "branch": config["sync"]["branch"]}}
        finally:
            if acquired:
                self.platform.upload_lock.release()

    def active(self, config):
        current = self.platform.read_config()
        require(current == config and config["auto_upload"] and self.platform.project_access()["state"] == "active"
                and not self.platform.stop_event.is_set(), "Project sync was paused or its binding changed", "sync_paused")
        require(not self.read_state(config)["paused"], "Automatic sync is paused", "sync_paused")
        credential = self.platform.credentials.read(config)
        require(credential.get("sync_repo_id") == config["sync"]["repo_id"] and credential.get("sync_files") is True,
                "Automatic sync and source file changes require project authorization", "sync_not_authorized")

    def capture_files(self, config, document, hashes):
        changed = [(name, artifact) for name, artifact in self.file_map(document).items()
                   if hashes.get(name) != (artifact["sha256"] if artifact else None)]
        if not changed:
            return
        with self.recorder.mutex:
            self.active(config)
            previous = self.recorder.recording_permission
            self.recorder.recording_permission = lambda: self.active(config)
            try:
                session = self.recorder.start_session(new_id(), True)
                source = new_id()
                sequence = 0
                for name, artifact in changed:
                    sequence += 1
                    envelope = {"event_id": new_id(), "source_id": source, "source_seq": sequence,
                                "session_id": session["session_id"], "agent_id": None, "type": "artifact.recorded",
                                "timestamp": utc_now(), "causal_parents": [], "payload": {}}
                    if hashes[name] is not None:
                        result = self.recorder.capture_artifact(session["session_token"], envelope, name,
                                                               mimetypes.guess_type(name)[0] or "application/octet-stream")
                        require(result["status"] == "success", "File observation could not be recorded")
                    else:
                        envelope.update(type="files.changed", payload={"files": {name: None}})
                        self.recorder.append(envelope, session["session_token"])
                sequence += 1
                envelope = {"event_id": new_id(), "source_id": source, "source_seq": sequence,
                            "session_id": session["session_id"], "agent_id": None, "type": "session.closed",
                            "timestamp": utc_now(), "causal_parents": [], "payload": {}}
                self.recorder.append(envelope, session["session_token"])
            finally:
                self.recorder.recording_permission = previous

    def upload(self, config, state, head):
        self.active(config)
        path = self.directory(config) / "sync-pending.zip"
        require(not path.is_symlink(), "Sync bundle cannot be a symlink", "unsafe_config")
        if state["pending"] is None:
            with self.recorder.mutex:
                self.recorder.flush()
                raw = (self.recorder.project / "aica.json").read_bytes()
                document = loads(raw.decode())
                hashes = {name: artifact["sha256"] if artifact else None for name, artifact in self.file_map(document).items()}
                snapshot_hash = archive_hash(document)
            if path.exists():
                receipt = self.platform.read_state(config).get("receipt")
                require(receipt and verify_bundle(path)["bundle_sha256"] == receipt["bundle_sha256"],
                        "Untracked pending sync bundle; inspect it before resuming", "invalid_sync_state")
                path.unlink()
            with tempfile.TemporaryDirectory(prefix="aica-sync-") as temporary:
                bundle = Path(temporary) / "pending.zip"
                report = build_bundle(self.recorder.project, bundle, archive_bytes=raw)
                atomic_write(path, bundle.read_bytes())
            state["pending"] = {"head": head, "local_hash": snapshot_hash, "files": hashes,
                                "bundle_sha256": report["bundle_sha256"], "receipt": None,
                                "sequence": document["snapshot"]["commit_seq"]}
            self.save(config, state)
        pending = state["pending"]
        require(verify_bundle(path)["bundle_sha256"] == pending["bundle_sha256"], "Pending sync bytes changed", "invalid_sync_state")
        if pending["receipt"] is None:
            result = upload_bundle(path, config["server_url"] + "/api/projects/" + config["project_id"] + "/imports",
                                   self.platform.token(config), True, self.platform.transport, config["allow_loopback_http"])
            require(result["receipt"]["project_id"] == config["project_id"], "Upload receipt identifies another project")
            pending["receipt"] = result["receipt"]
            self.save(config, state)
        self.active(config)
        result = self.request(config, "POST", "/repos/" + config["sync"]["repo_id"] + "/archive-commits",
                              {"branch": config["sync"]["branch"], "expected_head": pending["head"],
                               "project_id": config["project_id"], "import_id": pending["receipt"]["import_id"],
                               "message": "同步本地创作与已登记文件"})
        self.finish_upload(config, state, str(UUID(result["commit"]["id"])))

    def finish_upload(self, config, state, head):
        pending = state["pending"]
        upload_state = self.platform.read_state(config)
        upload_state.update(last_uploaded_seq=pending["sequence"], last_uploaded_at=time.time(), receipt=pending["receipt"],
                            failures=0, last_error=None, next_attempt_at=0, pending=None)
        self.platform.write_state(config, upload_state)
        state.update(head=head, local_hash=pending["local_hash"], files=pending["files"], pending=None,
                     conflict=None, error=None, status="synced", failures=0, retry_at=0)
        self.save(config, state)
        (self.directory(config) / "sync-pending.zip").unlink(missing_ok=True)

    def download(self, config, head):
        result = download_archive(config["server_url"], config["sync"]["repo_id"], head,
                                  allow_loopback_http=config["allow_loopback_http"], transport=self.platform.transport,
                                  credential=self.platform.credentials.token(config, login=True))
        return result

    def install_database(self, document, identifier):
        archive = copy.deepcopy(document)
        archive["snapshot"] = {**archive["snapshot"], "stream_id": self.recorder.stream_id, "snapshot_id": new_id(), "commit_seq": 0}
        require_archive(archive)
        self.recorder.connection.execute("BEGIN IMMEDIATE")
        try:
            for table in ("events", "sessions", "requests", "checkpoints", "segments", "metadata"):
                self.recorder.connection.execute("DELETE FROM " + table)
            self.recorder.connection.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            self.recorder.set_metadata("seed", dumps(archive))
            self.recorder.set_metadata("snapshot_hash", file_hash(self.recorder.project / "aica.json"))
            self.recorder.set_metadata("sync_install_id", identifier)
            self.recorder.connection.execute("COMMIT")
        except BaseException:
            self.recorder.connection.execute("ROLLBACK")
            raise
        self.recorder.seed = archive
        self.recorder.record_cache = []
        self.recorder.document = self.recorder.rebuild()
        self.coordinator.flushed_seq = -1
        self.recorder.flush()

    def recover(self, config, state):
        incoming = state["incoming"]
        if incoming is None:
            return
        identifier = str(UUID(incoming["id"]))
        backup = self.recorder.directory / "backups" / ("sync-" + identifier)
        require(not backup.is_symlink(), "Sync backup cannot be a symlink")
        row = self.recorder.connection.execute("SELECT value FROM metadata WHERE key='sync_install_id'").fetchone()
        if row and row["value"] == identifier:
            self.recorder.flush()
            state.update(head=incoming["head"], local_hash=archive_hash(self.recorder.read_archive()["archive"]),
                         files=incoming["files"], status="synced", incoming=None, conflict=None, failures=0, error=None)
        else:
            for name, saved in incoming["before"].items():
                path = self.safe_path(name)
                current = file_hash(path) if path.exists() else None
                require(current in {saved["hash"], incoming["files"].get(name)},
                        "A file changed during interrupted sync; preserve the backup and choose a version", "sync_recovery_conflict")
                if saved["hash"] is None:
                    path.unlink(missing_ok=True)
                else:
                    require(saved["object"].startswith("file-") and saved["object"][5:].isdigit(), "Invalid sync backup object")
                    content = (backup / saved["object"]).read_bytes()
                    require(digest_bytes(content) == saved["hash"], "Sync backup hash mismatch")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(path, content)
            state["incoming"] = None
        self.save(config, state)

    def pull(self, config, state, head, local_hash, hashes, forced=False):
        downloaded = self.download(config, head)
        document = downloaded["document"]
        desired = self.file_map(document)
        targets = {name: artifact["sha256"] if artifact else None for name, artifact in desired.items()}
        with self.platform.lock, self.recorder.mutex:
            self.active(config)
            _, current_hash, current_files = self.local(state)
            require(current_hash == local_hash and current_files == hashes and not self.busy(),
                    "Local content changed during download", "sync_local_changed")
            for name, sha in targets.items():
                path = self.safe_path(name)
                current = file_hash(path) if path.exists() else None
                expected = state["files"].get(name)
                if not forced and current != sha and current != expected:
                    self.conflict(config, state, head, current_hash, current_files, "local_file_changed:" + name)
                    return
            identifier = new_id()
            backup = self.recorder.directory / "backups" / ("sync-" + identifier)
            private_directory(backup)
            with sqlite3.connect(backup / "recording.sqlite3") as copy_database:
                self.recorder.connection.backup(copy_database)
            os.chmod(backup / "recording.sqlite3", 0o600)
            atomic_write(backup / "aica.json", (self.recorder.project / "aica.json").read_bytes())
            before = {}
            for index, name in enumerate(targets):
                path = self.safe_path(name)
                content = path.read_bytes() if path.exists() else None
                filename = "file-" + str(index)
                before[name] = {"hash": digest_bytes(content) if content is not None else None, "object": filename}
                if content is not None:
                    atomic_write(backup / filename, content)
            state["incoming"] = {"id": identifier, "head": head, "files": targets, "before": before}
            self.save(config, state)
            objects = self.recorder.directory / "artifacts"
            private_directory(objects)
            for sha, content in downloaded["objects"].items():
                atomic_write(objects / sha, content)
            for name, sha in targets.items():
                path = self.safe_path(name)
                if sha is None:
                    path.unlink(missing_ok=True)
                else:
                    content = downloaded["objects"][sha]
                    require(digest_bytes(content) == sha, "Downloaded file hash mismatch")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(path, content)
            self.install_database(document, identifier)
            self.recover(config, state)

    def busy(self):
        result = any(turn["state"] == "recording" for turn in self.coordinator.turns.turns.values())
        return result

    def run_once(self, config):
        if time.monotonic() < self.next_poll or not self.platform.upload_lock.acquire(blocking=False):
            return
        state = None
        head = None
        try:
            self.active(config)
            state = self.read_state(config)
            if state["paused"] or state["failures"] >= 5 or time.time() < state["retry_at"]:
                return
            with self.recorder.mutex:
                self.recover(config, state)
                if self.busy():
                    state["status"] = "waiting_for_turn"
                    self.save(config, state)
                    return
            self.next_poll = time.monotonic() + config["interval_seconds"]
            document, local_hash, hashes = self.local(state)
            head = self.remote_head(config)
            conflict = state["conflict"]
            if conflict:
                if conflict.get("choice") is None:
                    return
                require(conflict["id"] == digest([head, local_hash, hashes]), "Conflict changed; choose again", "sync_conflict")
                if conflict["choice"] == "remote":
                    require(head is not None, "Remote branch is empty")
                    self.abandon_pending(config, state)
                    self.save(config, state)
                    self.pull(config, state, head, local_hash, hashes, forced=True)
                    return
                self.abandon_pending(config, state)
                state.update(head=head, local_hash=None, conflict=None)
            if state["pending"]:
                if head != state["pending"]["head"]:
                    remote = self.download(config, head) if head else None
                    if remote and state["pending"]["receipt"] and archive_hash(remote["document"]) == state["pending"]["local_hash"]:
                        self.finish_upload(config, state, head)
                    else:
                        self.conflict(config, state, head, local_hash, hashes, "remote_changed_during_upload")
                    return
                self.upload(config, state, head)
                return
            local_changed = local_hash != state["local_hash"] or hashes != state["files"]
            remote_changed = head != state["head"]
            if remote_changed and local_changed:
                empty = not document["graph"]["nodes"] and not document["sessions"] and not document["artifacts"]
                if state["local_hash"] is None and empty:
                    self.pull(config, state, head, local_hash, hashes)
                else:
                    self.conflict(config, state, head, local_hash, hashes, "both_sides_changed")
            elif remote_changed:
                require(head is not None, "Remote branch was deleted", "sync_conflict")
                self.pull(config, state, head, local_hash, hashes)
            elif local_changed:
                self.capture_files(config, document, hashes)
                self.upload(config, state, head)
            else:
                state.update(status="synced", error=None, failures=0)
                self.save(config, state)
        except (ProtocolError, OSError, ValueError, KeyError, httpx.HTTPError) as error:
            if state is not None:
                code = getattr(error, "code", "sync_failed")
                if code in {"sync_conflict", "sync_local_changed", "sync_recovery_conflict"}:
                    try:
                        document, local_hash, hashes = self.local(state)
                        head = self.remote_head(config)
                        self.conflict(config, state, head, local_hash, hashes, code)
                    except (ProtocolError, OSError, ValueError, KeyError, httpx.HTTPError) as recovery_error:
                        code = getattr(recovery_error, "code", "sync_failed")
                    else:
                        return
                state["failures"] += 1
                state.update(status="error", error={"code": code, "message": "双向同步未完成；请查看状态，检查凭证、文件及备份后恢复。"},
                             retry_at=time.time() + min(300, 5 * 2 ** min(state["failures"] - 1, 6)))
                self.save(config, state)
        finally:
            self.platform.upload_lock.release()

    def abandon_pending(self, config, state):
        path = self.directory(config) / "sync-pending.zip"
        if path.exists():
            backup = self.recorder.directory / "backups" / ("sync-outgoing-" + new_id())
            private_directory(backup)
            atomic_write(backup / "pending.zip", path.read_bytes())
            path.unlink()
        state["pending"] = None
        self.save(config, state)


def digest_bytes(content):
    import hashlib

    value = hashlib.sha256(content).hexdigest()
    return value
