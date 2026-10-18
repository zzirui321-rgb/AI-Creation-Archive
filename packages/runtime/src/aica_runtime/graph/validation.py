import hashlib
import re

from aica_schema.protocol import ProtocolError, loads, require_archive, require_dag
from aica_schema.recording import require_event

from .contract import GraphDocument


class ContractError(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise ContractError(message)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def pointer(value, path):
    check(path == "" or path.startswith("/"), "Invalid JSON pointer")
    check(re.search(r"~(?![01])", path) is None, "Invalid JSON pointer escape")
    if not path:
        return value
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                check(bool(re.fullmatch(r"0|[1-9][0-9]*", part)), "Invalid array index")
                value = value[int(part)]
            else:
                value = value[part]
        return value
    except (KeyError, IndexError, TypeError) as error:
        raise ContractError("JSON pointer does not resolve") from error


def unique(items, message):
    check(len(items) == len(set(items)), message)


def claims(document):
    for node in document.nodes:
        yield node.purpose
        yield from node.requirements
        for page in node.pages:
            yield page.summary
    for edge in document.edges:
        yield edge.explanation
    for variable in document.variables:
        yield variable.description


def resolve_evidence(document, archive, archive_bytes, history):
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    event_artifacts = {item["artifact_id"] for item in archive["history"] if item["kind"] == "events"}
    resolved = {}
    for evidence in document.evidence:
        if evidence.kind == "archive":
            check(evidence.source_sha256 == sha256(archive_bytes), "Archive evidence hash mismatch")
            value = pointer(archive, evidence.pointer)
            resolved[evidence.id] = {"value": value, "role": "unknown", "actor_id": None, "event": None}
        else:
            check(evidence.artifact_id in event_artifacts, "Event evidence is outside declared history")
            content = history.get(evidence.artifact_id)
            check(content is not None, "Event evidence file is unavailable")
            check(evidence.source_sha256 == artifacts[evidence.artifact_id]["sha256"] == sha256(content),
                  "Event evidence hash mismatch")
            try:
                envelope = loads(content.decode("utf-8").splitlines()[evidence.line - 1])
                event = envelope["event"]
            except (IndexError, KeyError, UnicodeError, ProtocolError) as error:
                raise ContractError("Event evidence line is invalid") from error
            require_event(event)
            check(envelope.get("stream_id") == document.source.stream_id, "Event stream mismatch")
            sequence = envelope.get("commit_seq")
            check(type(sequence) is int and 1 <= sequence <= document.source.through_seq, "Event exceeds snapshot boundary")
            check(event["event_id"] == evidence.event_id, "Event identity mismatch")
            value = pointer(event, evidence.pointer)
            check(event["payload"].get("role") != "system", "System messages are not semantic evidence")
            role = event["payload"].get("role", "unknown") if (
                event["type"] == "message.recorded" and evidence.pointer == "/payload/content") else "unknown"
            role = "agent" if role == "assistant" else role
            check(role != "system", "System messages are not semantic evidence")
            resolved[evidence.id] = {"value": value, "role": role, "actor_id": event.get("agent_id"), "event": event}
    return resolved


def validate_document(content, archive_bytes, history):
    document = GraphDocument.model_validate(content)
    check(document.parent_revision_id != document.revision_id, "Semantic revision cannot be its own parent")
    archive = loads(archive_bytes.decode("utf-8"))
    require_archive(archive)
    source = document.source
    check(source.archive_sha256 == sha256(archive_bytes), "Source archive hash mismatch")
    check(source.archive_revision_id == archive["graph"]["revision_id"], "Source revision mismatch")
    snapshot = archive["snapshot"]
    check((source.snapshot_id, source.stream_id, source.through_seq) ==
          (snapshot["snapshot_id"], snapshot["stream_id"], snapshot["commit_seq"]), "Source snapshot mismatch")
    for collection in (document.nodes, document.edges, document.variables, document.evidence, document.coverage.issues):
        unique([item.id for item in collection], "Duplicate object ID")
    source_nodes = {node["id"]: node for node in archive["graph"]["nodes"]}
    runs = {run["id"]: run for run in archive["runs"]}
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    raw_edges = {edge["id"]: edge for edge in archive["graph"]["edges"]}
    nodes = {node.id: node for node in document.nodes}
    evidence = resolve_evidence(document, archive, archive_bytes, history)
    evidence_models = {item.id: item for item in document.evidence}

    def cited(identifiers):
        unique(identifiers, "Duplicate evidence reference")
        check(set(identifiers) <= evidence.keys(), "Unknown evidence reference")

    for claim in claims(document):
        cited(claim.evidence_ids)
        check(not (claim.origin == "llm" and claim.review == "accepted"), "LLM cannot approve its own interpretation")
        if claim.attributed_to in {"user", "agent", "tool"}:
            supported = [evidence[key] for key in claim.evidence_ids if evidence[key]["role"] == claim.attributed_to]
            check(bool(supported), "Claim attribution lacks matching source role")
            if claim.actor_id is not None:
                check(any(item["actor_id"] == claim.actor_id for item in supported), "Claim actor does not match evidence")
        else:
            check(claim.actor_id is None, "Unknown or analyst attribution cannot invent an actor")

    owners = {}
    for node in document.nodes:
        cited(node.evidence_ids)
        unique([page.id for page in node.pages], "Duplicate page identity")
        for prompt in [*node.prompts, *(prompt for page in node.pages for prompt in page.prompts)]:
            cited(prompt.evidence_ids)
            check(prompt.kind != "recorded" or prompt.source_pointer is not None, "Original prompt needs a source location")
        for page in node.pages:
            cited(page.evidence_ids)
            check(set(page.artifact_ids) <= set(node.artifact_ids), "Page image is outside its module")
        for identifiers in (node.source_node_ids, node.run_ids, node.artifact_ids):
            unique(identifiers, "Duplicate source reference")
        for raw_id in node.source_node_ids:
            check(raw_id in source_nodes, "Unknown source node")
            check(raw_id not in owners, "Source node has two owning steps")
            owners[raw_id] = node.id
            check(any(evidence_models[key].kind == "archive" and evidence[key]["value"] == source_nodes[raw_id]
                      for key in node.evidence_ids), "Step must cite its owned source node")
        for run_id in node.run_ids:
            check(run_id in runs and runs[run_id]["node_id"] in node.source_node_ids, "Run is assigned to the wrong step")
            check(runs[run_id]["revision_id"] == source.archive_revision_id, "Historical Run cannot impersonate current execution")
        for artifact_id in node.artifact_ids:
            check(artifact_id in artifacts, "Unknown artifact")
            artifact = artifacts[artifact_id]
            if artifact["producer_node_id"] is None:
                check(any(evidence_models[key].kind == "archive" and evidence[key]["value"] == artifact
                          for key in node.evidence_ids), "Unbound material must cite its registered artifact")
                check(artifact_id not in {ref["artifact_id"] for ref in archive["history"]}, "History is not a creative output")
            else:
                check(artifact["producer_node_id"] in node.source_node_ids, "Output artifact is assigned to the wrong step")

    coverage = document.coverage
    if any(artifacts[key]["producer_node_id"] is None for node in document.nodes for key in node.artifact_ids):
        check(any(issue.code == "ARTIFACT_ONLY_SOURCE" for issue in coverage.issues),
              "Material-derived steps must disclose missing execution provenance")
    unique(coverage.selected_source_node_ids, "Duplicate selected source")
    unique(coverage.unassigned_source_node_ids, "Duplicate unassigned source")
    unique(coverage.omitted_history_artifact_ids, "Duplicate omitted history")
    event_artifacts = {item["artifact_id"] for item in archive["history"] if item["kind"] == "events"}
    check(set(coverage.omitted_history_artifact_ids) <= event_artifacts, "Unknown omitted history")
    check(event_artifacts - history.keys() <= set(coverage.omitted_history_artifact_ids), "Unavailable history is not reported")
    check(set(coverage.selected_source_node_ids) <= source_nodes.keys(), "Unknown selected source")
    check(set(owners).isdisjoint(coverage.unassigned_source_node_ids), "Assigned source is also unassigned")
    check(set(owners) | set(coverage.unassigned_source_node_ids) == set(coverage.selected_source_node_ids),
          "Coverage hides or invents source nodes")
    check(coverage.recording_complete == snapshot["complete"], "Recording completeness was changed")
    for issue in coverage.issues:
        cited(issue.evidence_ids)
        check(set(issue.node_ids) <= nodes.keys(), "Issue references unknown step")
    if not snapshot["complete"] or snapshot["gaps"] or coverage.unassigned_source_node_ids or coverage.omitted_history_artifact_ids:
        check(bool(coverage.issues), "Incomplete coverage needs an explicit issue")
    selected = set(coverage.selected_source_node_ids)
    if any((edge["source"] in selected) != (edge["target"] in selected) for edge in raw_edges.values()):
        check(any(issue.code == "EXTERNAL_DEPENDENCY" for issue in coverage.issues), "Subgraph dependency boundary is not reported")
    if any(run["revision_id"] != source.archive_revision_id for run in runs.values()):
        check(any(issue.code == "HISTORICAL_RUN_SCOPE" for issue in coverage.issues),
              "Historical execution outside the selected definition is not reported")

    execution_edges = []
    for edge in document.edges:
        check(edge.source in nodes and edge.target in nodes, "Edge references unknown step")
        check(edge.source != edge.target, "Internal relationships belong in expanded call details")
        if edge.kind == "semantic" or edge.state == "candidate":
            continue
        raw = raw_edges.get(edge.archive_edge_id)
        check(raw is not None and raw["kind"] == edge.kind, "Verified dependency lacks an A dependency")
        check(owners.get(raw["source"]) == edge.source and owners.get(raw["target"]) == edge.target,
              "Verified dependency endpoints do not match source ownership")
        if edge.kind == "data":
            check(edge.binding is not None, "Verified data dependency needs ports")
            binding = edge.binding.model_dump()
            expected = {"source_node_id": raw["source"], "source_output": raw["source_output"],
                        "target_node_id": raw["target"], "target_input": raw["target_input"]}
            check(binding == expected, "Data ports do not match A binding")
        execution_edges.append((edge.source, edge.target))
    verified_raw_ids = {edge.archive_edge_id for edge in document.edges if edge.kind != "semantic" and edge.state == "verified"}
    for raw in raw_edges.values():
        if raw["source"] in owners and raw["target"] in owners and owners[raw["source"]] != owners[raw["target"]]:
            check(raw["id"] in verified_raw_ids, "Graph hides a declared cross-step dependency")
    require_dag(nodes, execution_edges)

    for variable in document.variables:
        check(variable.step_id in nodes, "Variable references unknown step")
        locator = variable.locator
        check(locator.source_node_id in nodes[variable.step_id].source_node_ids, "Variable belongs to another step")
        cited([locator.evidence_id])
        check(evidence[locator.evidence_id]["value"] == source_nodes[locator.source_node_id],
              "Variable locator must cite its source node")
        check(locator.pointer.startswith(("/execution/inputs/", "/execution/params/")), "Variable is not an input or parameter")
        if locator.pointer.startswith("/execution/inputs/"):
            parts = locator.pointer.split("/")
            check(len(parts) >= 5, "Variable must point into a literal value")
            input_name = parts[3].replace("~1", "/").replace("~0", "~")
            raw_binding = source_nodes[locator.source_node_id]["execution"]["inputs"].get(input_name, {})
            check(raw_binding.get("kind") == "literal" and parts[4] == "value", "Only literal input leaves are editable in v0.1")
        actual = pointer(source_nodes[locator.source_node_id], locator.pointer)
        if locator.decode == "json_string":
            check(isinstance(actual, str), "JSON-string decoder requires a string")
            actual = loads(actual)
        actual = pointer(actual, locator.value_pointer)
        check(type(actual) is type(variable.current_value) and actual == variable.current_value, "Variable value was invented")
        kinds = {str: "string", list: "array", bool: "boolean", int: "integer", float: "number", dict: "object"}
        check(kinds.get(type(actual)) == variable.value_type, "Variable type mismatch")
        unique(variable.allowed_modes, "Duplicate variable mode")
        check("keep" in variable.allowed_modes, "Variable must allow keep")
        if "append" in variable.allowed_modes:
            check(variable.value_type in {"string", "array"}, "Append requires a string or array")
        check((variable.separator is not None) == (variable.value_type == "string" and "append" in variable.allowed_modes),
              "Separator is only required for string append")
    if document.state == "accepted":
        check(all(claim.review != "candidate" for claim in claims(document)), "Accepted graph contains unreviewed claims")
        check(all(variable.review != "candidate" for variable in document.variables), "Accepted graph contains unreviewed variables")
        check(not any(issue.severity == "error" for issue in coverage.issues), "Accepted graph contains blocking issues")
    return document
