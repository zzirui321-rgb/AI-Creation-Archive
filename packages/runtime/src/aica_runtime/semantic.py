import copy
import re

from aica_schema.protocol import ProtocolError, require, require_dag
from pydantic import ValidationError

from .contracts import SemanticDraft
from .evidence import redact

PROMPT_VERSION = "aica-semantics/0.1"


def pointer_value(value, pointer):
    require(pointer.startswith("/") and re.search(r"~(?![01])", pointer) is None,
            "Invalid variable value pointer", "invalid_analysis")
    try:
        for component in pointer[1:].split("/"):
            key = component.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                require(key.isascii() and key.isdecimal(), "Invalid list pointer", "invalid_analysis")
                value = value[int(key)]
            else:
                value = value[key]
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ProtocolError("Variable pointer does not resolve to evidence", "invalid_analysis") from error
    return copy.deepcopy(value)


def validate_analysis(content, prepared):
    try:
        draft = SemanticDraft.model_validate(redact(content))
    except ValidationError as error:
        raise ProtocolError("Model output does not match the semantic contract", "invalid_analysis") from error
    facts = {fact["id"]: fact for fact in prepared["facts"]}
    nodes = set(prepared["node_ids"])
    claims = [draft.intent, draft.golden_path, *draft.steps, *draft.dependencies, *draft.variables, *draft.questions]
    for claim in claims:
        require(set(claim.evidence_ids) <= facts.keys(), "Model cited unknown evidence", "invalid_analysis")
    require(len({step.node_id for step in draft.steps}) == len(draft.steps), "Duplicate semantic step", "invalid_analysis")

    def cited_nodes(claim):
        return {node for identifier in claim.evidence_ids for node in facts[identifier]["node_ids"]}

    for step in draft.steps:
        require(step.node_id in nodes and step.node_id in cited_nodes(step),
                "Step must cite its source node", "invalid_analysis")
    edges = [(edge["source"], edge["target"]) for edge in prepared["declared_dependencies"]]
    for edge in draft.dependencies:
        require({edge.source, edge.target} <= nodes and {edge.source, edge.target} <= cited_nodes(edge),
                "Dependency must cite both known nodes", "invalid_analysis")
        edges.append((edge.source, edge.target))
    try:
        require_dag(nodes, edges)
    except ProtocolError as error:
        raise ProtocolError("Suggested dependencies form a cycle", "invalid_analysis") from error
    values = []
    require(len({variable.name for variable in draft.variables}) == len(draft.variables),
            "Duplicate variable suggestion name", "invalid_analysis")
    for variable in draft.variables:
        require(variable.fact_id in variable.evidence_ids and variable.fact_id.startswith("node:")
                and variable.pointer.startswith(("/execution/inputs/", "/execution/params/")),
                "Variable must cite an actual input or parameter", "invalid_analysis")
        value = pointer_value(facts[variable.fact_id]["value"], variable.pointer)
        if variable.pointer.startswith("/execution/inputs/"):
            name = variable.pointer.split("/")[3].replace("~1", "/").replace("~0", "~")
            binding = facts[variable.fact_id]["value"]["execution"]["input_bindings"].get(name, {})
            require(binding.get("kind") in {"literal", "variable"},
                    "Unresolved input bindings cannot become editable values", "invalid_analysis")
        require("[REDACTED]" not in str(value), "Redacted values cannot become variables", "invalid_analysis")
        kind = ("boolean" if isinstance(value, bool) else "integer" if isinstance(value, int)
                else "number" if isinstance(value, float) else "string" if isinstance(value, str)
                else "array" if isinstance(value, list) else "object" if isinstance(value, dict) else None)
        require(kind is not None, "Unsupported variable value", "invalid_analysis")
        values.append({**variable.model_dump(), "value": value, "type": kind,
                       "allowed_modes": ["keep", "override", "append"] if kind in {"string", "array"}
                       else ["keep", "override"]})
    path = draft.golden_path.node_ids
    require(len(set(path)) == len(path) and set(path) <= set(prepared["latest_successful_node_ids"]),
            "Golden Path needs current successful execution evidence", "invalid_analysis")
    cited_successes = {node for identifier in draft.golden_path.evidence_ids
                       if identifier.startswith("run:") and facts[identifier]["value"].get("state") == "succeeded"
                       and facts[identifier]["value"].get("revision_id") == prepared["source"]["revision_id"]
                       for node in facts[identifier]["node_ids"]}
    require(set(path) <= cited_successes, "Golden Path must cite successful Run facts", "invalid_analysis")
    require(not set(path) & set(prepared["external_parent_nodes"]),
            "Golden Path has dependencies outside the analysis scope", "invalid_analysis")
    positions = {node: index for index, node in enumerate(path)}
    for source, target in edges:
        if target in positions:
            require(source in positions and positions[source] < positions[target],
                    "Golden Path is not dependency-closed and ordered", "invalid_analysis")
    result = draft.model_dump()
    result["variables"] = values
    for item in [result["intent"], result["golden_path"], *result["steps"], *result["dependencies"],
                 *result["variables"], *result["questions"]]:
        item["provenance"] = "inferred"
    return {"format": "aica-semantic-report/0.1", "source": prepared["source"],
            "input_sha256": prepared["input_sha256"], "state": "draft", "requires_review": True,
            "executable": False, "coverage": prepared["coverage"], "analysis": result,
            "evidence": prepared["facts"], "declared_dependencies": prepared["declared_dependencies"],
            "uncovered_node_ids": sorted(nodes - {step.node_id for step in draft.steps})}
