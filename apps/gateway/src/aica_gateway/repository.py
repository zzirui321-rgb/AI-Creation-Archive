import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from aica_mcp.files import file_hash, private_directory
from aica_mcp.transfer import ARTIFACT_LIMIT, verify_bundle
from aica_schema.protocol import (
    ProtocolError,
    dumps,
    embedded_objects,
    loads,
    new_id,
    require,
    require_archive,
    utc_now,
)
from aica_schema.transfer import require_transfer


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Repository:
    def __init__(self, directory):
        self.directory = Path(directory)
        for name in ("snapshots", "staging", "uploads"):
            private_directory(self.directory / name)
        self.database = self.directory / "platform.sqlite3"
        require(not self.database.is_symlink(), "Database cannot be a symlink")
        with self.connection() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, subject TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, head TEXT
                );
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    created_at TEXT NOT NULL, bundle_hash TEXT NOT NULL, archive_hash TEXT NOT NULL,
                    archive BLOB NOT NULL, manifest TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS project_imports ON imports(project_id, created_at, id);
                CREATE TABLE IF NOT EXISTS receipts (
                    subject TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
                    request_key TEXT NOT NULL, bundle_hash TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(subject, project_id, request_key)
                );
            """)
        self.database.chmod(0o600)
        self.import_publisher = None
        self.media_snapshots = OrderedDict()
        self.media_lock = threading.Lock()

    def media_snapshot(self, subject, project_id, import_id):
        with self.connection() as db:
            self.owned_project(db, subject, project_id)
        key = (project_id, import_id)
        with self.media_lock:
            if key in self.media_snapshots:
                self.media_snapshots.move_to_end(key)
                return self.media_snapshots[key]
            snapshot = self.snapshot(subject, project_id, import_id)
            for content_hash, content in embedded_objects(snapshot["archive"]).items():
                path = self.directory / "snapshots" / import_id / content_hash
                if not path.exists():
                    private_directory(path.parent)
                    descriptor, name = tempfile.mkstemp(dir=path.parent)
                    try:
                        with os.fdopen(descriptor, "wb") as stream:
                            stream.write(content)
                        os.replace(name, path)
                    finally:
                        Path(name).unlink(missing_ok=True)
            lightweight = {**snapshot, "archive": {"artifacts": snapshot["archive"]["artifacts"],
                "graph": {"edges": snapshot["archive"].get("graph", {}).get("edges", [])}}}
            self.media_snapshots[key] = lightweight
            while len(self.media_snapshots) > 16:
                self.media_snapshots.popitem(last=False)
            return lightweight

    @contextmanager
    def connection(self, write=False):
        database = sqlite3.connect(self.database, timeout=30)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                database.execute("BEGIN IMMEDIATE")
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def owned_project(self, database, subject, project_id):
        project = database.execute("SELECT * FROM projects WHERE id=? AND subject=?",
                                   (project_id, subject)).fetchone()
        require(project is not None, "Project not found", "not_found")
        return dict(project)

    def authorize(self, subject, project_id):
        with self.connection() as database:
            return self.owned_project(database, subject, project_id)

    def create_project(self, subject, title):
        project = {"id": new_id(), "title": title, "created_at": utc_now(), "head": None}
        with self.connection(write=True) as database:
            database.execute("INSERT INTO projects VALUES (?,?,?,?,NULL)",
                             (project["id"], subject, title, project["created_at"]))
        return project

    def projects(self, subject, offset, limit):
        with self.connection() as database:
            rows = database.execute("SELECT id,title,created_at,head FROM projects WHERE subject=? "
                                    "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (subject, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def prior_receipt(self, database, subject, project_id, request_key, bundle_hash):
        row = database.execute("SELECT bundle_hash,body FROM receipts WHERE subject=? AND project_id=? "
                               "AND request_key=?", (subject, project_id, request_key)).fetchone()
        if row is None:
            return None
        require(row["bundle_hash"] == bundle_hash, "Idempotency key has different content", "id_conflict")
        return loads(row["body"])

    def publish_import(self, database, subject, project_id, import_id):
        if self.import_publisher:
            self.import_publisher(database, subject, project_id, import_id)

    def ingest(self, subject, project_id, path, expected_hash, request_key):
        self.authorize(subject, project_id)
        report = verify_bundle(path)
        require(report["bundle_sha256"] == expected_hash and request_key == "sha256:" + expected_hash,
                "Bundle does not match upload headers", "transfer_mismatch")
        with self.connection() as database:
            prior = self.prior_receipt(database, subject, project_id, request_key, expected_hash)
        if prior is not None:
            with self.connection(write=True) as database:
                self.publish_import(database, subject, project_id, prior["import_id"])
            return prior, False

        import_id = new_id()
        final = self.directory / "snapshots" / import_id
        stage = Path(tempfile.mkdtemp(dir=self.directory / "staging"))
        moved = False
        published = False
        try:
            with zipfile.ZipFile(path) as bundle:
                archive_bytes = bundle.read("aica.json")
                require(hashlib.sha256(archive_bytes).hexdigest() == report["archive_sha256"],
                        "Archive changed during ingestion", "transfer_mismatch")
                for item in {item["path"]: item for item in report["manifest"]["artifacts"]}.values():
                    target = stage / item["sha256"]
                    with bundle.open(item["path"]) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    target.chmod(0o600)
                    require(target.stat().st_size == item["size"] and file_hash(target) == item["sha256"],
                            "Object changed during ingestion", "transfer_mismatch")
            sync_directory(stage)
            receipt = {"status": "success", "format": "aica-transfer/0.1", "import_id": import_id,
                       "project_id": project_id, "bundle_sha256": expected_hash,
                       "archive_sha256": report["archive_sha256"]}
            require_transfer(receipt, "receipt")
            with self.connection(write=True) as database:
                self.owned_project(database, subject, project_id)
                prior = self.prior_receipt(database, subject, project_id, request_key, expected_hash)
                if prior is not None:
                    self.publish_import(database, subject, project_id, prior["import_id"])
                    return prior, False
                # Readers discover snapshots only through the committed database row.
                os.replace(stage, final)
                moved = True
                sync_directory(final.parent)
                database.execute("INSERT INTO imports VALUES (?,?,?,?,?,?,?)",
                                 (import_id, project_id, utc_now(), expected_hash, report["archive_sha256"],
                                  sqlite3.Binary(archive_bytes), dumps(report["manifest"])))
                database.execute("UPDATE projects SET head=? WHERE id=?", (import_id, project_id))
                database.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                                 (subject, project_id, request_key, expected_hash, dumps(receipt)))
                self.publish_import(database, subject, project_id, import_id)
            published = True
            return receipt, True
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            if moved and not published:
                shutil.rmtree(final)

    def ingest_json(self, subject, project_id, content, expected_hash=None, request_key=None):
        try:
            archive = loads(content.decode("utf-8"))
        except (UnicodeError, ProtocolError):
            raise ProtocolError("Expected a valid UTF-8 AiCA archive JSON") from None
        require_archive(archive)
        objects = embedded_objects(archive, complete=expected_hash is not None)
        archive_hash = hashlib.sha256(content).hexdigest()
        if expected_hash is not None:
            require(expected_hash == archive_hash and request_key == "sha256:" + archive_hash,
                    "JSON hash differs from upload headers", "id_conflict")
        key = request_key or "archive-json:" + archive_hash
        with self.connection(write=True) as database:
            self.owned_project(database, subject, project_id)
            prior = self.prior_receipt(database, subject, project_id, key, archive_hash)
            if prior is not None:
                self.publish_import(database, subject, project_id, prior["import_id"])
                return prior, False
            import_id = new_id()
            manifest = {"format": "aica-archive-json/0.1",
                        "artifacts": [{"artifact_id": item["id"], "sha256": item["sha256"],
                                       "size": len(objects[item["sha256"]]), "embedded": True}
                                      for item in archive["artifacts"] if item["sha256"] in objects],
                        "missing_artifact_ids": [item["id"] for item in archive["artifacts"]
                                                 if item["sha256"] not in objects]}
            receipt = {"status": "success", "format": manifest["format"], "project_id": project_id,
                       "import_id": import_id, "archive_sha256": archive_hash,
                       "missing_artifact_ids": manifest["missing_artifact_ids"]}
            if expected_hash is not None:
                receipt = {"status": "success", "format": "aica-json/0.1", "project_id": project_id,
                           "import_id": import_id, "archive_sha256": archive_hash, "bundle_sha256": archive_hash}
            database.execute("INSERT INTO imports VALUES (?,?,?,?,?,?,?)",
                             (import_id, project_id, utc_now(), archive_hash, archive_hash,
                              sqlite3.Binary(content), dumps(manifest)))
            database.execute("UPDATE projects SET head=? WHERE id=?", (import_id, project_id))
            database.execute("INSERT INTO receipts VALUES (?,?,?,?,?)",
                             (subject, project_id, key, archive_hash, dumps(receipt)))
            self.publish_import(database, subject, project_id, import_id)
        return receipt, True

    def imports(self, subject, project_id, offset, limit):
        with self.connection() as database:
            self.owned_project(database, subject, project_id)
            rows = database.execute("SELECT id,created_at,bundle_hash,archive_hash FROM imports WHERE project_id=? "
                                    "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (project_id, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, subject, project_id, import_id=None):
        with self.connection() as database:
            project = self.owned_project(database, subject, project_id)
            row = database.execute("SELECT * FROM imports WHERE id=? AND project_id=?",
                                   (import_id or project["head"], project_id)).fetchone()
        require(row is not None, "Archive not found", "not_found")
        require(hashlib.sha256(row["archive"]).hexdigest() == row["archive_hash"],
                "Stored archive is corrupt", "storage_corrupt")
        archive = loads(row["archive"].decode("utf-8"))
        require_archive(archive)
        return {"import_id": row["id"], "archive_sha256": row["archive_hash"], "archive": archive,
                "manifest": loads(row["manifest"])}

    def archive_bytes(self, subject, project_id, import_id):
        with self.connection() as database:
            self.owned_project(database, subject, project_id)
            row = database.execute("SELECT archive,archive_hash FROM imports WHERE id=? AND project_id=?",
                                   (import_id, project_id)).fetchone()
        require(row is not None, "Archive not found", "not_found")
        content = bytes(row["archive"])
        require(hashlib.sha256(content).hexdigest() == row["archive_hash"], "Stored archive is corrupt", "storage_corrupt")
        return content

    def artifact(self, subject, project_id, artifact_id, import_id=None):
        snapshot = self.snapshot(subject, project_id, import_id)
        return self.artifact_in_snapshot(snapshot, artifact_id)

    def artifact_in_snapshot(self, snapshot, artifact_id):
        artifact = next((item for item in snapshot["archive"]["artifacts"] if item["id"] == artifact_id), None)
        require(artifact is not None, "Artifact not found", "not_found")
        item = next((item for item in snapshot["manifest"]["artifacts"] if item["artifact_id"] == artifact_id), None)
        require(item is not None, "Artifact content was not uploaded; provide an AiCA transfer ZIP", "artifact_unavailable")
        require(item["sha256"] == artifact["sha256"], "Stored artifact mapping is corrupt", "storage_corrupt")
        path = self.directory / "snapshots" / snapshot["import_id"] / artifact["sha256"]
        if item.get("embedded") and not path.exists():
            content = embedded_objects(snapshot["archive"])[artifact["sha256"]]
            private_directory(path.parent)
            descriptor, name = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, path)
            finally:
                Path(name).unlink(missing_ok=True)
        require(not path.is_symlink() and path.is_file(), "Artifact is missing", "artifact_unavailable")
        require(path.stat().st_size == item["size"] <= ARTIFACT_LIMIT and file_hash(path) == artifact["sha256"],
                "Stored artifact is corrupt", "storage_corrupt")
        return artifact, path
