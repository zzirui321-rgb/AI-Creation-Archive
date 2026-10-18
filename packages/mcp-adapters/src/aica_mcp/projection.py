import copy

from aica_schema.protocol import (
    ProtocolError,
    digest,
    require,
    require_archive,
    resolve_variable_operations,
)


class PendingReference(Exception):
    pass


def find(items, item_id, kind):
    matches = [item for item in items if item["id"] == item_id]
    if not matches:
        raise PendingReference(f"Missing {kind}: {item_id}")
    return matches[0]


def insert_immutable(items, value, kind):
    matches = [item for item in items if item["id"] == value["id"]]
    require(not matches or matches[0] == value, f"{kind} ID already has different content", "identity_conflict")
    if not matches:
        items.append(copy.deepcopy(value))


def apply_event(archive, event, preserve_revision, read_definition):
    result = copy.deepcopy(archive)
    payload = event["payload"]
    kind = event["type"]
    if event["agent_id"] is not None and kind != "agent.registered":
        actor = find(result["agents"], event["agent_id"], "Agent")
        require(actor["session_id"] == event["session_id"], "Event actor belongs to another Session")
    if kind == "session.created":
        insert_immutable(result["sessions"], payload, "Session")
    elif kind == "recorder.recovered":
        for run in result["runs"]:
            if run["id"] in payload["run_ids"] and run["state"] == "running":
                run["state"] = "unknown"
    elif kind == "harness.registered":
        for artifact_id in payload.get("artifact_ids", []):
            find(result["artifacts"], artifact_id, "Harness artifact")
        insert_immutable(result["harnesses"], payload, "Harness")
    elif kind == "agent.registered":
        find(result["sessions"], payload["session_id"], "Session")
        find(result["harnesses"], payload["harness_id"], "Harness")
        if payload["parent_agent_id"] is not None:
            find(result["agents"], payload["parent_agent_id"], "parent Agent")
        require(payload["session_id"] == event["session_id"], "Agent belongs to a different Session")
        insert_immutable(result["agents"], payload, "Agent")
    elif kind in ("definition.replaced", "variables.changed"):
        require(event.get("expected_revision") == result["graph"]["revision_id"],
                "Workflow revision changed; reload before editing", "revision_conflict")
        branches = payload["graph"]["branches"] if kind == "definition.replaced" else result["graph"]["branches"]
        require(any(branch["id"] == event["branch_id"] for branch in branches), "Unknown target branch")
        preserve_revision(result)
        previous_revision = result["graph"]["revision_id"]
        if kind == "definition.replaced":
            for field in ("graph", "variables", "interactive_questions"):
                result[field] = copy.deepcopy(payload[field])
            result["variable_operations"] = []
        else:
            result["variables"] = resolve_variable_operations(result["variables"], payload["operations"])
            result["variable_operations"].extend(copy.deepcopy(payload["operations"]))
        result["graph"]["parent_revision_id"] = previous_revision
        result["graph"]["revision_id"] = "revision-" + digest(event["event_id"])[:32]
        modes = {node["provenance"]["mode"] for node in result["graph"]["nodes"]}
        if modes:
            result["metadata"]["source_mode"] = ("live_recorded" if modes == {"recorded"}
                                                  else "mixed" if "recorded" in modes else "extracted")
    elif kind == "tool.started":
        find(result["sessions"], payload["session_id"], "Session")
        find(result["agents"], payload["agent_id"], "Agent")
        find(result["harnesses"], payload["harness_id"], "Harness")
        if payload["revision_id"] == result["graph"]["revision_id"]:
            find(result["graph"]["nodes"], payload["node_id"], "node")
        else:
            known = any(item.get("revision_id") == payload["revision_id"]
                        for item in result.get("history", []) if item["kind"] == "revision")
            if not known:
                raise PendingReference(f"Missing workflow revision: {payload['revision_id']}")
            definition = read_definition(result, payload["revision_id"])
            require(any(node["id"] == payload["node_id"] for node in definition["graph"]["nodes"]),
                    "Unknown node in historical execution revision")
        require(payload["state"] == "running" and not payload["artifact_ids"] and not payload["outputs"],
                "tool.started must describe an unfinished attempt")
        require(payload["agent_id"] == event["agent_id"] and payload["session_id"] == event["session_id"],
                "Execution actor disagrees with event actor")
        insert_immutable(result["runs"], payload, "Run")
    elif kind == "artifact.recorded":
        if payload["producer_run_id"] is not None:
            run = find(result["runs"], payload["producer_run_id"], "Run")
            require(run["session_id"] == event["session_id"], "Artifact belongs to another Session's execution")
            require(payload["producer_node_id"] == run["node_id"], "Artifact producer node disagrees with Run")
        matches = [item for item in result["artifacts"] if item["id"] == payload["id"]]
        if matches:
            existing = matches[0]
            require({key: value for key, value in existing.items() if key != "locations"}
                    == {key: value for key, value in payload.items() if key != "locations"}
                    and set(payload.get("locations", [])) <= set(existing.get("locations", [])),
                    "Artifact ID already has different content", "identity_conflict")
        else:
            insert_immutable(result["artifacts"], payload, "Artifact")
        if payload["producer_run_id"] is not None and payload["id"] not in run["artifact_ids"]:
            run["artifact_ids"].append(payload["id"])
        if "source_path" in event:
            result.setdefault("extensions", {}).setdefault("aica.sync.files", {})[event["source_path"]] = payload["id"]
    elif kind == "files.changed":
        for location, identifier in payload["files"].items():
            if identifier is not None:
                find(result["artifacts"], identifier, "Artifact")
            result.setdefault("extensions", {}).setdefault("aica.sync.files", {})[location] = identifier
    elif kind == "artifact.location_added":
        artifact = find(result["artifacts"], payload["artifact_id"], "Artifact")
        require(artifact["sha256"] == payload["sha256"], "Artifact content identity changed", "artifact_conflict")
        if payload["uri"] != artifact["uri"] and payload["uri"] not in artifact.get("locations", []):
            artifact.setdefault("locations", []).append(payload["uri"])
    elif kind == "tool.finished":
        run = find(result["runs"], payload["run_id"], "Run")
        require(run["agent_id"] == event["agent_id"] and run["session_id"] == event["session_id"],
                "Only the execution actor can finish an attempt")
        require(run["state"] in ("running", "unknown"), "Attempt is already finished", "execution_conflict")
        for artifact_id in payload["artifact_ids"]:
            find(result["artifacts"], artifact_id, "Artifact")
        for key, value in payload.items():
            if key != "run_id":
                run[key] = copy.deepcopy(value)
    elif kind in ("session.paused", "session.resumed", "session.closed"):
        session = find(result["sessions"], event["session_id"], "Session")
        states = {"session.paused": ("active", "paused"), "session.resumed": ("paused", "active")}
        if kind in states:
            previous, current = states[kind]
            require(session["state"] == previous, f"Cannot {kind} from {session['state']}", "session_state")
            session["state"] = current
        else:
            require(session["state"] != "closed", "Session is already closed", "session_state")
            session["state"] = "closed"
    elif kind == "message.recorded" and "node_id" in payload:
        find(result["graph"]["nodes"], payload["node_id"], "node")
    result["metadata"]["updated_at"] = event["timestamp"]
    require_archive(result)
    return result


