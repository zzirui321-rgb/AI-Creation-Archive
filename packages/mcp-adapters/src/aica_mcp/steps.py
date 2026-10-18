import copy
from datetime import datetime

from aica_schema.protocol import digest, dumps, loads, require, utc_now

from .capture import checked
from .files import ProjectLock, atomic_write, private_directory


def step_plan(archive, session_id, request_id, step):
    key = digest([session_id, request_id])
    now = utc_now()
    harness = {"name": "AiCA observation adapter", "version": "0.1.0", "config": {"source_client": step["client"]},
               "state": {}, "availability": {"original_harness": "missing", "host_system_instructions": "missing"}}
    harness["id"] = "harness-" + digest(harness)[:32]
    harness = copy.deepcopy(step.get("harness", harness))
    agent = {"id": "agent-" + digest([session_id, step["client"], step.get("model", {}), harness["id"]])[:32],
             "session_id": session_id, "parent_agent_id": None, "harness_id": harness["id"],
             "client": step["client"], "model": step.get("model", {})}
    node_id = "step-" + key[:32]
    provenance = step.get("provenance", {"mode": "recorded", "evidence": []})
    execution = step["execution"]
    node = {"id": node_id, "label": step["label"], "kind": "model" if execution["kind"] == "model" else "tool",
            "branch_id": "main", "provenance": provenance, "execution": execution,
            "description": step.get("description", "")}
    graph = copy.deepcopy(archive["graph"])
    active = [item["id"] for item in graph["branches"] if item["state"] == "active"]
    require(bool(active), "No active branch is available for this observed step")
    branch = active[0]
    node["branch_id"] = branch
    existing = {item["id"] for item in graph["nodes"]}
    require(set(step.get("depends_on", [])) <= existing, "A dependency is not in the current graph")
    graph["nodes"].append(node)
    graph["edges"].extend({"id": "edge-" + digest([source, node_id])[:32], "source": source,
                            "target": node_id, "kind": "control"} for source in step.get("depends_on", []))
    source_id = "step-source-" + key[:32]

    def event(sequence, kind, payload, actor=None):
        return {"event_id": "step-event-" + digest([key, sequence])[:32], "source_id": source_id,
                "source_seq": sequence, "session_id": session_id, "agent_id": actor, "type": kind,
                "timestamp": now, "causal_parents": [], "payload": payload}

    definition = event(3, "definition.replaced", {"graph": graph, "variables": archive["variables"],
                                                 "interactive_questions": archive["interactive_questions"]}, agent["id"])
    definition.update(expected_revision=archive["graph"]["revision_id"], branch_id=branch)
    revision = "revision-" + digest(definition["event_id"])[:32]
    run = {"id": "run-" + key[:32], "trace_id": "trace-" + key[:32], "node_id": node_id,
           "revision_id": revision, "session_id": session_id, "agent_id": agent["id"],
           "harness_id": harness["id"], "state": "running", "started_at": step["started_at"],
           "inputs": step.get("inputs", {}), "outputs": {}, "artifact_ids": [], "provenance": provenance}
    events = [event(1, "harness.registered", harness), event(2, "agent.registered", agent), definition,
              event(4, "tool.started", run, agent["id"])]
    captures = []
    for index, artifact in enumerate(step.get("artifacts", []), 5):
        captures.append({**artifact, "event": event(index, "artifact.recorded", {}, agent["id"]),
                         "producer_node_id": node_id, "producer_run_id": run["id"]})
    finish = event(5 + len(captures), "tool.finished", {}, agent["id"])
    return {"events": events, "captures": captures, "finish": finish, "node_id": node_id, "run_id": run["id"],
            "agent_id": agent["id"], "harness_id": harness["id"], "revision_id": revision}


def record_step(client, request_id, session_id, session_token, step, expected_revision=None):
    key = digest([session_id, request_id])
    finish_id = "step-event-" + digest([key, 5 + len(step.get("artifacts", []))])[:32]
    checked(client.call("aica_check_consent", {"session_id": session_id, "session_token": session_token,
                                               "event_type": "definition.replaced", "event_id": finish_id}))
    started = datetime.fromisoformat(step["started_at"].replace("Z", "+00:00"))
    ended = datetime.fromisoformat(step["ended_at"].replace("Z", "+00:00"))
    require(ended >= started, "Step ended_at precedes started_at")
    directory = client.directory / "outboxes" / "steps"
    private_directory(directory)
    path = directory / f"{key}.json"
    lock = ProjectLock(directory / f"{key}.lock")
    lock.acquire()
    try:
        request_hash = digest([step, expected_revision])
        require(not path.is_symlink(), "Step plan cannot be a symlink")
        if path.exists():
            plan = loads(path.read_text(encoding="utf-8"))
            require(plan["request_hash"] == request_hash, "Step request ID has different content", "id_conflict")
        else:
            archive = checked(client.call("aica_get_archive"))["archive"]
            require(expected_revision is None or expected_revision == archive["graph"]["revision_id"],
                    "Workflow revision changed; reload before recording", "revision_conflict")
            plan = step_plan(archive, session_id, request_id, step)
            plan["request_hash"] = request_hash
            atomic_write(path, dumps(plan).encode("utf-8"))
        if "result" in plan:
            return plan["result"]
        for event in plan["events"]:
            receipt = checked(client.call("aica_submit_event", {"session_token": session_token, "event": event}))
            require(receipt["projected"], "Step has unresolved references; retry after resolving its recording gaps")
        artifacts = []
        for capture in plan["captures"]:
            receipt = checked(client.call("aica_capture_artifact", {"session_token": session_token, **capture}))
            require(receipt["projected"], "Step artifact has unresolved references")
            artifacts.append(receipt["artifact"]["id"])
        finish = copy.deepcopy(plan["finish"])
        finish["payload"] = {"run_id": plan["run_id"], "state": step["state"], "ended_at": step["ended_at"],
                             "outputs": step.get("outputs", {}), "artifact_ids": list(dict.fromkeys(artifacts)),
                             "duration_ms": (ended - started).total_seconds() * 1000}
        if "error" in step:
            finish["payload"]["error"] = step["error"]
        receipt = checked(client.call("aica_submit_event", {"session_token": session_token, "event": finish}))
        require(receipt["projected"], "Step completion has unresolved references")
        result = {"status": "success", "node_id": plan["node_id"], "run_id": plan["run_id"],
                  "agent_id": plan["agent_id"], "harness_id": plan["harness_id"], "artifact_ids": artifacts,
                  "revision_id": plan["revision_id"], "commit_seq": receipt["commit_seq"], "executed": False}
        plan["result"] = result
        atomic_write(path, dumps(plan).encode("utf-8"))
        return result
    finally:
        lock.close()
