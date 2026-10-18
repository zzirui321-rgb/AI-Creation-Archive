import logging
from concurrent.futures import ThreadPoolExecutor

from aica_runtime.evidence import HISTORY_LIMIT, redact
from aica_runtime.graph.assembly import assemble_graph
from aica_runtime.graph.context import prepare_graph
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.materials import TEXT_MIMES, read_material
from aica_runtime.graph.provider import GraphResponseError
from aica_runtime.graph.validation import resolve_evidence
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, utc_now


class GraphService:
    def __init__(self, repository, provider, publisher=None):
        self.repository, self.provider = repository, provider
        self.publisher = publisher
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aica-graph")
        with repository.connection(write=True) as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS semantic_graph_branches (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
                    name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS semantic_graph_parses (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    import_id TEXT NOT NULL REFERENCES imports(id), subject TEXT NOT NULL,
                    request_id TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL,
                    engine TEXT NOT NULL, input TEXT NOT NULL, graph_id TEXT NOT NULL UNIQUE,
                    revision_id TEXT NOT NULL UNIQUE, branch_id TEXT NOT NULL REFERENCES semantic_graph_branches(id),
                    graph TEXT, usage TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(subject,project_id,request_id)
                );
            """)
            database.execute("UPDATE semantic_graph_parses SET state='unknown',error_code='service_restarted',updated_at=? "
                             "WHERE state IN ('queued','running')", (utc_now(),))
            columns = {row["name"] for row in database.execute("PRAGMA table_info(semantic_graph_parses)")}
            if "error_detail" not in columns:
                database.execute("ALTER TABLE semantic_graph_parses ADD COLUMN error_detail TEXT")
        if publisher:
            with repository.connection() as database:
                pending = database.execute(
                    "SELECT id FROM semantic_graph_parses WHERE state='succeeded' AND subject LIKE 'user:%' "
                    "AND id NOT IN (SELECT parse_id FROM parse_publications) ORDER BY created_at,id").fetchall()
            for row in pending:
                self.publish(row["id"])

    def publish(self, identifier):
        if self.publisher:
            try:
                self.publisher.publish_parse(identifier)
            except Exception:
                logging.getLogger(__name__).warning("Graph result retained; automatic repository save pending: %s", identifier)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)

    def read_history(self, subject, project_id, import_id, artifact_id, limit):
        _, path = self.repository.artifact(subject, project_id, artifact_id, import_id)
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        require(len(content) <= limit, "History exceeds parsing budget", "analysis_limit")
        return content

    def prepare(self, subject, project_id, import_id, node_ids=None):
        raw = self.repository.archive_bytes(subject, project_id, import_id)
        snapshot = self.repository.snapshot(subject, project_id, import_id)

        def reader(identifier, limit):
            _, path = self.repository.artifact_in_snapshot(snapshot, identifier)
            with path.open("rb") as stream:
                content = stream.read(limit + 1)
            require(len(content) <= limit, "History exceeds parsing budget", "analysis_limit")
            return content

        return prepare_graph(raw, import_id, reader, node_ids)

    def serialize(self, row):
        result = {key: row[key] for key in ("id", "import_id", "state", "error_code", "created_at", "updated_at")}
        result.update(parse_id=row["id"], source=loads(row["input"])["source"], engine=loads(row["engine"]),
                      graph_id=row["graph_id"] if row["state"] == "succeeded" else None,
                      revision_id=row["revision_id"] if row["state"] == "succeeded" else None,
                      usage=loads(row["usage"]) if row["usage"] is not None else None)
        result["publication"] = self.publisher.publication(row["id"]) if self.publisher else None
        result["error_detail"] = loads(row["error_detail"]) if row["error_detail"] else None
        return result

    def previous(self, database, subject, project_id, payload):
        row = database.execute("SELECT * FROM semantic_graph_parses WHERE subject=? AND project_id=? AND request_id=?",
                               (subject, project_id, payload["request_id"])).fetchone()
        if row is not None:
            require(row["request_hash"] == digest(payload), "Request ID has different content", "id_conflict")
        return row

    def submit(self, subject, project_id, payload):
        self.repository.authorize(subject, project_id)
        require(payload["allow_external_processing"] is True,
                "Parsing requires permission to send the reviewed facts to DeepSeek", "not_authorized")
        with self.repository.connection() as database:
            previous = self.previous(database, subject, project_id, payload)
        if previous is not None:
            return self.serialize(previous), 202 if previous["state"] in {"queued", "running"} else 200
        context = self.prepare(subject, project_id, payload["import_id"], payload.get("node_ids"))
        require(bool(context.prepared["facts"]), "Empty parsing input cannot be sent to the model", "analysis_empty")
        require(context.prepared["input_sha256"] == payload["input_sha256"],
                "Reviewed parsing input changed; prepare it again", "analysis_input_conflict")
        provider = self.provider.snapshot(context.prepared)
        engine = provider.describe()
        require(engine["configured"], "Graph provider is unavailable", "provider_unavailable")
        identifier, graph_id, revision_id, now = new_id(), new_id(), new_id(), utc_now()
        with self.repository.connection(write=True) as database:
            self.repository.owned_project(database, subject, project_id)
            previous = self.previous(database, subject, project_id, payload)
            if previous is not None:
                return self.serialize(previous), 202 if previous["state"] in {"queued", "running"} else 200
            active = database.execute("SELECT project_id FROM semantic_graph_parses WHERE state IN ('queued','running')").fetchall()
            require(not any(row["project_id"] == project_id for row in active) and len(active) < 2,
                    "Graph parser is busy; no model call made", "project_busy")
            branch = database.execute("SELECT id FROM semantic_graph_branches WHERE project_id=?", (project_id,)).fetchone()
            branch_id = branch["id"] if branch is not None else new_id()
            if branch is None:
                database.execute("INSERT INTO semantic_graph_branches VALUES (?,?,'main')", (branch_id, project_id))
            database.execute("""INSERT INTO semantic_graph_parses
                (id,project_id,import_id,subject,request_id,request_hash,state,engine,input,graph_id,revision_id,branch_id,created_at,updated_at)
                VALUES (?,?,?,?,?,?,'queued',?,?,?,?,?,?,?)""",
                (identifier, project_id, payload["import_id"], subject, payload["request_id"], digest(payload),
                 dumps(engine), dumps(context.prepared), graph_id, revision_id, branch_id, now, now))
            row = database.execute("SELECT * FROM semantic_graph_parses WHERE id=?", (identifier,)).fetchone()
        try:
            self.executor.submit(self.run, identifier, context, graph_id, revision_id, branch_id, provider)
        except RuntimeError as error:
            self.finish(identifier, "failed", error_code="parser_stopped")
            raise ProtocolError("Graph parser stopped before dispatch", "provider_unavailable") from error
        return self.serialize(row), 202

    def run(self, identifier, context, graph_id, revision_id, branch_id, provider):
        usage = None
        try:
            with self.repository.connection(write=True) as database:
                changed = database.execute("UPDATE semantic_graph_parses SET state='running',updated_at=? "
                                           "WHERE id=? AND state='queued'", (utc_now(), identifier)).rowcount
            if changed != 1:
                return
            response = provider.propose(context.prepared)
            usage = response["usage"]
            graph = assemble_graph(response["proposal"], context, graph_id, revision_id, branch_id, response["model"])
            self.finish(identifier, "succeeded", graph=graph, usage=response["usage"])
            self.publish(identifier)
        except ProtocolError as error:
            detail = {"stage": "graph_validation" if error.code == "invalid_semantic_graph" else error.code,
                      "errors": error.details[:10]}
            if isinstance(error, GraphResponseError):
                diagnostic = error.diagnostic
                detail = {key: diagnostic[key] for key in ("stage", "finish_reason", "errors", "max_tokens") if key in diagnostic}
                usage = diagnostic.get("usage", usage)
            self.finish(identifier, "unknown" if error.code == "provider_connection" else "failed", usage=usage,
                        error_code=error.code, error_detail=detail)
        except Exception:
            self.finish(identifier, "unknown", error_code="parse_interrupted")

    def finish(self, identifier, state, graph=None, usage=None, error_code=None, error_detail=None):
        with self.repository.connection(write=True) as database:
            database.execute("UPDATE semantic_graph_parses SET state=?,graph=?,usage=?,error_code=?,updated_at=?,error_detail=? "
                             "WHERE id=? AND state IN ('queued','running')",
                             (state, dumps(graph) if graph is not None else None, dumps(usage) if usage is not None else None,
                              error_code, utc_now(), dumps(redact(error_detail)) if error_detail else None, identifier))

    def detail(self, subject, project_id, parse_id):
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            row = database.execute("SELECT * FROM semantic_graph_parses WHERE id=? AND project_id=? AND subject=?",
                                   (parse_id, project_id, subject)).fetchone()
        require(row is not None, "Graph parse not found", "not_found")
        return self.serialize(row)

    def list(self, subject, project_id, offset, limit):
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            rows = database.execute("SELECT * FROM semantic_graph_parses WHERE project_id=? AND subject=? "
                                    "ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (project_id, subject, limit, offset)).fetchall()
        return [self.serialize(row) for row in rows]

    def graph(self, subject, project_id, graph_id, revision_id):
        with self.repository.connection() as database:
            self.repository.owned_project(database, subject, project_id)
            row = database.execute("SELECT graph FROM semantic_graph_parses WHERE project_id=? AND subject=? "
                                   "AND graph_id=? AND revision_id=? AND state='succeeded'",
                                   (project_id, subject, graph_id, revision_id)).fetchone()
        require(row is not None, "Graph revision not found", "not_found")
        return GraphDocument.model_validate(loads(row["graph"]))

    def evidence(self, subject, project_id, graph_id, revision_id, evidence_id):
        graph = self.graph(subject, project_id, graph_id, revision_id)
        evidence = next((item for item in graph.evidence if item.id == evidence_id), None)
        require(evidence is not None, "Evidence not found", "not_found")
        raw = self.repository.archive_bytes(subject, project_id, graph.source.import_id)
        history = {}
        try:
            if evidence.kind == "event":
                history[evidence.artifact_id] = self.read_history(subject, project_id, graph.source.import_id,
                                                                 evidence.artifact_id, HISTORY_LIMIT)
            resolved = resolve_evidence(graph.model_copy(update={"evidence": [evidence]}), loads(raw.decode()), raw, history)[evidence.id]
        except (OSError, ValueError):
            return {"source_ref": evidence.model_dump(), "availability": "unavailable", "role": "unknown",
                    "actor_id": None, "value": None}
        result = {"source_ref": evidence.model_dump(), "availability": "available", "role": resolved["role"],
                  "actor_id": resolved["actor_id"], "value": redact(resolved["value"])}
        value = resolved["value"]
        if evidence.kind == "archive" and evidence.pointer.startswith("/artifacts/") and isinstance(value, dict):
            result["content_state"] = "metadata_only"
            if value.get("mime_type", "").split(";")[0].strip() in TEXT_MIMES:
                try:
                    content, _ = read_material(value, lambda identifier, limit: self.read_history(
                        subject, project_id, graph.source.import_id, identifier, limit))
                except (OSError, ValueError, UnicodeError):
                    result["content_state"] = "unavailable"
                else:
                    result.update(content=content, content_state="available")
        return result