def project_events(seed, records, preserve_revision, read_definition, reject_id=None, previous=None):
    document = copy.deepcopy(previous if previous is not None else seed)
    applied = set()
    source_heads = {}
    remaining = []
    boundary = previous["snapshot"]["commit_seq"] if previous is not None else 0
    pending_ids = {gap.get("event_id") for gap in previous["snapshot"]["gaps"]} if previous is not None else set()
    for record in records:
        event = record["event"]
        if record["seq"] <= boundary and event["event_id"] not in pending_ids:
            applied.add(event["event_id"])
            source_heads[event["source_id"]] = max(source_heads.get(event["source_id"], 0), event["source_seq"])
        else:
            remaining.append(record)
    while remaining:
        progressed = False
        waiting = []
        for record in remaining:
            event = record["event"]
            source = event["source_id"]
            expected_seq = source_heads.get(source, 0) + 1
            ready = event["source_seq"] == expected_seq and set(event["causal_parents"]) <= applied
            if ready:
                try:
                    candidate = apply_event(document, event, preserve_revision, read_definition)
                except (PendingReference, ProtocolError) as error:
                    if isinstance(error, ProtocolError) and event["event_id"] == reject_id:
                        raise
                    record = {**record, "reason": str(error)}
                    waiting.append(record)
                else:
                    document = candidate
                    applied.add(event["event_id"])
                    source_heads[source] = event["source_seq"]
                    progressed = True
            else:
                waiting.append({**record, "reason": "Missing earlier source event or causal parent"})
        remaining = waiting
        if not progressed:
            break
    if reject_id is not None:
        mutations = [record for record in remaining if record["event"]["event_id"] == reject_id
                     and record["event"]["type"] in ("definition.replaced", "variables.changed")]
        require(not mutations, "Definition mutation has unresolved dependencies", "pending_definition")
    gaps = copy.deepcopy(seed["snapshot"]["gaps"]) + [
            {"event_id": record["event"]["event_id"], "source_id": record["event"]["source_id"],
             "source_seq": record["event"]["source_seq"], "reason": record.get("reason", "Unresolved event")}
            for record in remaining]
    document["snapshot"]["gaps"] = gaps
    document["snapshot"]["complete"] = not gaps
    document["snapshot"]["commit_seq"] = max((record["seq"] for record in records), default=0)
    require_archive(document)
    return document
