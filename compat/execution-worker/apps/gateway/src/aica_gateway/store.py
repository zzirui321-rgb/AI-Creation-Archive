import copy
import hashlib
import sqlite3
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

from aica_mcp.files import atomic_write, file_hash, private_directory, read_artifact
from aica_mcp.transfer import ARTIFACT_LIMIT, read_portable_json, verify_bundle
from aica_schema.protocol import EMBEDDED_EXTENSION, digest, dumps, loads, new_id, require, require_archive, utc_now
from aica_schema.transfer import require_transfer


class PlatformStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        private_directory(self.directory)
        private_directory(self.directory / "objects")
        private_directory(self.directory / "work")
        private_directory(self.directory / "uploads")
        self.lock = threading.RLock()
        self.database = self.directory / "platform.sqlite3"
        require(not self.database.is_symlink(), "Platform database cannot be a symlink")
        with self.transaction() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, head TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archives (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), parent_id TEXT,
                    body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    project_id TEXT NOT NULL REFERENCES projects(id), id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(project_id,id)
                );
                CREATE TABLE IF NOT EXISTS actions (
                    project_id TEXT NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL, response TEXT NOT NULL, PRIMARY KEY(project_id,kind,request_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), state TEXT NOT NULL,
                    payload TEXT NOT NULL, result_archive_id TEXT, result TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_project_job ON jobs(project_id)
                    WHERE state IN ('queued','running');
                CREATE TABLE IF NOT EXISTS cache (
                    project_id TEXT NOT NULL REFERENCES projects(id), key TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(project_id,key)
                );
            """)
        self.database.chmod(0o600)

    @contextmanager
    def transaction(self):
        with self.lock:
            database = sqlite3.connect(self.database, timeout=10)
            database.row_factory = sqlite3.Row
            database.execute("PRAGMA foreign_keys=ON")
            database.execute("PRAGMA synchronous=FULL")
            database.execute("BEGIN IMMEDIATE")
            try:
                yield database
                database.commit()
            except BaseException:
                database.rollback()
                raise
            finally:
                database.close()

    def project(self, database, project_id, owner):
        row = database.execute("SELECT * FROM projects WHERE id=? AND owner=?", (project_id, owner)).fetchone()
        require(row is not None, "Project not found", "not_found")
        return dict(row)

    def create_project(self, owner, title):
        project = {"id": new_id(), "owner": owner, "title": title, "head": None, "created_at": utc_now()}
        with self.transaction() as database:
            database.execute("INSERT INTO projects VALUES (:id,:owner,:title,:head,:created_at)", project)
        return {"status": "success", "project": project}

    def list_projects(self, owner, offset=0, limit=50):
        with self.transaction() as database:
            rows = database.execute("SELECT * FROM projects WHERE owner=? ORDER BY created_at,id LIMIT ? OFFSET ?",
                                    (owner, limit, offset)).fetchall()
        return {"status": "success", "projects": [dict(row) for row in rows]}

    def read_archive(self, owner, project_id, archive_id=None):
        with self.transaction() as database:
            project = self.project(database, project_id, owner)
            archive_id = archive_id or project["head"]
            row = database.execute("SELECT * FROM archives WHERE project_id=? AND id=?",
                                   (project_id, archive_id)).fetchone()
            require(row is not None, "Archive not found", "not_found")
        return {"status": "success", "archive_id": row["id"], "parent_archive_id": row["parent_id"],
                "archive": loads(row["body"])}

    def list_archives(self, owner, project_id, offset=0, limit=50):
        with self.transaction() as database:
            self.project(database, project_id, owner)
            rows = database.execute("SELECT id,parent_id,created_at FROM archives WHERE project_id=? "
                                    "ORDER BY created_at,id LIMIT ? OFFSET ?", (project_id, limit, offset)).fetchall()
        return {"status": "success", "archives": [dict(row) for row in rows]}

    def check_idle(self, database, project_id):
        active = database.execute("SELECT id FROM jobs WHERE project_id=? AND state IN ('queued','running')",
                                  (project_id,)).fetchone()
        require(active is None, "Project has an active execution", "project_busy")

    def previous_action(self, database, project_id, kind, request_id, request_hash):
        row = database.execute("SELECT * FROM actions WHERE project_id=? AND kind=? AND request_id=?",
                               (project_id, kind, request_id)).fetchone()
        if row is not None:
            require(row["request_hash"] == request_hash, "Request ID has different content", "id_conflict")
            result = loads(row["response"])
            return result
        return None

    def save_action(self, database, project_id, kind, request_id, request_hash, result):
        database.execute("INSERT INTO actions VALUES (?,?,?,?,?)",
                         (project_id, kind, request_id, request_hash, dumps(result)))

    def blob_path(self, project_id, sha256):
        require(len(sha256) == 64 and all(char in "0123456789abcdef" for char in sha256), "Invalid content hash")
        require(len(project_id) == 36 and all(char in "0123456789abcdef-" for char in project_id), "Invalid project ID")
        path = self.directory / "objects" / project_id / sha256
        require(path.resolve().is_relative_to(self.directory / "objects"), "Object path escapes storage")
        return path

    def put_blob(self, project_id, content):
        require(len(content) <= ARTIFACT_LIMIT, "Artifact exceeds 64 MiB", "transfer_limit")
        sha256 = hashlib.sha256(content).hexdigest()
        path = self.blob_path(project_id, sha256)
        private_directory(path.parent)
        if path.exists():
            require(not path.is_symlink() and file_hash(path) == sha256, "Stored object is corrupt", "artifact_conflict")
        else:
            atomic_write(path, content)
        return sha256

    def read_blob(self, project_id, sha256):
        path = self.blob_path(project_id, sha256)
        require(path.is_file() and not path.is_symlink(), "Artifact bytes unavailable", "artifact_unavailable")
        with path.open("rb") as stream:
            content = stream.read(ARTIFACT_LIMIT + 1)
        require(len(content) <= ARTIFACT_LIMIT and hashlib.sha256(content).hexdigest() == sha256,
                "Stored object is corrupt", "artifact_conflict")
        return content

    def index_artifacts(self, database, project_id, artifacts):
        for artifact in artifacts:
            previous = database.execute("SELECT body FROM artifacts WHERE project_id=? AND id=?",
                                        (project_id, artifact["id"])).fetchone()
            if previous is not None:
                old = loads(previous["body"])
                fields = ("sha256", "mime_type", "producer_run_id", "producer_node_id")
                require(all(old[key] == artifact[key] for key in fields), "Artifact ID collision", "artifact_conflict")
            database.execute("INSERT OR REPLACE INTO artifacts VALUES (?,?,?)",
                             (project_id, artifact["id"], dumps(artifact)))

    def publish(self, database, project_id, archive, parent_id, raw_text=None):
        require_archive(archive)
        archive_id = new_id()
        self.index_artifacts(database, project_id, archive["artifacts"])
        database.execute("INSERT INTO archives VALUES (?,?,?,?,?)",
                         (archive_id, project_id, parent_id, raw_text if raw_text is not None else dumps(archive), utc_now()))
        database.execute("UPDATE projects SET head=? WHERE id=?", (archive_id, project_id))
        return archive_id

    def import_bundle(self, owner, project_id, path, expected_hash, request_id):
        report = verify_bundle(path)
        require(report["bundle_sha256"] == expected_hash and request_id == "sha256:" + expected_hash,
                "Bundle hash or idempotency key differs from request bytes", "transfer_mismatch")
        with self.transaction() as database:
            project = self.project(database, project_id, owner)
            previous = self.previous_action(database, project_id, "import", request_id, expected_hash)
            if previous is not None:
                return previous
            self.check_idle(database, project_id)
            with zipfile.ZipFile(path) as bundle:
                raw_archive = bundle.read("aica.json")
                require(hashlib.sha256(raw_archive).hexdigest() == report["archive_sha256"],
                        "Bundle changed during import", "transfer_mismatch")
                archive = loads(raw_archive.decode("utf-8"))
                for name in {item["path"] for item in report["manifest"]["artifacts"]}:
                    sha256 = self.put_blob(project_id, bundle.read(name))
                    require(name == "objects/" + sha256, "Bundle changed during import", "transfer_mismatch")
            archive_id = self.publish(database, project_id, archive, project["head"], raw_archive.decode("utf-8"))
            result = {"status": "success", "format": "aica-transfer/0.1", "import_id": archive_id,
                      "project_id": project_id, "bundle_sha256": report["bundle_sha256"],
                      "archive_sha256": report["archive_sha256"]}
            self.save_action(database, project_id, "import", request_id, expected_hash, result)
        return result

    def import_json(self, owner, project_id, path, expected_hash, request_id):
        archive, objects, raw = read_portable_json(path)
        actual_hash = hashlib.sha256(raw).hexdigest()
        require(actual_hash == expected_hash and request_id == "sha256:" + expected_hash,
                "JSON hash or idempotency key differs from request bytes", "transfer_mismatch")
        with self.transaction() as database:
            project = self.project(database, project_id, owner)
            previous = self.previous_action(database, project_id, "import-json", request_id, expected_hash)
            if previous is not None:
                return previous
            self.check_idle(database, project_id)
            for content in objects.values():
                self.put_blob(project_id, content)
            self.put_blob(project_id, raw)
            archive.setdefault("extensions", {}).pop(EMBEDDED_EXTENSION, None)
            archive_id = self.publish(database, project_id, archive, project["head"])
            result = {"status": "success", "format": "aica-json/0.1", "import_id": archive_id,
                      "project_id": project_id, "bundle_sha256": actual_hash, "archive_sha256": actual_hash}
            require_transfer(result, "receipt")
            self.save_action(database, project_id, "import-json", request_id, expected_hash, result)
        return result

    def artifact(self, owner, project_id, artifact_id):
        with self.transaction() as database:
            self.project(database, project_id, owner)
            row = database.execute("SELECT body FROM artifacts WHERE project_id=? AND id=?",
                                   (project_id, artifact_id)).fetchone()
            require(row is not None, "Artifact not found", "not_found")
        record = loads(row["body"])
        self.read_blob(project_id, record["sha256"])
        return record, self.blob_path(project_id, record["sha256"])

    def save_fork(self, owner, project_id, payload, transform):
        request_hash = digest(payload)
        with self.transaction() as database:
            project = self.project(database, project_id, owner)
            previous = self.previous_action(database, project_id, "fork", payload["request_id"], request_hash)
            if previous is not None:
                return previous
            self.check_idle(database, project_id)
            require(project["head"] == payload["archive_id"], "Archive head changed", "revision_conflict")
            row = database.execute("SELECT body FROM archives WHERE id=? AND project_id=?",
                                   (payload["archive_id"], project_id)).fetchone()
            require(row is not None, "Archive not found", "not_found")
            archive, dirty = transform(loads(row["body"]))
            archive_id = self.publish(database, project_id, archive, project["head"])
            result = {"status": "success", "archive_id": archive_id, "revision_id": archive["graph"]["revision_id"],
                      "dirty_node_ids": dirty}
            self.save_action(database, project_id, "fork", payload["request_id"], request_hash, result)
        return result

    def create_job(self, owner, project_id, payload):
        with self.transaction() as database:
            project = self.project(database, project_id, owner)
            previous = self.previous_action(database, project_id, "execution", payload["request_id"], digest(payload))
            if previous is not None:
                return previous, False
            self.check_idle(database, project_id)
            require(project["head"] == payload["archive_id"], "Archive head changed", "revision_conflict")
            job_id = new_id()
            now = utc_now()
            database.execute("INSERT INTO jobs VALUES (?,?, 'queued',?,?,NULL,0,?,?)",
                             (job_id, project_id, dumps(payload), payload["archive_id"], now, now))
            result = {"status": "success", "job_id": job_id, "state": "queued"}
            self.save_action(database, project_id, "execution", payload["request_id"], digest(payload), result)
        return result, True

    def read_job(self, owner, project_id, job_id):
        with self.transaction() as database:
            self.project(database, project_id, owner)
            row = database.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            require(row is not None, "Execution not found", "not_found")
        result = dict(row)
        result["payload"] = loads(result["payload"])
        result["result"] = loads(result["result"]) if result["result"] else None
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def list_jobs(self, owner, project_id, offset=0, limit=50):
        with self.transaction() as database:
            self.project(database, project_id, owner)
            rows = database.execute("SELECT id FROM jobs WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                                    (project_id, limit, offset)).fetchall()
        jobs = [self.read_job(owner, project_id, row["id"]) for row in rows]
        return {"status": "success", "executions": jobs}

    def set_job_state(self, job_id, state, result=None):
        with self.transaction() as database:
            database.execute("UPDATE jobs SET state=?,result=?,updated_at=? WHERE id=?",
                             (state, dumps(result) if result else None, utc_now(), job_id))

    def request_cancel(self, owner, project_id, job_id):
        self.read_job(owner, project_id, job_id)
        with self.transaction() as database:
            database.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND state IN ('queued','running')", (job_id,))
            row = database.execute("SELECT state,cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        return {"status": "success", "job_id": job_id, "state": row["state"],
                "cancel_requested": bool(row["cancel_requested"])}

    def cancelled(self, job_id):
        with self.transaction() as database:
            row = database.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row["cancel_requested"])

    def prepare_workspace(self, project_id, job_id, archive):
        path = self.directory / "work" / job_id
        private_directory(path)
        private_directory(path / ".aica")
        private_directory(path / ".aica" / "artifacts")
        document = copy.deepcopy(archive)
        for key, value in (("history", []), ("variable_operations", []), ("extensions", {})):
            document.setdefault(key, value)
        for artifact in document["artifacts"]:
            content = self.read_blob(project_id, artifact["sha256"])
            relative = ".aica/artifacts/" + artifact["sha256"]
            artifact["locations"] = list(dict.fromkeys([*artifact.get("locations", []), relative]))
            atomic_write(path / relative, content)
        atomic_write(path / "aica.json", dumps(document).encode("utf-8"))
        return path

    def checkpoint(self, project_id, job_id, workspace, archive):
        for artifact in archive["artifacts"]:
            self.put_blob(project_id, read_artifact(workspace, artifact, ARTIFACT_LIMIT))
        with self.transaction() as database:
            job = database.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            project = database.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            require(project["head"] == job["result_archive_id"], "Project changed during execution", "revision_conflict")
            archive_id = self.publish(database, project_id, archive, project["head"])
            database.execute("UPDATE jobs SET result_archive_id=?,updated_at=? WHERE id=?", (archive_id, utc_now(), job_id))
        return archive_id

    def cache_get(self, project_id, key):
        with self.transaction() as database:
            row = database.execute("SELECT body FROM cache WHERE project_id=? AND key=?", (project_id, key)).fetchone()
        result = loads(row["body"]) if row else None
        return result

    def cache_put(self, project_id, key, value):
        with self.transaction() as database:
            database.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (project_id, key, dumps(value)))
