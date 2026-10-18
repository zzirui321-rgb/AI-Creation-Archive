import copy
import re
from datetime import datetime

from aica_schema.protocol import ProtocolError, digest, dumps, loads, require, require_archive

INPUT_LIMIT = 128 * 1024
HISTORY_LIMIT = 1024 * 1024
SECRET_KEYS = {"apikey", "accesstoken", "refreshtoken", "sessiontoken", "token", "password", "secret",
               "authorization", "privatekey", "reasoningcontent", "chainofthought"}


def redact(value, depth=0):
    if depth > 32:
        return "[REDACTED:depth]"
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.sub(r"[^a-z]", "", key.lower()) in SECRET_KEYS else redact(item, depth + 1)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                return dumps(redact(loads(value), depth + 1))
            except (ProtocolError, ValueError):
                pass
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)([?&](?:token|api_key|access_token|signature|sig|key|password)=)[^&\s\"']+",
                       r"\1[REDACTED]", value)
        value = re.sub(r"(https?://)[^/\s@]+:[^/\s@]+@", r"\1[REDACTED]@", value)
        return value
    return value


def normalized_node(node, variables):
    value = copy.deepcopy(node)
    execution = value.get("execution", {})
    bindings = execution.get("inputs", {})
    inputs = {}
    for name, binding in bindings.items():
        if binding["kind"] == "literal":
            item = binding["value"]
            if isinstance(item, str) and item.lstrip().startswith(("{", "[")):
                try:
                    item = loads(item)
                except (ProtocolError, ValueError):
                    pass
            inputs[name] = item
        elif binding["kind"] == "variable":
            inputs[name] = variables[binding["variable_id"]]["value"]
        else:
            inputs[name] = {"binding": binding}
    if execution:
        execution["input_bindings"] = bindings
        execution["inputs"] = inputs
    return value


def prepare_evidence(archive, import_id, archive_sha256, read_history=None, node_ids=None):
    require_archive(archive)
    nodes = {node["id"]: node for node in archive["graph"]["nodes"]}
    selected = set(nodes) if node_ids is None else set(node_ids)
    require(selected <= nodes.keys(), "Unknown analysis node", "invalid_analysis_scope")
    require(len(selected) <= 50, "Select at most 50 nodes for one analysis", "analysis_limit")
    facts, warnings = [], []

    def add(identifier, value, source, related=()):
        facts.append({"id": identifier, "node_ids": list(related), "source": source,
                      "original_digest": digest(value), "value": redact(value)})

    branches = {branch["id"]: branch["state"] for branch in archive["graph"]["branches"]}
    for index, node in enumerate(archive["graph"]["nodes"]):
        if node["id"] in selected:
            value = normalized_node(node, archive["variables"])
            value["branch_state"] = branches[node["branch_id"]]
            add("node:" + node["id"], value, {"kind": "archive", "pointer": f"/graph/nodes/{index}",
                                             "normalization": "literal-inputs-and-JSON-arguments"}, [node["id"]])
    latest = {}
    relevant_artifacts = set()
    for index, run in enumerate(archive["runs"]):
        if run["node_id"] in selected:
            add("run:" + run["id"], run, {"kind": "archive", "pointer": f"/runs/{index}"}, [run["node_id"]])
            relevant_artifacts.update(run["artifact_ids"])
            if run["revision_id"] == archive["graph"]["revision_id"]:
                try:
                    timestamp = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00")).timestamp()
                except ValueError:
                    warnings.append({"code": "run_timestamp_invalid", "run_id": run["id"]})
                else:
                    previous = latest.get(run["node_id"])
                    if previous is None or (timestamp, index) > previous[:2]:
                        latest[run["node_id"]] = (timestamp, index, run["state"])
    for node_id in selected:
        for binding in nodes[node_id].get("execution", {}).get("inputs", {}).values():
            if binding["kind"] == "artifact":
                relevant_artifacts.add(binding["artifact_id"])
    for index, artifact in enumerate(archive["artifacts"]):
        if artifact["id"] in relevant_artifacts or artifact["producer_node_id"] in selected:
            add("artifact:" + artifact["id"], artifact, {"kind": "archive", "pointer": f"/artifacts/{index}"},
                [artifact["producer_node_id"]] if artifact["producer_node_id"] else [])
    add("variables", archive["variables"], {"kind": "archive", "pointer": "/variables"})
    add("snapshot", archive["snapshot"], {"kind": "archive", "pointer": "/snapshot"})
    remaining = HISTORY_LIMIT
    seen = set()
    history_count = 0
    for reference in archive.get("history", []):
        if reference["kind"] != "events" or reference["artifact_id"] in seen:
            continue
        artifact_id = reference["artifact_id"]
        seen.add(artifact_id)
        if read_history is None or history_count >= 16 or remaining <= 0:
            warnings.append({"code": "history_not_read", "artifact_id": artifact_id})
            continue
        try:
            content = read_history(artifact_id, remaining)
            require(len(content) <= remaining, "Event history exceeds read budget", "analysis_limit")
            remaining -= len(content)
            history_count += 1
            for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                event = loads(line)["event"]
                if event.get("type") not in {"message.recorded", "note.recorded"}:
                    continue
                payload = event["payload"]
                if event["type"] == "message.recorded" and payload.get("role") == "system":
                    warnings.append({"code": "system_message_excluded", "artifact_id": artifact_id, "line": line_number})
                    continue
                related = [payload["node_id"]] if payload.get("node_id") in selected else []
                add(f"event:{artifact_id}:{line_number}", {"type": event["type"], "payload": payload},
                    {"kind": "artifact", "artifact_id": artifact_id, "line": line_number,
                     "event_id": event.get("event_id")}, related)
        except (OSError, ProtocolError, ValueError, KeyError, TypeError, UnicodeError):
            warnings.append({"code": "history_unavailable_or_invalid", "artifact_id": artifact_id})
    edges = [edge for edge in archive["graph"]["edges"] if edge["source"] in selected and edge["target"] in selected]
    external_parents = sorted({edge["target"] for edge in archive["graph"]["edges"]
                               if edge["target"] in selected and edge["source"] not in selected})
    prepared = {"format": "aica-semantic-input/0.1",
                "source": {"import_id": import_id, "archive_sha256": archive_sha256,
                           "revision_id": archive["graph"]["revision_id"]},
                "node_ids": sorted(selected), "facts": facts, "declared_dependencies": edges,
                "latest_successful_node_ids": sorted(node for node, record in latest.items()
                                                      if record[2] == "succeeded" and branches[nodes[node]["branch_id"]] == "active"),
                "external_parent_nodes": external_parents,
                "coverage": {"selected_nodes": len(selected), "total_nodes": len(nodes), "history_files": history_count,
                             "warnings": warnings, "recording_complete": archive["snapshot"]["complete"]}}
    encoded = dumps(prepared)
    require(re.search(r"\bsk-[A-Za-z0-9_-]{12,}\b", encoded) is None,
            "A source identifier contains credentials; review the selected export", "analysis_sensitive_identifiers")
    require(len(facts) <= 400 and len(encoded.encode("utf-8")) <= INPUT_LIMIT,
            "Analysis context exceeds 128 KiB or 400 facts; select fewer nodes", "analysis_limit")
    prepared["input_sha256"] = digest(prepared)
    return prepared
