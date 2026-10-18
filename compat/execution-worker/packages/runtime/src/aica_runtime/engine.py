import copy
import time
from pathlib import Path

from aica_mcp.files import atomic_write, read_artifact
from aica_mcp.store import Recorder
from aica_schema.protocol import ProtocolError, digest, dumps, loads, new_id, require, utc_now

from .executors import execution_plan
from .graph import active_nodes, golden_path, resolve_inputs


def cache_key(execution, inputs, controls, harness, tool, artifacts, stage):
    specification = {key: value for key, value in execution.items() if key != "inputs"}

    def normalize(value):
        if isinstance(value, str) and value in artifacts:
            artifact = artifacts[value]
            return {"sha256": artifact["sha256"], "mime_type": artifact["mime_type"]}
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    result = digest({"format": "aica-cache/0.1", "execution": normalize(specification),
                     "inputs": normalize(inputs), "controls": normalize(controls), "harness": harness,
                     "executor": {key: value for key, value in tool.items() if key != "handler"}, "stage": stage})
    return result


def replace_artifact_ids(value, replacements):
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        result = [replace_artifact_ids(item, replacements) for item in value]
        return result
    if isinstance(value, dict):
        result = {key: replace_artifact_ids(item, replacements) for key, item in value.items()}
        return result
    return value


def replay_plan(archive, registry, cache_get, read_blob, stage="draft", targets=None):
    """Predict only verifiable hits; execution checks keys and bytes again."""
    plan = execution_plan(archive, registry, targets)
    if not plan["executable"]:
        return {**plan, "decisions": {}, "stage": stage}
    harness = {"name": "AiCA registered Runtime", "version": "0.1.0",
               "config": {"executors": registry.describe()}, "state": {},
               "availability": {"executors": "recorded"}}
    outputs, keys, decisions = {}, {}, {}
    nodes = active_nodes(archive)
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    for identifier in plan["node_ids"]:
        node = nodes[identifier]
        parents = [edge["source"] for edge in archive["graph"]["edges"] if edge["target"] == identifier]
        decision = "REBUILD" if node["kind"] == "composition" else "RECOMPUTE"
        if all(parent in outputs for parent in parents):
            execution = node["execution"]
            tool = registry.resolve(execution)
            inputs = resolve_inputs(archive, node, outputs)
            controls = {edge["source"]: {"outputs": outputs[edge["source"]], "input_hash": keys[edge["source"]]}
                        for edge in archive["graph"]["edges"] if edge["target"] == identifier and edge["kind"] == "control"}
            key = cache_key(execution, inputs, controls, harness, tool, artifacts, stage)
            cached = cache_get(key) if tool["cacheable"] else None
            if cached is not None:
                try:
                    for artifact in cached["artifacts"]:
                        read_blob(artifact["sha256"])
                    outputs[identifier] = cached["outputs"]
                    keys[identifier] = key
                    artifacts.update({item["id"]: item for item in cached["artifacts"]})
                    decision = "CACHE_HIT"
                except ProtocolError as error:
                    if error.code not in {"artifact_unavailable", "artifact_conflict"}:
                        raise
        decisions[identifier] = decision
    return {**plan, "stage": stage, "decisions": decisions,
            "statistics": {name: list(decisions.values()).count(value) for name, value in
                           (("reused", "CACHE_HIT"), ("recomputed", "RECOMPUTE"), ("rebuilt", "REBUILD"))}}


