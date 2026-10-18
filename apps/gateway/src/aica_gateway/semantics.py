import hashlib

from aica_runtime.evidence import prepare_evidence
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, utc_now


class SemanticService:
    def __init__(self, repository, provider):
        self.repository = repository
        self.provider = provider
        with repository.connection(write=True) as database:
            database.execute("""CREATE TABLE IF NOT EXISTS semantic_analyses (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                import_id TEXT NOT NULL REFERENCES imports(id), subject TEXT NOT NULL, request_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, state TEXT NOT NULL, engine TEXT NOT NULL, input TEXT NOT NULL,
                result TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(subject,project_id,request_id)
            )""")
            database.execute("UPDATE semantic_analyses SET state='unknown',error_code='service_restarted',updated_at=? "
                             "WHERE state='running'", (utc_now(),))

    def prepare(self, subject, project_id, import_id, node_ids=None):
        snapshot = self.repository.snapshot(subject, project_id, import_id)

        def read_history(artifact_id, limit):
            artifact, path = self.repository.artifact(subject, project_id, artifact_id, import_id)
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
            require(len(content) <= limit, "History exceeds analysis budget", "analysis_limit")
            require(hashlib.sha256(content).hexdigest() == artifact["sha256"],
                    "History bytes changed", "storage_corrupt")
            return content

        return prepare_evidence(snapshot["archive"], import_id, snapshot["archive_sha256"], read_history, node_ids)

    def serialize(self, row):
        return {key: loads(row[key]) if key in {"engine", "result"} and row[key] is not None else row[key]
                for key in ("id", "import_id", "state", "engine", "result", "error_code", "created_at", "updated_at")}

    def previous(self, database, subject, project_id, request_id, request_hash):
        row = database.execute("SELECT * FROM semantic_analyses WHERE subject=? AND project_id=? AND request_id=?",
                               (subject, project_id, request_id)).fetchone()
        if row is None:
            return None
        require(row["request_hash"] == request_hash, "Analysis request ID has different content", "id_conflict")
        return self.serialize(row)

    def analyze(self, subject, project_id, payload):
        require(payload["allow_external_processing"] is True,
                "Analysis requires permission to send the reviewed facts to DeepSeek", "not_authorized")
        fingerprint = digest(payload)
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            previous = self.previous(database, subject, project_id, payload["request_id"], fingerprint)
        if previous is not None:
            return previous, 202 if previous["state"] == "running" else 200
        prepared = self.prepare(subject, project_id, payload["import_id"], payload.get("node_ids"))
        require(prepared["input_sha256"] == payload["input_sha256"],
                "Reviewed analysis input changed; fetch it again", "analysis_input_conflict")
        engine = self.provider.describe()
        require(engine["configured"], "DeepSeek provider is unavailable", "provider_unavailable")
        identifier, now = new_id(), utc_now()
        with self.repository.connection(write=True) as database:
            self.repository.owned_project(database, subject, project_id)
            previous = self.previous(database, subject, project_id, payload["request_id"], fingerprint)
            if previous is not None:
                return previous, 202 if previous["state"] == "running" else 200
            active = database.execute("SELECT id FROM semantic_analyses WHERE project_id=? AND state='running'",
                                      (project_id,)).fetchone()
            require(active is None, "Project already has a running semantic analysis", "project_busy")
            database.execute("INSERT INTO semantic_analyses VALUES (?,?,?,?,?,?,'running',?,?,NULL,NULL,?,?)",
                             (identifier, project_id, payload["import_id"], subject, payload["request_id"], fingerprint,
                              dumps(engine), dumps(prepared), now, now))
        try:
            report = self.provider.analyze(prepared)
        except ProtocolError as error:
            self.finish(identifier, "unknown" if error.code == "provider_connection" else "failed", error_code=error.code)
            raise
        except Exception as error:
            self.finish(identifier, "unknown", error_code="analysis_interrupted")
            raise ProtocolError("Semantic analysis interrupted; inspect its saved status", "analysis_interrupted") from error
        self.finish(identifier, "succeeded", report)
        return self.detail(subject, project_id, identifier), 201

    def finish(self, identifier, state, result=None, error_code=None):
        with self.repository.connection(write=True) as database:
            database.execute("UPDATE semantic_analyses SET state=?,result=?,error_code=?,updated_at=? WHERE id=?",
                             (state, dumps(result) if result is not None else None, error_code, utc_now(), identifier))

    def detail(self, subject, project_id, identifier):
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            row = database.execute("SELECT * FROM semantic_analyses WHERE id=? AND project_id=? AND subject=?",
                                   (identifier, project_id, subject)).fetchone()
        require(row is not None, "Semantic analysis not found", "not_found")
        return self.serialize(row)

    def list(self, subject, project_id, offset, limit):
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            rows = database.execute("SELECT id,import_id,state,engine,error_code,created_at,updated_at "
                                    "FROM semantic_analyses WHERE subject=? AND project_id=? "
                                    "ORDER BY created_at,id LIMIT ? OFFSET ?", (subject, project_id, limit, offset)).fetchall()
        return [{**dict(row), "engine": loads(row["engine"])} for row in rows]
