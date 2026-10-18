import base64
import binascii
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from importlib.resources import files
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

EMBEDDED_EXTENSION = "aica.embedded_artifacts"
EMBEDDED_ARTIFACT_LIMIT = 64 * 1024 * 1024
PORTABLE_JSON_LIMIT = 128 * 1024 * 1024


class ProtocolError(ValueError):
    def __init__(self, message, code="invalid_contract", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or []


def new_id():
    value = str(uuid4())
    return value


def utc_now():
    value = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def reject_constant(value):
    raise ProtocolError(f"Non-finite JSON number: {value}")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(text):
    try:
        value = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError(f"Invalid JSON: {error}") from error
    require_json(value)
    return value


def dumps(value, pretty=False):
    require_json(value)
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          indent=2 if pretty else None, separators=None if pretty else (",", ":"))
        text.encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as error:
        raise ProtocolError(f"Value is not portable JSON: {error}") from error
    return text


def require_json(value, depth=0):
    if depth > 64:
        raise ProtocolError("JSON nesting exceeds 64 levels")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            require_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            require_json(item, depth + 1)
    elif type(value) is int:
        if abs(value) > 9007199254740991:
            raise ProtocolError("Integers outside the interoperable JSON range must be encoded as strings")
    elif type(value) is float:
        if not math.isfinite(value):
            raise ProtocolError("Non-finite JSON numbers are not allowed")
    elif value is not None and not isinstance(value, (str, bool)):
        raise ProtocolError("Only JSON values are allowed")


def require_local_schema(schema):
    if isinstance(schema, dict):
        require("$ref" not in schema and "$dynamicRef" not in schema,
                "Variable item schemas must be self-contained without references")
        for value in schema.values():
            require_local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            require_local_schema(value)


def digest(value):
    content = dumps(value).encode("utf-8")
    value = hashlib.sha256(content).hexdigest()
    return value


def archive_schema():
    schema = loads(files("aica_schema").joinpath("aica.schema.json").read_text(encoding="utf-8"))
    return schema


def definition_schema(name):
    schema = archive_schema()
    result = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"}
    return result


@lru_cache(maxsize=32)
def validator_for(definition=None):
    schema = definition_schema(definition) if definition else archive_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return validator


def shape_errors(value, definition=None):
    validator = validator_for(definition)
    errors = [{"path": "/" + "/".join(str(part) for part in error.absolute_path), "message": error.message}
              for error in validator.iter_errors(value)]
    return errors


def require_shape(value, definition):
    dumps(value)
    errors = shape_errors(value, definition)
    if errors:
        raise ProtocolError(f"Invalid {definition}", details=errors)


def require(condition, message, code="invalid_contract"):
    if not condition:
        raise ProtocolError(message, code)


def index_items(items, name):
    result = {}
    for item in items:
        require(item["id"] not in result, f"Duplicate {name} ID: {item['id']}")
        result[item["id"]] = item
    return result