class ExecutionContext:
    def __init__(self, engine, node_id, run_id, timeout):
        self.engine = engine
        self.node_id = node_id
        self.run_id = run_id
        self.artifacts = []
        self.deadline = time.monotonic() + timeout

    def check_cancelled(self):
        require(not self.engine.cancelled(), "Execution cancelled", "cancelled")
        require(time.monotonic() <= self.deadline, "Registered executor exceeded its deadline", "execution_timeout")

    def capture(self, content, mime_type):
        self.check_cancelled()
        require(isinstance(content, bytes) and len(content) <= 64 * 1024 * 1024,
                "Executor artifacts must be bytes of at most 64 MiB")
        path = self.engine.project / "outputs" / new_id()
        path.parent.mkdir(exist_ok=True)
        atomic_write(path, content)
        try:
            event = self.engine.event("artifact.recorded", {})
            result = self.engine.recorder.capture_artifact(self.engine.token, event,
                                                           path.relative_to(self.engine.project).as_posix(), mime_type,
                                                           self.node_id, self.run_id)
        finally:
            path.unlink(missing_ok=True)
        artifact = result["artifact"]
        if artifact["id"] not in {item["id"] for item in self.artifacts}:
            self.artifacts.append(artifact)
        return artifact

    def read_artifact(self, artifact_id):
        self.check_cancelled()
        document = self.engine.recorder.read_archive()["archive"]
        artifact = next((item for item in document["artifacts"] if item["id"] == artifact_id), None)
        require(artifact is not None, "Unknown input artifact", "artifact_unavailable")
        content = read_artifact(self.engine.project, artifact, 64 * 1024 * 1024)
        return content


