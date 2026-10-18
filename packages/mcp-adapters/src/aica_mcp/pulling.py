import copy
import os
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from aica_schema.protocol import (
    EMBEDDED_EXTENSION,
    PORTABLE_JSON_LIMIT,
    ProtocolError,
    digest,
    dumps,
    embedded_objects,
    loads,
    new_id,
    require,
    require_archive,
)

from .files import atomic_write, file_hash, private_directory
from .transfer import (
    ARCHIVE_LIMIT,
    ARTIFACT_LIMIT,
    BUNDLE_LIMIT,
    content_hash,
    read_portable_json,
    require_upload_url,
    verify_bundle,
)


def download_objects(document, server_url, repo_id, commit_id, token, objects, transport=None):
    total = sum(len(content) for content in objects.values())
    try:
        with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False,
                          transport=transport) as client:
            for artifact in document["artifacts"]:
                sha256 = artifact["sha256"]
                require(sha256 is not None, "Artifact has no content hash", "transfer_mismatch")
                if sha256 not in objects:
                    url = server_url.rstrip("/") + f"/api/repos/{repo_id}/artifacts/" + quote(artifact["id"], safe="")
                    content = bytearray()
                    with client.stream("GET", url, params={"commit_id": commit_id},
                                       headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}) as response:
                        require(response.status_code == 200, f"Artifact download rejected (HTTP {response.status_code})",
                                "download_rejected")
                        require(response.headers.get("content-encoding", "identity") == "identity",
                                "Compressed HTTP downloads are unsupported", "invalid_transfer")
                        for block in response.iter_bytes():
                            content.extend(block)
                            total += len(block)
                            require(len(content) <= ARTIFACT_LIMIT and total <= BUNDLE_LIMIT,
                                    "Downloaded artifacts exceed size limits", "transfer_limit")
                    require(content_hash(content) == sha256, "Artifact download does not match its hash", "transfer_mismatch")
                    objects[sha256] = bytes(content)
                require("size" not in artifact or len(objects[sha256]) == artifact["size"],
                        "Artifact download does not match its size", "transfer_mismatch")
    except httpx.HTTPError as error:
        raise ProtocolError("Artifact download interrupted; local project was not changed", "download_failed") from error
    return objects


def download_archive(server_url, repo_id, commit_id, token_env="AICA_DOWNLOAD_TOKEN", allow_loopback_http=False,
                     expected_sha256=None, transport=None, credential=None):
    require_upload_url(server_url, allow_loopback_http)
    require(urlsplit(server_url).path in {"", "/"}, "Platform URL must be an origin", "unsafe_destination")
    token = credential if credential is not None else os.environ.get(token_env, "")
    require(32 <= len(token) <= 512 and all(33 <= ord(char) <= 126 for char in token),
            "Set the download credential environment variable on the coordinator; upload-only tokens cannot pull",
            "credential_missing")
    url = server_url.rstrip("/") + f"/api/repos/{repo_id}/archive"
    with tempfile.TemporaryDirectory(prefix="aica-pull-") as temporary:
        path = Path(temporary) / "archive"
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False,
                              transport=transport) as client:
                with client.stream("GET", url, params={"commit_id": commit_id},
                                   headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity",
                                            "Accept": "application/json, application/vnd.aica.bundle+zip"}) as response:
                    require(response.status_code == 200, f"Archive download rejected (HTTP {response.status_code})",
                            "download_rejected")
                    require(response.headers.get("content-encoding", "identity") == "identity",
                            "Compressed HTTP downloads are unsupported", "invalid_transfer")
                    total = 0
                    with path.open("wb") as output:
                        for block in response.iter_bytes():
                            total += len(block)
                            require(total <= BUNDLE_LIMIT, "Download exceeds 512 MiB", "transfer_limit")
                            output.write(block)
        except httpx.HTTPError as error:
            raise ProtocolError("Archive download interrupted; local project was not changed", "download_failed") from error
        actual = file_hash(path)
        require(expected_sha256 is None or actual == expected_sha256,
                "Downloaded archive differs from the expected SHA-256", "transfer_mismatch")
        if zipfile.is_zipfile(path):
            report = verify_bundle(path)
            with zipfile.ZipFile(path) as bundle:
                document = loads(bundle.read("aica.json").decode("utf-8"))
                objects = {item["sha256"]: bundle.read(item["path"]) for item in report["manifest"]["artifacts"]}
        else:
            try:
                document, objects, raw = read_portable_json(path)
                del raw
            except ProtocolError as error:
                if error.code != "missing_embedded_artifacts":
                    raise
                require(path.stat().st_size <= PORTABLE_JSON_LIMIT, "Archive exceeds JSON size limit", "transfer_limit")
                document = loads(path.read_text(encoding="utf-8"))
                require_archive(document)
                metadata = copy.deepcopy(document)
                metadata.setdefault("extensions", {}).pop(EMBEDDED_EXTENSION, None)
                require(len(dumps(metadata).encode()) <= ARCHIVE_LIMIT and len(document["artifacts"]) <= 4096,
                        "Archive metadata exceeds limits", "transfer_limit")
                objects = embedded_objects(document)
                objects = download_objects(document, server_url, repo_id, commit_id, token, objects, transport)
        require_archive(document)
        document = copy.deepcopy(document)
        document.setdefault("extensions", {}).pop(EMBEDDED_EXTENSION, None)
        for artifact in document["artifacts"]:
            location = ".aica/artifacts/" + artifact["sha256"]
            if artifact["uri"] != location and location not in artifact.get("locations", []):
                artifact.setdefault("locations", []).append(location)
        require_archive(document)
    return {"document": document, "objects": objects, "download_sha256": actual}


