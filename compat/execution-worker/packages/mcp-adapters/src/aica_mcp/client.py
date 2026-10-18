import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, require_shape, utc_now
from aica_schema.recording import require_arguments

from .files import private_directory


class RecorderClient:
    def __init__(self, project, autostart=True):
        self.project = Path(project).resolve(strict=True)
        self.directory = self.project / ".aica"
        self.endpoint = None
        if autostart:
            self.connect()

    def load_endpoint(self):
        path = self.directory / "endpoint.json"
        require(not path.is_symlink(), "Endpoint file cannot be a symlink")
        endpoint = loads(path.read_text(encoding="utf-8"))
        require(endpoint.get("project") == str(self.project), "Coordinator belongs to a different project")
        require(type(endpoint.get("port")) is int and 1024 <= endpoint["port"] <= 65535, "Invalid coordinator port")
        require(isinstance(endpoint.get("token"), str) and len(endpoint["token"]) >= 32, "Invalid coordinator token")
        self.endpoint = endpoint

    def request(self, name, arguments=None):
        if self.endpoint is None:
            self.load_endpoint()
        url = f"http://127.0.0.1:{self.endpoint['port']}/rpc"
        with httpx.Client(timeout=30, trust_env=False) as transport:
            response = transport.post(url, headers={"Authorization": f"Bearer {self.endpoint['token']}"},
                                      json={"method": name, "arguments": arguments or {}})
            response.raise_for_status()
            result = loads(response.text)
        return result

    def connect(self):
        try:
            self.load_endpoint()
            response = self.request("status")
        except (OSError, ProtocolError, httpx.HTTPError):
            private_directory(self.directory)
            log_path = self.directory / "coordinator.log"
            require(not log_path.is_symlink(), "Coordinator log cannot be a symlink")
            with log_path.open("ab") as log:
                os.chmod(log_path, 0o600)
                subprocess.Popen([sys.executable, "-m", "aica_mcp", "coordinator", "--project", str(self.project)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                 start_new_session=os.name != "nt",
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            deadline = time.monotonic() + 15
            connected = False
            while not connected and time.monotonic() < deadline:
                try:
                    self.load_endpoint()
                    response = self.request("status")
                    connected = response.get("status") == "success"
                except (OSError, ProtocolError, httpx.HTTPError):
                    time.sleep(0.05)
            require(connected, f"Recorder could not start; inspect {log_path}", "coordinator_unavailable")
        require(response.get("status") == "success", "Coordinator is not ready", "coordinator_unavailable")

    def send(self, name, arguments):
        try:
            result = self.request(name, arguments)
        except (OSError, httpx.HTTPError):
            self.connect()
            result = self.request(name, arguments)
        return result

    def transport_outbox(self):
        directory = self.directory / "outboxes"
        private_directory(directory)
        path = directory / "transport.sqlite3"
        require(not path.is_symlink(), "Transport outbox cannot be a symlink")
        connection = sqlite3.connect(path, isolation_level=None, timeout=5)
        os.chmod(path, 0o600)
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS pending (
                event_id TEXT PRIMARY KEY, method TEXT NOT NULL, arguments TEXT NOT NULL, content_hash TEXT NOT NULL
            );
        """)
        return connection

    def consent_for(self, arguments):
        event = arguments["event"]
        result = self.send("aica_check_consent", {"session_id": event["session_id"],
                                                  "session_token": arguments["session_token"],
                                                  "event_type": event["type"], "event_id": event["event_id"]})
        return result

    def replay_pending(self):
        connection = self.transport_outbox()
        results = []
        try:
            rows = connection.execute("SELECT * FROM pending ORDER BY rowid").fetchall()
            for event_id, method, serialized, content_hash in rows:
                arguments = loads(serialized)
                consent = self.consent_for(arguments)
                if consent["status"] == "success":
                    result = self.send(method, arguments)
                    if result["status"] == "success":
                        connection.execute("DELETE FROM pending WHERE event_id=?", (event_id,))
                else:
                    result = consent
                results.append(result)
        finally:
            connection.close()
        return results

    def call(self, name, arguments=None):
        arguments = arguments or {}
        require_arguments(name, arguments)
        if name in {"aica_bind_platform", "aica_platform_status", "aica_pause_upload", "aica_resume_upload",
                    "aica_sync_platform"}:
            status = self.send("status", {})
            if status.get("platform_upload_version") != 1:
                return {"status": "error", "code": "coordinator_upgrade_required",
                        "message": "Stop this workspace coordinator with aica stop --project PATH, then reconnect MCP to load platform uploads"}
        if name == "aica_import_archive":
            from .importing import import_archive

            result = import_archive(self.project, **arguments)
            return result
        if name == "aica_record_step":
            from .steps import record_step

            result = record_step(self, **arguments)
            return result
        if name in {"aica_submit_event", "aica_capture_artifact"}:
            consent = self.consent_for(arguments)
            if consent["status"] != "success":
                return consent
            connection = self.transport_outbox()
            event_id = arguments["event"]["event_id"]
            request_hash = digest([name, arguments])
            try:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute("SELECT content_hash FROM pending WHERE event_id=?", (event_id,)).fetchone()
                require(previous is None or previous[0] == request_hash, "Pending event has different content", "id_conflict")
                connection.execute("INSERT OR IGNORE INTO pending VALUES (?, ?, ?, ?)",
                                   (event_id, name, dumps(arguments), request_hash))
                connection.execute("COMMIT")
                result = self.send(name, arguments)
                if result["status"] == "success":
                    connection.execute("DELETE FROM pending WHERE event_id=?", (event_id,))
            finally:
                connection.close()
        else:
            result = self.send(name, arguments)
        return result


class RecordingSource:
    def __init__(self, client, session_id, session_token, source_id=None):
        self.client = client
        self.session_id = session_id
        self.session_token = session_token
        directory = client.directory / "outboxes"
        private_directory(directory)
        source_id = source_id or new_id()
        require_shape(source_id, "id")
        path = directory / f"{source_id}.sqlite3"
        require(not path.is_symlink(), "Outbox cannot be a symlink")
        self.connection = sqlite3.connect(path, isolation_level=None)
        os.chmod(path, 0o600)
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
        """)
        previous = self.connection.execute("SELECT value FROM state WHERE key='session'").fetchone()
        require(previous is None or previous[0] == session_id, "Source belongs to another Session")
        self.connection.execute("INSERT OR IGNORE INTO state VALUES ('session', ?)", (session_id,))
        self.connection.execute("INSERT OR IGNORE INTO state VALUES ('sequence', '0')")
        self.source_id = source_id

    def emit(self, kind, payload, agent_id=None, expected_revision=None, causal_parents=None, capture=None, branch_id=None):
        consent = self.client.call("aica_check_consent", {"session_id": self.session_id,
                                                         "session_token": self.session_token, "event_type": kind})
        require(consent["status"] == "success", consent.get("message", "Recording consent is missing"),
                consent.get("code", "not_authorized"))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            sequence = int(self.connection.execute("SELECT value FROM state WHERE key='sequence'").fetchone()[0]) + 1
            event = {"event_id": new_id(), "source_id": self.source_id, "source_seq": sequence,
                     "session_id": self.session_id, "agent_id": agent_id, "type": kind, "timestamp": utc_now(),
                     "causal_parents": causal_parents or [], "payload": payload}
            if expected_revision is not None:
                event["expected_revision"] = expected_revision
            if branch_id is not None:
                event["branch_id"] = branch_id
            self.connection.execute("INSERT INTO pending VALUES (?, ?)",
                                    (sequence, dumps({"event": event, "capture": capture})))
            self.connection.execute("UPDATE state SET value=? WHERE key='sequence'", (str(sequence),))
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        results = self.replay()
        result = next((item for item in results if item.get("event_id") == event["event_id"]), results[-1])
        return result

    def replay(self):
        results = []
        rows = self.connection.execute("SELECT seq, body FROM pending ORDER BY seq").fetchall()
        for row in rows:
            queued = loads(row[1])
            arguments = {"session_token": self.session_token, "event": queued["event"]}
            method = "aica_submit_event"
            if queued["capture"] is not None:
                arguments.update(queued["capture"])
                method = "aica_capture_artifact"
            result = self.client.call(method, arguments)
            results.append(result)
            if result["status"] != "success":
                break
            self.connection.execute("DELETE FROM pending WHERE seq=?", (row[0],))
        return results

    def close(self):
        self.connection.close()