def require_dag(vertices, edges):
    outgoing = {vertex: [] for vertex in vertices}
    indegree = dict.fromkeys(vertices, 0)
    for source, target in edges:
        require(source in outgoing and target in outgoing, f"Unknown dependency: {source} -> {target}")
        outgoing[source].append(target)
        indegree[target] += 1
    ready = [vertex for vertex, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        vertex = ready.pop()
        visited += 1
        for target in outgoing[vertex]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    require(visited == len(vertices), "Dependency graph contains a cycle")


def value_matches(value, kind):
    types = {"string": str, "array": list, "object": dict, "boolean": bool, "integer": int,
             "number": (int, float)}
    valid = isinstance(value, types[kind])
    if kind in ("number", "integer"):
        valid = valid and not isinstance(value, bool) and math.isfinite(value)
    return valid


def check_variable(variable, label):
    for field in ("default", "value"):
        require(value_matches(variable[field], variable["type"]), f"{label}.{field} has the wrong type")
        if variable["type"] == "array" and "items_schema" in variable:
            item_schema = variable["items_schema"]
            require_local_schema(item_schema)
            try:
                Draft202012Validator.check_schema(item_schema)
            except SchemaError as error:
                raise ProtocolError("Invalid variable items_schema") from error
            checker = Draft202012Validator(item_schema, format_checker=FormatChecker())
            require(all(checker.is_valid(item) for item in variable[field]), f"{label}.{field} has invalid items")
    require("append" not in variable["modes"] or variable["type"] in ("string", "array"),
            f"Append is unsupported for {label}")


def check_definition(archive):
    graph = archive["graph"]
    nodes = index_items(graph["nodes"], "node")
    edges = index_items(graph["edges"], "edge")
    branches = index_items(graph["branches"], "branch")
    artifacts = index_items(archive["artifacts"], "artifact")
    index_items(archive["interactive_questions"], "question")
    branch_edges = [(branch["parent_id"], branch["id"]) for branch in branches.values()
                    if branch["parent_id"] is not None]
    require_dag(branches, branch_edges)
    dependency_edges = [(edge["source"], edge["target"]) for edge in edges.values()]
    require_dag(nodes, dependency_edges)
    for node in nodes.values():
        require(node["branch_id"] in branches, f"Unknown branch for {node['id']}")
        execution = node.get("execution", {})
        for input_name, binding in execution.get("inputs", {}).items():
            kind = binding["kind"]
            if kind == "variable":
                require(binding["variable_id"] in archive["variables"], f"Unknown variable in {node['id']}")
            elif kind == "template":
                pattern = r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}"
                placeholders = re.findall(pattern, binding["template"])
                residue = re.sub(pattern, "", binding["template"])
                require("{{" not in residue and "}}" not in residue, "Unsupported template expression")
                require(set(placeholders) == set(binding["variables"]), "Template mapping disagrees with placeholders")
                for variable_id in binding["variables"].values():
                    variable = archive["variables"].get(variable_id)
                    require(variable is not None and variable["type"] == "string",
                            "Templates reference only declared string variables")
            elif kind == "artifact":
                require(binding["artifact_id"] in artifacts, f"Unknown artifact in {node['id']}")
            elif kind == "node_output":
                source_id = binding["node_id"]
                require(source_id in nodes, f"Unknown input node: {source_id}")
                require(binding["output"] in nodes[source_id].get("execution", {}).get("outputs", []),
                        f"Unknown output on {source_id}")
                matched = any(edge["kind"] == "data" and edge["source"] == source_id
                              and edge["target"] == node["id"] and edge.get("source_output") == binding["output"]
                              and edge.get("target_input") == input_name for edge in edges.values())
                require(matched, f"Missing data edge for {node['id']}.{input_name}")
        script_id = execution.get("script_artifact_id")
        require(script_id is None or script_id in artifacts, f"Unknown script artifact on {node['id']}")
        for evidence in node["provenance"]["evidence"]:
            require("artifact_id" not in evidence or evidence["artifact_id"] in artifacts,
                    f"Unknown evidence artifact on {node['id']}")
    for edge in edges.values():
        if edge["kind"] == "data":
            binding = nodes[edge["target"]].get("execution", {}).get("inputs", {}).get(edge.get("target_input"), {})
            require(binding.get("kind") == "node_output" and binding.get("node_id") == edge["source"]
                    and binding.get("output") == edge.get("source_output"), f"Invalid data edge: {edge['id']}")
    require(all(node_id in nodes for node_id in graph["golden_path"]), "Golden Path references unknown nodes")
    for variable_id, variable in archive["variables"].items():
        require_shape(variable_id, "id")
        check_variable(variable, variable_id)
        for binding in variable["bindings"]:
            node = nodes.get(binding["node_id"], {})
            source = node.get("execution", {}).get("inputs", {}).get(binding["input"], {})
            direct = source == {"kind": "variable", "variable_id": variable_id}
            templated = source.get("kind") == "template" and variable_id in source["variables"].values()
            require(direct or templated,
                    f"Variable binding disagrees with node input: {variable_id}")
    for question in archive["interactive_questions"]:
        variable = archive["variables"].get(question["variable_id"])
        require(variable is not None, f"Unknown question variable: {question['variable_id']}")
        require(set(question["modes"]) <= set(variable["modes"]), "Question modes exceed variable modes")


