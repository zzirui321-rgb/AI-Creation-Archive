import copy
from concurrent.futures import ThreadPoolExecutor

from aica_mcp.store import Recorder
from aica_runtime.engine import RuntimeEngine
from aica_runtime.executors import execution_plan
from aica_runtime.graph import dirty_nodes, fork_definition
from aica_schema.protocol import ProtocolError, digest, dumps, require


def fork_project(store, owner, project_id, payload):
    def transform(archive):
        definition = {key: copy.deepcopy(archive.get(key, []))
                      for key in ("graph", "variables", "interactive_questions", "variable_operations")}
        sha256 = store.put_blob(project_id, dumps(definition).encode("utf-8"))
        artifact = {"id": "revision-" + sha256, "uri": "objects/" + sha256, "mime_type": "application/json",
                    "sha256": sha256, "size": len(dumps(definition).encode("utf-8")), "availability": "available",
                    "producer_node_id": None, "producer_run_id": None}
        result = fork_definition(archive, payload["expected_revision"], payload["operations"], artifact)
        dirty = dirty_nodes(archive, result)
        return result, dirty

    result = store.save_fork(owner, project_id, payload, transform)
    return result


class JobManager:
    def __init__(self, store, registry):
        self.store = store
        self.registry = registry
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aica-runtime")

    def recover(self):
        with self.store.transaction() as database:
            rows = database.execute("SELECT jobs.*,projects.owner FROM jobs JOIN projects "
                                    "ON jobs.project_id=projects.id WHERE jobs.state IN ('queued','running')").fetchall()
        for row in rows:
            workspace = self.store.directory / "work" / row["id"]
            if (workspace / ".aica" / "recording.sqlite3").exists():
                recorder = Recorder(workspace)
                try:
                    recorder.flush()
                    archive = recorder.read_archive()["archive"]
                    self.store.checkpoint(row["project_id"], row["id"], workspace, archive)
                finally:
                    recorder.close()
            self.store.set_job_state(row["id"], "unknown", {"status": "error", "code": "server_restarted",
                                                           "message": "Execution interrupted; no automatic replay"})

    def submit(self, owner, project_id, payload, registry=None):
        registry = registry or self.registry
        with self.store.transaction() as database:
            self.store.project(database, project_id, owner)
            previous = self.store.previous_action(database, project_id, "execution", payload["request_id"], digest(payload))
        if previous is not None:
            return previous
        archive = self.store.read_archive(owner, project_id, payload["archive_id"])["archive"]
        require(archive["graph"]["revision_id"] == payload["expected_revision"],
                "Workflow revision changed", "revision_conflict")
        plan = execution_plan(archive, registry, payload.get("target_node_ids"))
        if not plan["executable"]:
            raise ProtocolError("Execution preflight failed", "execution_blocked", plan["blockers"])
        result, created = self.store.create_job(owner, project_id, payload)
        if created:
            self.pool.submit(self.execute, owner, project_id, result["job_id"], payload, archive, registry)
        return result

    def execute(self, owner, project_id, job_id, payload, archive, registry=None):
        self.store.set_job_state(job_id, "running")
        try:
            workspace = self.store.prepare_workspace(project_id, job_id, archive)
            engine = RuntimeEngine(registry or self.registry,
                                   lambda key: self.store.cache_get(project_id, key),
                                   lambda key, value: self.store.cache_put(project_id, key, value),
                                   lambda sha256: self.store.read_blob(project_id, sha256),
                                   lambda document: self.store.checkpoint(project_id, job_id, workspace, document),
                                   lambda: self.store.cancelled(job_id))
            result = engine.run(workspace, payload.get("target_node_ids"), payload["stage"])
            self.store.set_job_state(job_id, result["state"], result)
        except Exception as error:
            code = error.code if isinstance(error, ProtocolError) else "execution_error"
            workspace = self.store.directory / "work" / job_id
            try:
                if (workspace / ".aica" / "recording.sqlite3").exists():
                    recorder = Recorder(workspace)
                    try:
                        recorder.flush()
                        self.store.checkpoint(project_id, job_id, workspace, recorder.read_archive()["archive"])
                    finally:
                        recorder.close()
            finally:
                self.store.set_job_state(job_id, "unknown", {"status": "error", "code": code,
                                                            "message": "Execution stopped; inspect persisted checkpoints"})

    def shutdown(self):
        with self.store.transaction() as database:
            database.execute("UPDATE jobs SET cancel_requested=1 WHERE state IN ('queued','running')")
        self.pool.shutdown(wait=True)