class PlatformPull:
    def __init__(self, coordinator, transport=None):
        self.coordinator = coordinator
        self.transport = transport
        self.prepared = {}

    def prepare(self, bridge_id, request_id, expected_snapshot_id, **source):
        coordinator = self.coordinator
        recorder = coordinator.recorder
        coordinator.turns.heartbeat(bridge_id)
        identity = digest([request_id, source])
        with coordinator.platform.lock:
            config = coordinator.platform.read_config()
            require(config is not None and source["server_url"].rstrip("/") == config["server_url"].rstrip("/"),
                    "Pull must use the configured platform origin; configure the destination before sending credentials",
                    "unsafe_destination")
            require(not source.get("allow_loopback_http", False) or config["allow_loopback_http"],
                    "HTTP downloads must already be enabled in platform configuration", "unsafe_destination")
        with recorder.mutex:
            prior = recorder.connection.execute("SELECT value FROM metadata WHERE key='platform_pull_receipt'").fetchone()
            if prior:
                receipt = loads(prior["value"])
                if receipt["request_id"] == request_id:
                    require(receipt["identity"] == identity, "Pull request ID has different source", "id_conflict")
                    if recorder.snapshot_error:
                        recorder.flush()
                    return {"status": "success", "completed": True, "result": {**receipt["result"], "already_pulled": True}}
            require(recorder.document["snapshot"]["snapshot_id"] == expected_snapshot_id,
                    "Local archive changed; reload it before pulling", "pull_conflict")
        credential = None
        if coordinator.platform.credentials.read(config) is not None:
            credential = coordinator.platform.credentials.token(config, login=True)
        downloaded = download_archive(**source, transport=self.transport, credential=credential)
        with recorder.mutex:
            coordinator.turns.heartbeat(bridge_id)
            require(recorder.document["snapshot"]["snapshot_id"] == expected_snapshot_id,
                    "Local archive changed during download; review again", "pull_conflict")
            require(downloaded["document"]["metadata"]["project_id"] == recorder.document["metadata"]["project_id"],
                    "Remote archive belongs to another project; import it into a new directory instead", "project_mismatch")
            self.prepared = {key: item for key, item in self.prepared.items() if time.monotonic() - item["created"] < 300}
            require(len(self.prepared) < 4, "Too many pending pulls; cancel or finish one first", "pull_busy")
            identifier = new_id()
            self.prepared[identifier] = {**downloaded, "bridge_id": bridge_id, "request_id": request_id,
                                         "identity": identity, "expected_snapshot_id": expected_snapshot_id,
                                         "created": time.monotonic(), "source": source}
            archive = downloaded["document"]
            return {"status": "success", "completed": False, "prepared_id": identifier,
                    "local_node_count": len(recorder.document["graph"]["nodes"]),
                    "remote_node_count": len(archive["graph"]["nodes"]),
                    "remote_artifact_count": len(archive["artifacts"]), "download_sha256": downloaded["download_sha256"]}

    def cancel(self, bridge_id, prepared_id):
        with self.coordinator.recorder.mutex:
            item = self.prepared.get(prepared_id)
            require(item is None or item["bridge_id"] == bridge_id, "Pull belongs to another connection", "not_authorized")
            self.prepared.pop(prepared_id, None)
        return {"status": "success", "pulled": False}

    def apply(self, bridge_id, prepared_id):
        coordinator = self.coordinator
        recorder = coordinator.recorder
        require(coordinator.platform.upload_lock.acquire(blocking=False),
                "An upload is in flight; wait for it to finish before pulling", "pull_busy")
        try:
            with coordinator.platform.lock, recorder.mutex:
                coordinator.turns.heartbeat(bridge_id)
                item = self.prepared.get(prepared_id)
                require(item is not None and item["bridge_id"] == bridge_id
                        and time.monotonic() - item["created"] < 300, "Pull confirmation expired", "not_authorized")
                require(recorder.document["snapshot"]["snapshot_id"] == item["expected_snapshot_id"],
                        "Local archive changed after review; pull again to confirm the new conflict", "pull_conflict")
                require(not any(turn["state"] == "recording" and turn.get("bridge_id") != bridge_id
                                for turn in coordinator.turns.turns.values()),
                        "Another connection is recording this project", "pull_busy")
                for turn in coordinator.turns.turns.values():
                    coordinator.turns.close(turn, "platform_pull")
                result = self.replace(item)
                self.prepared.pop(prepared_id, None)
                return result
        finally:
            coordinator.platform.upload_lock.release()

    def replace(self, item):
        recorder = self.coordinator.recorder
        recorder.flush()
        backup = recorder.directory / "backups" / ("pull-" + digest(item["request_id"])[:32])
        private_directory(backup.parent)
        require(not backup.exists(), "Pull backup already exists; inspect it before retrying", "pull_recovery_required")
        private_directory(backup)
        atomic_write(backup / "aica.json", (recorder.project / "aica.json").read_bytes())
        connection = sqlite3.connect(backup / "recording.sqlite3")
        try:
            os.chmod(backup / "recording.sqlite3", 0o600)
            recorder.connection.backup(connection)
        finally:
            connection.close()
        config_path = recorder.project / ".aicaconfig"
        if config_path.exists():
            with self.coordinator.platform.lock:
                config = self.coordinator.platform.read_config()
                atomic_write(backup / "aicaconfig.json", config_path.read_bytes())
                config["auto_upload"] = False
                atomic_write(config_path, dumps(config, pretty=True).encode())
        archive = copy.deepcopy(item["document"])
        archive.setdefault("extensions", {})["aica.imported_snapshot"] = copy.deepcopy(archive["snapshot"])
        archive["snapshot"] = {"stream_id": new_id(), "snapshot_id": new_id(), "commit_seq": 0,
                               "complete": archive["snapshot"]["complete"], "gaps": archive["snapshot"]["gaps"]}
        archive["metadata"]["source_mode"] = "imported"
        require_archive(archive)
        artifacts = recorder.directory / "artifacts"
        private_directory(artifacts)
        for sha256, content in item["objects"].items():
            path = artifacts / sha256
            if path.exists():
                require(not path.is_symlink() and content_hash(path.read_bytes()) == sha256,
                        "Existing local artifact differs from its hash", "artifact_conflict")
            else:
                atomic_write(path, content)
        result = {"status": "success", "pulled": True, "already_pulled": False,
                  "backup_path": backup.relative_to(recorder.project).as_posix(), "archive_path": "aica.json",
                  "node_count": len(archive["graph"]["nodes"]), "artifact_count": len(archive["artifacts"]),
                  "download_sha256": item["download_sha256"], "repo_id": item["source"]["repo_id"],
                  "commit_id": item["source"]["commit_id"], "recording": False, "auto_upload": False,
                  "executed": False, "requires_new_recording_consent": True,
                  "next_step": "Request fresh recording consent. Rebind the platform separately before uploading this new recording stream."}
        receipt = {"request_id": item["request_id"], "identity": item["identity"], "result": result}
        recorder.connection.execute("BEGIN IMMEDIATE")
        try:
            for table in ("events", "sessions", "requests", "checkpoints", "segments", "metadata"):
                recorder.connection.execute("DELETE FROM " + table)
            recorder.connection.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            recorder.set_metadata("seed", dumps(archive))
            recorder.set_metadata("snapshot_hash", content_hash((recorder.project / "aica.json").read_bytes()))
            recorder.set_metadata("platform_pull_receipt", dumps(receipt))
            recorder.connection.execute("COMMIT")
        except BaseException:
            recorder.connection.execute("ROLLBACK")
            raise
        recorder.seed = archive
        recorder.stream_id = archive["snapshot"]["stream_id"]
        from .credentials import Credentials

        self.coordinator.platform.credentials = Credentials(recorder.project, recorder.stream_id)
        recorder.record_cache = []
        recorder.recording_permission = None
        recorder.document = recorder.rebuild()
        self.coordinator.flushed_seq = -1
        try:
            recorder.flush()
        except (OSError, ProtocolError) as error:
            recorder.snapshot_error = "Pull committed but snapshot write failed; reconnect to recover from the recording database"
            raise ProtocolError(recorder.snapshot_error, "pull_recovery_required") from error
        return result