class RuntimeEngine:
    def __init__(self, registry, cache_get, cache_put, read_blob, checkpoint, cancelled):
        self.registry = registry
        self.cache_get = cache_get
        self.cache_put = cache_put
        self.read_blob = read_blob
        self.checkpoint = checkpoint
        self.cancelled = cancelled
        self.recorder = None
        self.source_id = new_id()
        self.sequence = 0
        self.agent_id = None

    def event(self, kind, payload):
        self.sequence += 1
        return {"event_id": new_id(), "source_id": self.source_id, "source_seq": self.sequence,
                "session_id": self.session_id, "agent_id": self.agent_id, "type": kind,
                "timestamp": utc_now(), "causal_parents": [], "payload": payload}

    def emit(self, kind, payload):
        result = self.recorder.append(self.event(kind, payload), self.token)
        require(result["status"] == "success", "Execution recording failed")

    def publish(self):
        self.recorder.flush()
        archive = loads((self.project / "aica.json").read_text(encoding="utf-8"))
        self.checkpoint(archive)
        return archive

    def reuse(self, cached, context):
        replacements = {}
        content = [(artifact, self.read_blob(artifact["sha256"])) for artifact in cached["artifacts"]]
        for artifact, data in content:
            copied = context.capture(data, artifact["mime_type"])
            replacements[artifact["id"]] = copied["id"]
        outputs = replace_artifact_ids(cached["outputs"], replacements)
        return outputs

    def execute_node(self, node, outputs, controls, harness, stage):
        archive = self.recorder.read_archive()["archive"]
        execution = node["execution"]
        tool = self.registry.resolve(execution)
        inputs = resolve_inputs(archive, node, outputs)
        artifacts = {item["id"]: item for item in archive["artifacts"]}
        key = cache_key(execution, inputs, controls, harness, tool, artifacts, stage)
        run_id = new_id()
        cache = {"decision": "REBUILD" if node["kind"] == "composition" else "RECOMPUTE", "input_hash": key, "artifact_ids": []}
        run = {"id": run_id, "trace_id": self.source_id, "node_id": node["id"],
               "revision_id": archive["graph"]["revision_id"], "session_id": self.session_id,
               "agent_id": self.agent_id, "harness_id": self.harness_id, "state": "running",
               "started_at": utc_now(), "inputs": inputs, "outputs": {}, "artifact_ids": [],
               "provenance": {"mode": "recorded", "evidence": [{"source": "aica-runtime"}]},
               "cache": cache, "preview_stage": stage}
        self.emit("tool.started", run)
        self.publish()
        timeout = execution.get("limits", {}).get("timeout_seconds", 60)
        require(type(timeout) in {int, float} and 0 < timeout <= 300, "Executor timeout must be within 300 seconds")
        context = ExecutionContext(self, node["id"], run_id, timeout)
        context.stage = stage
        started = time.monotonic()
        result = {}
        state = "succeeded"
        error_message = None
        try:
            context.check_cancelled()
            cached = self.cache_get(key) if tool["cacheable"] else None
            if cached is not None:
                try:
                    result = self.reuse(cached, context)
                    cache["decision"] = "CACHE_HIT"
                except ProtocolError as error:
                    if error.code not in {"artifact_unavailable", "artifact_conflict"}:
                        raise
                    cache["decision"] = "REBUILD"
            if cache["decision"] != "CACHE_HIT":
                result = tool["handler"](copy.deepcopy(inputs), copy.deepcopy(execution["params"]), context)
            context.check_cancelled()
            require(isinstance(result, dict) and set(result) == set(execution["outputs"]),
                    "Executor outputs differ from declared ports", "invalid_output")
            dumps(result)
        except Exception as error:
            state = "cancelled" if isinstance(error, ProtocolError) and error.code == "cancelled" else "failed"
            error_message = str(error) if isinstance(error, ProtocolError) else "Registered executor failed"
            result = {}
        artifact_ids = [item["id"] for item in context.artifacts]
        cache["artifact_ids"] = artifact_ids
        finish = {"run_id": run_id, "state": state, "ended_at": utc_now(), "outputs": result,
                  "artifact_ids": artifact_ids, "duration_ms": (time.monotonic() - started) * 1000,
                  "cache": cache, "preview_stage": stage}
        if error_message is not None:
            finish["error"] = error_message
        self.emit("tool.finished", finish)
        published = self.publish()
        if state == "succeeded" and tool["cacheable"] and getattr(context, "cache_allowed", True):
            available = {item["id"]: item for item in published["artifacts"]}
            referenced = {item["id"] for item in context.artifacts}

            def collect(value):
                if isinstance(value, str) and value in available:
                    referenced.add(value)
                elif isinstance(value, dict):
                    for item in value.values():
                        collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)

            collect(result)
            self.cache_put(key, {"outputs": result, "artifacts": [available[key] for key in sorted(referenced)]})
        return run_id, state, result, key

    def run(self, project, targets=None, stage="draft"):
        self.project = Path(project)
        require(stage in {"draft", "final"}, "Invalid preview stage")
        self.recorder = Recorder(self.project)
        try:
            archive = self.recorder.read_archive()["archive"]
            plan = execution_plan(archive, self.registry, targets)
            require(plan["executable"], "Execution preflight failed", "execution_blocked")
            session = self.recorder.start_session(new_id(), True)
            self.session_id, self.token = session["session_id"], session["session_token"]
            harness = {"name": "AiCA registered Runtime", "version": "0.1.0",
                       "config": {"executors": self.registry.describe()}, "state": {},
                       "availability": {"executors": "recorded"}}
            self.harness_id = new_id()
            self.emit("harness.registered", {"id": self.harness_id, **harness})
            agent_id = new_id()
            self.emit("agent.registered", {"id": agent_id, "session_id": self.session_id,
                                           "parent_agent_id": None, "harness_id": self.harness_id,
                                           "client": "aica-runtime", "model": {}})
            self.agent_id = agent_id
            self.publish()
            nodes = active_nodes(archive)
            outputs, keys, run_ids = {}, {}, []
            state = "succeeded"
            for node_id in plan["node_ids"]:
                if self.cancelled():
                    state = "cancelled"
                    break
                controls = {edge["source"]: {"outputs": outputs[edge["source"]], "input_hash": keys[edge["source"]]}
                            for edge in archive["graph"]["edges"]
                            if edge["target"] == node_id and edge["kind"] == "control"}
                run_id, state, result, key = self.execute_node(nodes[node_id], outputs, controls, harness, stage)
                run_ids.append(run_id)
                outputs[node_id], keys[node_id] = result, key
                if state != "succeeded":
                    break
            self.emit("session.closed", {})
            final = self.publish()
            return {"status": "success" if state == "succeeded" else "error", "state": state,
                    "run_ids": run_ids, "golden_path": golden_path(final, targets)}
        finally:
            self.recorder.close()