def require_definition_snapshot(definition, artifacts, revision_id):
    require_shape(definition, "definition_snapshot")
    require(definition["graph"]["revision_id"] == revision_id, "Historical definition has a different revision ID")
    check_definition({**definition, "artifacts": artifacts})


def semantic_check(archive):
    check_definition(archive)
    graph = archive["graph"]
    nodes = index_items(graph["nodes"], "node")
    artifacts = index_items(archive["artifacts"], "artifact")
    harnesses = index_items(archive["harnesses"], "harness")
    agents = index_items(archive["agents"], "agent")
    sessions = index_items(archive["sessions"], "session")
    runs = index_items(archive["runs"], "run")
    revisions = {}
    for item in archive.get("history", []):
        require(item["artifact_id"] in artifacts, "Unknown history artifact")
        if item["kind"] == "revision":
            require("revision_id" in item, "Revision history needs a revision ID")
            revision = item["revision_id"]
            require(revision != graph["revision_id"], "Current revision cannot also be historical")
            require(revision not in revisions, "Duplicate historical revision ID")
            revisions[revision] = item["artifact_id"]
            require(artifacts[item["artifact_id"]]["sha256"] is not None, "Historical revision needs a content hash")
    revision_ids = {graph["revision_id"]} | revisions.keys()
    parent_revision = graph.get("parent_revision_id")
    require(parent_revision is None or parent_revision in revisions, "Unknown parent workflow revision")
    for agent in agents.values():
        require(agent["harness_id"] in harnesses and agent["session_id"] in sessions, "Unknown agent context")
        require(sessions[agent["session_id"]]["consent"]["record"], "Agent belongs to a denied session")
        parent = agent["parent_agent_id"]
        if parent is not None:
            require(parent in agents and agents[parent]["session_id"] == agent["session_id"], "Invalid parent Agent")
            require(sessions[agent["session_id"]]["consent"]["include_children"], "Child Agent outside consent scope")
    require_dag(agents, [(agent["parent_agent_id"], agent["id"]) for agent in agents.values()
                         if agent["parent_agent_id"] is not None])
    for harness in harnesses.values():
        require(all(item in artifacts for item in harness.get("artifact_ids", [])), "Unknown Harness artifact")
    for run in runs.values():
        require(run["agent_id"] in agents and run["session_id"] in sessions, "Unknown execution actor")
        require(agents[run["agent_id"]]["session_id"] == run["session_id"], "Execution Session disagrees with Agent")
        require(run["harness_id"] in harnesses and run["revision_id"] in revision_ids, "Unknown execution revision")
        if run["revision_id"] == graph["revision_id"]:
            require(run["node_id"] in nodes, "Unknown execution node")
        require(all(item in artifacts for item in run["artifact_ids"]), "Unknown execution artifact")
        require(all(item in artifacts for item in run.get("cache", {}).get("artifact_ids", [])),
                "Unknown cached artifact")
        require(run["state"] not in ("succeeded", "failed", "cancelled") or "ended_at" in run,
                "Finished execution needs ended_at")
    for artifact in artifacts.values():
        producer = artifact["producer_run_id"]
        require(producer is None or producer in runs, "Unknown artifact producer Run")
        require(producer is None or artifact["producer_node_id"] == runs[producer]["node_id"],
                "Artifact producer node disagrees with Run")
        require(artifact["availability"] != "available" or artifact["sha256"] is not None,
                "Available artifact needs a content hash")
        require(not artifact.get("locations") or artifact["sha256"] is not None,
                "Alternative artifact locations need a content hash")
    require(archive["snapshot"]["complete"] == (not archive["snapshot"]["gaps"]),
            "Snapshot completeness disagrees with gaps")


