import copy
import re

from aica_schema.protocol import new_id, require, require_archive, resolve_variable_operations, utc_now


def active_nodes(archive):
    branches = {item["id"] for item in archive["graph"]["branches"] if item["state"] == "active"}
    result = {item["id"]: item for item in archive["graph"]["nodes"] if item["branch_id"] in branches}
    return result


def ordered_nodes(archive, targets=None):
    require_archive(archive)
    nodes = active_nodes(archive)
    selected = set(nodes if targets is None else targets)
    require(selected <= nodes.keys(), "Targets must reference active nodes", "invalid_target")
    parents = {node: set() for node in nodes}
    for edge in archive["graph"]["edges"]:
        if edge["target"] in nodes:
            parents[edge["target"]].add(edge["source"])
    pending = list(selected)
    while pending:
        node = pending.pop()
        require(node in nodes, "Active node depends on an inactive branch", "inactive_dependency")
        missing = parents[node] - selected
        selected.update(missing)
        pending.extend(missing)
    result = []
    remaining = set(selected)
    while remaining:
        ready = sorted(node for node in remaining if not parents[node] & remaining)
        require(bool(ready), "Dependency graph contains a cycle")
        result.extend(ready)
        remaining.difference_update(ready)
    return result


def variable_inputs(archive, node):
    result = {}
    for name, binding in node.get("execution", {}).get("inputs", {}).items():
        if binding["kind"] == "variable":
            result[name] = archive["variables"][binding["variable_id"]]["value"]
        elif binding["kind"] == "template":
            result[name] = {key: archive["variables"][value]["value"]
                            for key, value in binding["variables"].items()}
    return result


def dirty_nodes(before, after):
    require_archive(before)
    require_archive(after)
    old = {node["id"]: node for node in before["graph"]["nodes"]}
    old_active = active_nodes(before)
    new = active_nodes(after)
    dirty = {key for key, node in new.items() if key not in old
             or old[key].get("execution") != node.get("execution")
             or variable_inputs(before, old[key]) != variable_inputs(after, node)
             or key not in old_active}
    previous = {(edge["source"], edge["target"], edge["kind"], edge.get("source_output"), edge.get("target_input"))
                for edge in before["graph"]["edges"]}
    current = {(edge["source"], edge["target"], edge["kind"], edge.get("source_output"), edge.get("target_input"))
               for edge in after["graph"]["edges"]}
    dirty.update(edge[1] for edge in previous ^ current if edge[1] in new)
    pending = list(dirty)
    edges = before["graph"]["edges"] + after["graph"]["edges"]
    while pending:
        source = pending.pop()
        children = {edge["target"] for edge in edges if edge["source"] == source and edge["target"] in new} - dirty
        dirty.update(children)
        pending.extend(children)
    result = [key for key in ordered_nodes(after) if key in dirty]
    return result


def fork_definition(archive, expected_revision, operations, revision_artifact):
    require_archive(archive)
    require(archive["graph"]["revision_id"] == expected_revision, "Workflow revision changed", "revision_conflict")
    result = copy.deepcopy(archive)
    if revision_artifact["id"] not in {item["id"] for item in result["artifacts"]}:
        result["artifacts"].append(revision_artifact)
    result.setdefault("history", []).append({"kind": "revision", "revision_id": expected_revision,
                                             "artifact_id": revision_artifact["id"]})
    result["variables"] = resolve_variable_operations(result["variables"], operations)
    result["variable_operations"] = copy.deepcopy(operations)
    branch_id = new_id()
    active = active_nodes(archive)
    parent = next((node["branch_id"] for node in active.values()), result["graph"]["branches"][0]["id"])
    result["graph"]["branches"].append({"id": branch_id, "parent_id": parent, "state": "active"})
    for node in result["graph"]["nodes"]:
        if node["id"] in active:
            node["branch_id"] = branch_id
    result["graph"]["parent_revision_id"] = expected_revision
    result["graph"]["revision_id"] = new_id()
    result["graph"]["golden_path"] = []
    result["metadata"]["updated_at"] = utc_now()
    require_archive(result)
    return result


def resolve_inputs(archive, node, outputs):
    result = {}
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    for name, binding in node["execution"]["inputs"].items():
        kind = binding["kind"]
        if kind == "literal":
            result[name] = copy.deepcopy(binding["value"])
        elif kind == "variable":
            result[name] = copy.deepcopy(archive["variables"][binding["variable_id"]]["value"])
        elif kind == "template":
            values = {key: archive["variables"][value]["value"] for key, value in binding["variables"].items()}
            result[name] = re.sub(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}",
                                  lambda match: values[match.group(1)], binding["template"])
        elif kind == "artifact":
            artifact = artifacts[binding["artifact_id"]]
            result[name] = {"artifact_id": artifact["id"], "sha256": artifact["sha256"],
                            "mime_type": artifact["mime_type"], "size": artifact.get("size")}
        else:
            require(binding["node_id"] in outputs and binding["output"] in outputs[binding["node_id"]],
                    "Upstream output is unavailable", "missing_output")
            result[name] = copy.deepcopy(outputs[binding["node_id"]][binding["output"]])
    return result


def golden_path(archive, targets=None):
    latest = {}
    revision = archive["graph"]["revision_id"]
    for run in archive["runs"]:
        if run["revision_id"] == revision:
            latest[run["node_id"]] = run
    successful = {node for node, run in latest.items() if run["state"] == "succeeded"}
    requested = sorted(successful) if targets is None else targets
    result = []
    for node in requested:
        if node in active_nodes(archive):
            closure = ordered_nodes(archive, [node])
            if set(closure) <= successful:
                result.extend(item for item in closure if item not in result)
    return result
