import copy
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from pathlib import Path

from aica_schema.protocol import (
    ProtocolError,
    digest,
    dumps,
    empty_archive,
    loads,
    new_id,
    require,
    require_archive,
    require_dag,
    require_definition_snapshot,
    utc_now,
)
from aica_schema.recording import require_event

from .files import (
    ProjectLock,
    atomic_write,
    file_hash,
    inspect_artifact,
    private_directory,
    project_file,
    read_artifact,
)
from .projection import project_events


class Recorder:
    def __init__(self, project):
        self.project = Path(project).resolve(strict=True)
        require(self.project.is_dir(), "Project must be an existing directory")
        self.directory = self.project / ".aica"
        private_directory(self.directory)
        self.lock = ProjectLock(self.directory / "recorder.lock")
        self.lock.acquire()
        self.mutex = threading.RLock()
        self.connection = None
        self.snapshot_error = None
        try:
            ignore_path = self.directory / ".gitignore"
            require(not ignore_path.is_symlink(), "Internal ignore file cannot be a symlink")
            if not ignore_path.exists():
                atomic_write(ignore_path, b"*\n")
            self.open_database()
            self.recover()
        except BaseException:
            self.close()
            raise

    def open_database(self):
        require(sqlite3.sqlite_version_info >= (3, 51, 3), "SQLite >=3.51.3 is required for recording")
        database = self.directory / "recording.sqlite3"
        require(not database.is_symlink(), "Recording database cannot be a symlink")
        self.connection = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
        os.chmod(database, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                source_id TEXT NOT NULL, source_seq INTEGER NOT NULL, session_id TEXT NOT NULL,
                body TEXT NOT NULL, content_hash TEXT NOT NULL, UNIQUE(source_id, source_seq)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, record INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, content_hash TEXT NOT NULL, response TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY, through_seq INTEGER NOT NULL, content_hash TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS segments (
                first_seq INTEGER PRIMARY KEY, last_seq INTEGER NOT NULL, artifact TEXT NOT NULL
            );
        """)
        row = self.connection.execute("SELECT value FROM metadata WHERE key='seed'").fetchone()
        if row is None:
            path = self.project / "aica.json"
            require(not path.is_symlink(), "aica.json cannot be a symlink")
            seed = loads(path.read_text(encoding="utf-8")) if path.exists() else empty_archive(self.project.name)
            require_archive(seed)
            if path.exists():
                seed.setdefault("extensions", {})["aica.imported_snapshot"] = copy.deepcopy(seed["snapshot"])
                seed["metadata"]["source_mode"] = "imported"
                seed["snapshot"] = {"stream_id": new_id(), "snapshot_id": new_id(), "commit_seq": 0,
                                    "complete": seed["snapshot"]["complete"], "gaps": seed["snapshot"]["gaps"]}
            self.connection.execute("INSERT INTO metadata VALUES ('seed', ?)", (dumps(seed),))
            if path.exists():
                self.set_metadata("snapshot_hash", file_hash(path))
        seed_row = self.connection.execute("SELECT value FROM metadata WHERE key='seed'").fetchone()
        self.seed = loads(seed_row["value"])
        self.stream_id = self.seed["snapshot"]["stream_id"]
        self.record_cache = self.load_records()

    def set_metadata(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))

    def recover(self):
        self.document = self.rebuild()
        running = [run["id"] for run in self.document["runs"] if run["state"] == "running"]
        if running:
            event = self.internal_event("recorder.recovered", "recorder", {"run_ids": running})
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.insert_event(event)
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.record_cache.append({"seq": self.connection.execute("SELECT MAX(seq) FROM events").fetchone()[0],
                                      "event": event})
            self.document = self.rebuild(previous=self.document)
        try:
            self.flush()
        except (OSError, ProtocolError) as error:
            self.snapshot_error = str(error)

    def load_records(self):
        rows = self.connection.execute("SELECT seq, body FROM events ORDER BY seq").fetchall()
        result = [{"seq": row["seq"], "event": loads(row["body"])} for row in rows]
        return result

    def records(self):
        result = copy.deepcopy(self.record_cache)
        return result

    def rebuild(self, records=None, reject_id=None, previous=None):
        records = self.record_cache if records is None else records
        document = project_events(self.seed, records, self.preserve_revision, self.read_definition, reject_id, previous)
        document["snapshot"]["snapshot_id"] = "projection-" + digest([self.stream_id,
                                                                     document["snapshot"]["commit_seq"]])[:32]
        return document

    def put_bytes(self, content, mime_type, prefix="artifact"):
        content_hash = hashlib.sha256(content).hexdigest()
        directory = self.directory / "artifacts"
        private_directory(directory)
        path = directory / content_hash
        if path.exists():
            require(not path.is_symlink() and file_hash(path) == content_hash,
                    "Immutable artifact storage was modified", "artifact_conflict")
        else:
            atomic_write(path, content)
        return {"id": f"{prefix}-{content_hash}", "uri": path.relative_to(self.project).as_posix(),
                "mime_type": mime_type, "sha256": content_hash, "size": len(content), "availability": "available",
                "producer_node_id": None, "producer_run_id": None}

    def preserve_revision(self, document):
        revision = document["graph"]["revision_id"]
        content = {key: document[key] for key in ("graph", "variables", "interactive_questions", "variable_operations")}
        artifact = self.put_bytes(dumps(content).encode("utf-8"), "application/json", "revision")
        if artifact["id"] not in {item["id"] for item in document["artifacts"]}:
            document["artifacts"].append(artifact)
        reference = {"kind": "revision", "revision_id": revision, "artifact_id": artifact["id"]}
        if reference not in document["history"]:
            document["history"].append(reference)

    def internal_event(self, kind, session_id, payload):
        source = f"recorder.{self.stream_id}"
        row = self.connection.execute("SELECT COALESCE(MAX(source_seq), 0) AS head FROM events WHERE source_id=?",
                                      (source,)).fetchone()
        return {"event_id": new_id(), "source_id": source, "source_seq": row["head"] + 1,
                "session_id": session_id, "agent_id": None, "type": kind, "timestamp": utc_now(),
                "causal_parents": [], "payload": payload}

    def insert_event(self, event):
        cursor = self.connection.execute(
            "INSERT INTO events(event_id, source_id, source_seq, session_id, body, content_hash) VALUES(?,?,?,?,?,?)",
            (event["event_id"], event["source_id"], event["source_seq"], event["session_id"], dumps(event), digest(event)))
        sequence = cursor.lastrowid
        return sequence

    def receipt(self, event_id, sequence, duplicate=False):
        pending_ids = {gap["event_id"] for gap in self.document["snapshot"]["gaps"]}
        return {"status": "success", "event_id": event_id, "stream_id": self.stream_id, "commit_seq": sequence,
                "duplicate": duplicate, "projected": event_id not in pending_ids,
                "revision_id": self.document["graph"]["revision_id"], "snapshot_error": self.snapshot_error}

    def start_session(self, request_id, record, include_children=False):
        with self.mutex:
            request_id = "session:" + request_id
            request = {"record": record, "include_children": include_children}
            previous = self.connection.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if previous is not None:
                require(previous["content_hash"] == digest(request), "Request ID has different content", "id_conflict")
                result = loads(previous["response"])
            else:
                session_id = new_id()
                token = secrets.token_urlsafe(32)
                now = utc_now()
                session = {"id": session_id, "state": "active" if record else "closed", "created_at": now,
                           "consent": {"record": record, "include_children": include_children if record else False,
                                       "decided_at": now}}
                event = self.internal_event("session.created", session_id, session)
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    sequence = self.insert_event(event)
                    self.connection.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                                            (session_id, digest(token), int(record)))
                    result = {"status": "success", "session_id": session_id, "recording": record,
                              "session_token": token if record else None, "commit_seq": sequence,
                              "stream_id": self.stream_id}
                    self.connection.execute("INSERT INTO requests VALUES (?, ?, ?)",
                                            (request_id, digest(request), dumps(result)))
                    self.connection.execute("COMMIT")
                except BaseException:
                    self.connection.execute("ROLLBACK")
                    raise
                self.record_cache.append({"seq": sequence, "event": event})
                self.document = self.rebuild(previous=self.document)
            return result

    def authorize(self, session_id, token, kind=None):
        row = self.connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        require(row is not None and row["record"] and hmac.compare_digest(row["token_hash"], digest(token)),
                "Recording consent or Session credential is missing", "not_authorized")
        session = next(item for item in self.document["sessions"] if item["id"] == session_id)
        late_types = {"tool.finished", "artifact.recorded", "session.resumed", "session.closed"}
        require(session["state"] == "active" or kind in late_types, "Session is not recording", "session_state")
        require(session["state"] != "closed" or kind in {"tool.finished", "artifact.recorded"},
                "Session is closed", "session_state")

    def check_consent(self, session_id, session_token, event_type, event_id=None):
        with self.mutex:
            previous = self.connection.execute("SELECT session_id FROM events WHERE event_id=?", (event_id,)).fetchone()
            duplicate = previous is not None and previous["session_id"] == session_id
            self.authorize(session_id, session_token, "tool.finished" if duplicate else event_type)
            return {"status": "success", "session_id": session_id, "allowed": True}

    def append(self, event, session_token):
        with self.mutex:
            event = copy.deepcopy(event)
            require_event(event)
            require(not event["source_id"].startswith("recorder."), "Reserved source identity")
            previous = self.connection.execute("SELECT * FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            self.authorize(event["session_id"], session_token, event["type"] if previous is None else "tool.finished")
            if previous is not None:
                require(previous["content_hash"] == digest(event), "Event ID has different content", "id_conflict")
                result = self.receipt(event["event_id"], previous["seq"], True)
            else:
                collision = self.connection.execute("SELECT session_id, source_seq FROM events WHERE source_id=?",
                                                    (event["source_id"],)).fetchall()
                require(all(row["session_id"] == event["session_id"] for row in collision),
                        "Source identity belongs to another Session", "source_conflict")
                require(all(row["source_seq"] != event["source_seq"] for row in collision),
                        "Source sequence already belongs to another event", "source_conflict")
                if event["agent_id"] is not None and event["type"] != "agent.registered":
                    actors = [item for item in self.document["agents"] if item["id"] == event["agent_id"]]
                    if actors:
                        require(actors[0]["session_id"] == event["session_id"], "Agent belongs to another Session")
                all_records = list(self.record_cache)
                next_sequence = max((item["seq"] for item in all_records), default=0) + 1
                all_records.append({"seq": next_sequence, "event": event})
                vertices = {item["event"]["event_id"] for item in all_records}
                causal_edges = [(parent, item["event"]["event_id"]) for item in all_records
                                for parent in item["event"]["causal_parents"] if parent in vertices]
                by_source = {}
                for item in all_records:
                    by_source.setdefault(item["event"]["source_id"], []).append(item["event"])
                for messages in by_source.values():
                    messages.sort(key=lambda message: message["source_seq"])
                    causal_edges.extend((left["event_id"], right["event_id"])
                                        for left, right in zip(messages, messages[1:]))
                require_dag(vertices, causal_edges)
                candidate = self.rebuild(all_records, event["event_id"], self.document)
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    sequence = self.insert_event(event)
                    self.connection.execute("COMMIT")
                except BaseException:
                    self.connection.execute("ROLLBACK")
                    raise
                self.record_cache = all_records
                self.document = candidate
                result = self.receipt(event["event_id"], sequence)
                if event["type"] == "session.closed":
                    try:
                        self.flush()
                    except (OSError, ProtocolError) as error:
                        self.snapshot_error = str(error)
                        result["snapshot_error"] = self.snapshot_error
            return result

    def capture_artifact(self, session_token, event, path, mime_type, producer_node_id=None, producer_run_id=None):
        with self.mutex:
            self.authorize(event["session_id"], session_token, "artifact.recorded")
            require(event["type"] == "artifact.recorded" and event["payload"] == {},
                    "Artifact capture needs an empty artifact.recorded payload")
            capture_id = "capture:" + event["event_id"]
            request_hash = digest({"event": event, "path": path, "mime_type": mime_type,
                                   "producer_node_id": producer_node_id, "producer_run_id": producer_run_id})
            previous = self.connection.execute("SELECT * FROM requests WHERE id=?", (capture_id,)).fetchone()
            if previous is not None:
                require(previous["content_hash"] == request_hash, "Artifact request has different content", "id_conflict")
                artifact = loads(previous["response"])
            else:
                source = project_file(self.project, path)
                require(not source.is_relative_to(self.directory) or source.is_relative_to(self.directory / "imports"),
                        "Recorder internal state cannot be captured as an artifact")
                require(source.stat().st_size <= 64 * 1024 * 1024, "Local artifact capture limit is 64 MiB")
                content = source.read_bytes()
                artifact = self.put_bytes(content, mime_type)
                artifact["producer_node_id"] = producer_node_id
                artifact["producer_run_id"] = producer_run_id
                artifact["id"] = "artifact-" + digest([artifact["sha256"], mime_type, producer_node_id, producer_run_id])
                self.connection.execute("INSERT INTO requests VALUES (?, ?, ?)",
                                        (capture_id, request_hash, dumps(artifact)))
            submitted = {**event, "payload": artifact}
            result = self.append(submitted, session_token)
            result["artifact"] = artifact
            return result

    def attach_segments(self, document):
        result = copy.deepcopy(document)
        for row in self.connection.execute("SELECT * FROM segments ORDER BY first_seq"):
            artifact = loads(row["artifact"])
            result["artifacts"].append(artifact)
            result["history"].append({"kind": "events", "artifact_id": artifact["id"],
                                      "through_seq": row["last_seq"]})
        require_archive(result)
        return result

    def read_archive(self):
        with self.mutex:
            archive = self.attach_segments(self.document)
            return {"status": "success", "archive": archive, "snapshot_error": self.snapshot_error}

    def read_definition(self, document, revision_id):
        fields = ("graph", "variables", "interactive_questions", "variable_operations")
        if revision_id == document["graph"]["revision_id"]:
            definition = {key: copy.deepcopy(document.get(key, [])) for key in fields}
        else:
            references = [item for item in document.get("history", [])
                          if item["kind"] == "revision" and item.get("revision_id") == revision_id]
            require(bool(references), "Unknown workflow revision", "revision_not_found")
            artifact = next(item for item in document["artifacts"] if item["id"] == references[0]["artifact_id"])
            content = read_artifact(self.project, artifact)
            definition = loads(content.decode("utf-8"))
        require_definition_snapshot(definition, document["artifacts"], revision_id)
        return definition

    def get_revision(self, revision_id):
        with self.mutex:
            definition = self.read_definition(self.document, revision_id)
            artifact_id = next((item["artifact_id"] for item in self.document.get("history", [])
                                if item["kind"] == "revision" and item.get("revision_id") == revision_id), None)
            return {"status": "success", "revision_id": revision_id, "artifact_id": artifact_id,
                    "definition": definition, "snapshot": copy.deepcopy(self.document["snapshot"])}

    def get_items(self, collection, offset=0, limit=50, snapshot_id=None):
        with self.mutex:
            require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 200,
                    "Invalid page bounds")
            require(offset == 0 or snapshot_id is not None, "Subsequent pages require snapshot_id")
            segment_head = self.connection.execute("SELECT COALESCE(MAX(last_seq),0) FROM segments").fetchone()[0]
            snapshot = copy.deepcopy(self.document["snapshot"])
            snapshot["snapshot_id"] = "view-" + digest([snapshot["snapshot_id"], segment_head])[:32]
            require(snapshot_id is None or snapshot_id == snapshot["snapshot_id"],
                    "Archive changed; restart pagination", "snapshot_conflict")
            if collection in ("nodes", "edges", "branches"):
                items = self.document["graph"][collection]
            else:
                require(collection in ("runs", "artifacts", "harnesses", "agents", "sessions", "history"),
                        "Unknown archive collection")
                items = self.document.get(collection, [])
                if collection in ("artifacts", "history"):
                    items = list(items)
                    for row in self.connection.execute("SELECT * FROM segments ORDER BY first_seq"):
                        artifact = loads(row["artifact"])
                        items.append(artifact if collection == "artifacts" else {
                            "kind": "events", "artifact_id": artifact["id"], "through_seq": row["last_seq"]})
            next_offset = offset + limit if offset + limit < len(items) else None
            return {"status": "success", "collection": collection, "items": copy.deepcopy(items[offset:offset + limit]),
                    "total": len(items), "next_offset": next_offset, "snapshot": snapshot,
                    "revision_id": self.document["graph"]["revision_id"]}

    def check_references(self, offset=0, limit=50, snapshot_id=None):
        with self.mutex:
            page = self.get_items("artifacts", offset, limit, snapshot_id)
            uses = {}
            selected = {item["id"] for item in page["items"]}

            def add_use(artifact_id, reference):
                if artifact_id in selected:
                    uses.setdefault(artifact_id, []).append(reference)

            for node in self.document["graph"]["nodes"]:
                execution = node.get("execution", {})
                for name, binding in execution.get("inputs", {}).items():
                    if binding["kind"] == "artifact":
                        add_use(binding["artifact_id"], {"kind": "node_input", "node_id": node["id"], "input": name})
                if "script_artifact_id" in execution:
                    add_use(execution["script_artifact_id"], {"kind": "node_script", "node_id": node["id"]})
                for evidence in node["provenance"]["evidence"]:
                    if "artifact_id" in evidence:
                        add_use(evidence["artifact_id"], {"kind": "evidence", "node_id": node["id"]})
            for run in self.document["runs"]:
                for artifact_id in run["artifact_ids"]:
                    add_use(artifact_id, {"kind": "execution_output", "run_id": run["id"]})
            for harness in self.document["harnesses"]:
                for artifact_id in harness.get("artifact_ids", []):
                    add_use(artifact_id, {"kind": "harness", "harness_id": harness["id"]})
            for item in self.document.get("history", []):
                add_use(item["artifact_id"], {"kind": "history", "history_kind": item["kind"]})
            for row in self.connection.execute("SELECT artifact FROM segments"):
                artifact = loads(row["artifact"])
                add_use(artifact["id"], {"kind": "history", "history_kind": "events"})
            reports = []
            for artifact in page["items"]:
                report = inspect_artifact(self.project, artifact)
                report["uses"] = uses.get(artifact["id"], [])
                reports.append(report)
            verified = all(item["state"] == "verified" for item in reports)
            return {"status": "success", "archive_readable": True, "execution_checked": False,
                    "all_references_verified": verified if offset == 0 and page["next_offset"] is None else None,
                    "references": reports, "total": page["total"], "next_offset": page["next_offset"],
                    "snapshot": page["snapshot"]}

    def get_events(self, after_seq=0, limit=50):
        with self.mutex:
            rows = self.connection.execute("SELECT seq, body FROM events WHERE seq>? ORDER BY seq LIMIT ?",
                                           (after_seq, limit)).fetchall()
            return {"status": "success", "stream_id": self.stream_id,
                    "events": [{"commit_seq": row["seq"], "event": loads(row["body"])} for row in rows]}

    def check_snapshot_owner(self):
        path = self.project / "aica.json"
        row = self.connection.execute("SELECT value FROM metadata WHERE key='snapshot_hash'").fetchone()
        candidates = self.connection.execute("SELECT content_hash FROM checkpoints").fetchall()
        hashes = {item["content_hash"] for item in candidates}
        if row:
            hashes.add(row["value"])
        if path.exists():
            require(not path.is_symlink() and file_hash(path) in hashes,
                    "aica.json was edited outside the recorder; stop recording and import changes explicitly",
                    "snapshot_conflict")
        else:
            require(row is None, "aica.json disappeared while the recorder owns it", "snapshot_conflict")

    def flush(self):
        with self.mutex:
            self.check_snapshot_owner()
            last = self.connection.execute("SELECT COALESCE(MAX(last_seq),0) AS head FROM segments").fetchone()["head"]
            records = [item for item in self.record_cache if item["seq"] > last]
            if records:
                content = "".join(dumps({"stream_id": self.stream_id, "commit_seq": item["seq"],
                                         "event": item["event"]}) + "\n" for item in records).encode("utf-8")
                artifact = self.put_bytes(content, "application/x-ndjson", "events")
                self.connection.execute("INSERT INTO segments VALUES (?, ?, ?)",
                                        (records[0]["seq"], records[-1]["seq"], dumps(artifact)))
            archive = self.attach_segments(self.document)
            archive["snapshot"]["snapshot_id"] = new_id()
            content = (dumps(archive, pretty=True) + "\n").encode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
            checkpoint_id = archive["snapshot"]["snapshot_id"]
            sequence = archive["snapshot"]["commit_seq"]
            self.connection.execute("INSERT INTO checkpoints VALUES (?, ?, ?, 'pending')",
                                    (checkpoint_id, sequence, content_hash))
            atomic_write(self.project / "aica.json", content)
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute("UPDATE checkpoints SET state='published' WHERE id=?", (checkpoint_id,))
                self.set_metadata("snapshot_hash", content_hash)
                self.connection.execute("COMMIT")
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.snapshot_error = None
            return {"status": "success", "path": "aica.json", "snapshot": archive["snapshot"],
                    "sha256": content_hash}

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.lock.close()


def error_result(error):
    return {"status": "error", "code": getattr(error, "code", "storage_error"), "message": str(error),
            "details": getattr(error, "details", [])}