def embedded_objects(archive, complete=False):
    extension = archive.get("extensions", {}).get(EMBEDDED_EXTENSION)
    objects = extension["objects"] if extension is not None else {}
    hashes = {artifact["sha256"] for artifact in archive["artifacts"]}
    require(set(objects) <= hashes, "Embedded object is not referenced by an artifact", "invalid_portable_json")
    if complete:
        require(None not in hashes and hashes <= set(objects),
                "JSON is missing artifact bytes; export a self-contained JSON with aica export --portable",
                "missing_embedded_artifacts")
    decoded = {}
    total = 0
    for sha256, value in objects.items():
        require(len(value) <= ((EMBEDDED_ARTIFACT_LIMIT + 2) // 3) * 4,
                "Embedded artifact exceeds 64 MiB", "transfer_limit")
        try:
            content = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ProtocolError("Embedded artifact is not valid Base64", "invalid_portable_json") from error
        total += len(content)
        require(len(content) <= EMBEDDED_ARTIFACT_LIMIT and total <= PORTABLE_JSON_LIMIT * 3 // 4,
                "Embedded artifacts exceed the JSON transfer limit", "transfer_limit")
        require(base64.b64encode(content).decode("ascii") == value and hashlib.sha256(content).hexdigest() == sha256,
                "Embedded artifact does not match its hash or canonical Base64", "invalid_portable_json")
        decoded[sha256] = content
    for artifact in archive["artifacts"]:
        if artifact["sha256"] in decoded:
            require("size" not in artifact or artifact["size"] == len(decoded[artifact["sha256"]]),
                    "Embedded artifact size differs from its record", "invalid_portable_json")
    return decoded


def validate_archive(archive):
    dumps(archive)
    errors = shape_errors(archive)
    if not errors:
        try:
            semantic_check(archive)
            embedded_objects(archive)
        except (ProtocolError, ValueError) as error:
            errors.append({"path": "/", "message": str(error)})
    return {"status": "success" if not errors else "error", "valid": not errors, "errors": errors}


def require_archive(archive):
    result = validate_archive(archive)
    if not result["valid"]:
        raise ProtocolError("Invalid AiCA archive", details=result["errors"])


def empty_archive(title="Untitled", project_id=None, stream_id=None):
    now = utc_now()
    return {
        "aica_version": "0.1",
        "metadata": {"project_id": project_id or new_id(), "title": title, "source_mode": "live_recorded",
                     "created_at": now, "updated_at": now, "external_links": []},
        "variables": {}, "interactive_questions": [], "variable_operations": [],
        "graph": {"revision_id": new_id(), "parent_revision_id": None, "nodes": [], "edges": [],
                  "branches": [{"id": "main", "parent_id": None, "state": "active"}], "golden_path": []},
        "artifacts": [], "harnesses": [], "agents": [], "sessions": [], "runs": [], "history": [],
        "snapshot": {"stream_id": stream_id or new_id(), "snapshot_id": new_id(), "commit_seq": 0,
                     "complete": True, "gaps": []}, "extensions": {},
    }


def resolve_variable_operations(variables, operations):
    result = copy.deepcopy(variables)
    for operation in operations:
        require_shape(operation, "operation")
        variable_id = operation["variable_id"]
        require(variable_id in result, f"Unknown variable: {variable_id}")
        variable = result[variable_id]
        mode = operation["mode"]
        require(mode in variable["modes"], f"Mode {mode} is disabled for {variable_id}")
        if mode == "override":
            variable["value"] = copy.deepcopy(operation["value"])
        elif mode == "append":
            require(variable["type"] in ("string", "array"), "Append supports only strings and arrays")
            require(value_matches(operation["value"], variable["type"]), "Append value has the wrong type")
            current = variable["value"]
            incoming = operation["value"]
            if variable["type"] == "string":
                separator = variable.get("separator", "\n") if current and incoming else ""
                variable["value"] = current + separator + incoming
            else:
                variable["value"] = current + incoming
        check_variable(variable, variable_id)
    return result
