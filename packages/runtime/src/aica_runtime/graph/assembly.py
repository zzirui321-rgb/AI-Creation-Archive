import copy

from aica_schema.protocol import ProtocolError, loads
from pydantic import ValidationError

from ..evidence import redact
from .context import PARSER_VERSION, PROMPT_VERSION, issue, stable_id
from .contract import ModelProposal
from .module_content import content_catalog, fallback_content, recorded_prompt, restore_unassigned_pages
from .validation import ContractError, check, claims, pointer, unique, validate_document


def assemble_graph(content, context, graph_id, revision_id, branch_id, model):
    try:
        return _assemble(content, context, graph_id, revision_id, branch_id, model)
    except (ContractError, ValidationError, ProtocolError, KeyError, TypeError, ValueError) as error:
        details = [{"stage": "graph_validation", "reason": str(error) if isinstance(error, ContractError)
                    else type(error).__name__}]
        raise ProtocolError("Semantic proposal failed structure, source, attribution or dependency validation",
                            "invalid_semantic_graph", details) from error


def _assemble(content, context, graph_id, revision_id, branch_id, model):
    proposal = ModelProposal.model_validate(redact(content))
    unique([group.key for group in proposal.groups], "Duplicate proposal group key")
    archive, prepared = context.archive, context.prepared
    selected = set(prepared["source_node_ids"])
    raw_nodes = {node["id"]: node for node in archive["graph"]["nodes"]}
    material_evidence = {fact["id"]: fact["artifact_id"] for fact in prepared["facts"] if fact.get("artifact_id")}
    artifacts_by_id = {item["id"]: item for item in archive["artifacts"]}
    catalog = context.module_catalog or content_catalog(prepared["facts"], archive)
    prompt_sources = {item["id"]: item for item in catalog["prompts"]}
    page_sources = {item["id"]: item for item in catalog["pages"]}
    page_owners = {}
    for group in proposal.groups:
        for page in group.pages:
            check(page.source_page_id not in page_owners, "Page has two owning modules")
            page_owners[page.source_page_id] = group.key
    assigned_pages = set()
    if material_evidence and not selected:
        check(bool(proposal.groups), "Artifact-only input cannot produce a silently empty graph")
    keys, owners, nodes = {}, {}, []
    for group in proposal.groups:
        check(group.source_node_ids or group.evidence_ids, "Empty group has no anchor")
        check(set(group.source_node_ids) <= selected, "Group is outside selected scope")
        identity = [sorted(group.source_node_ids), group.kind] if group.source_node_ids else [
            sorted(group.evidence_ids), group.kind, group.key]
        step_id = stable_id("step", identity)
        keys[group.key] = step_id
        for node_id in group.source_node_ids:
            check(node_id not in owners, "Source has two owners")
            owners[node_id] = step_id
        evidence_ids = list(dict.fromkeys([*group.evidence_ids,
                                          *(context.node_evidence[key] for key in group.source_node_ids)]))
        if not group.source_node_ids:
            message_anchor = group.kind in {"intent", "decision"} and any(
                fact["id"] in evidence_ids and fact.get("event", {}).get("type") == "message.recorded" for fact in prepared["facts"])
            material_anchor = any(key in material_evidence and artifacts_by_id[material_evidence[key]]["producer_node_id"] is None
                                  for key in evidence_ids)
            check(message_anchor or material_anchor, "Call-free group needs real message or material evidence")
        associated = [material_evidence[key] for key in evidence_ids if key in material_evidence
                      and artifacts_by_id[material_evidence[key]]["producer_node_id"] is None]
        nodes.append({"id": step_id, "kind": group.kind, "label": group.label,
                      "purpose": group.purpose.model_dump(), "requirements": [item.model_dump() for item in group.requirements],
                      "source_node_ids": group.source_node_ids,
                      "run_ids": [run["id"] for run in archive["runs"] if run["node_id"] in group.source_node_ids
                                  and run["revision_id"] == prepared["source"]["archive_revision_id"]],
                      "artifact_ids": list(dict.fromkeys([item["id"] for item in archive["artifacts"]
                          if item["producer_node_id"] in group.source_node_ids] + associated)),
                      "evidence_ids": evidence_ids})
        node = nodes[-1]
        fallback = fallback_content(node, catalog)
        check(set(group.prompt_ids) <= prompt_sources.keys(), "Unknown original prompt reference")
        explicit = [recorded_prompt(prompt_sources[key]) for key in group.prompt_ids]
        node["prompts"] = list({(item["text"], tuple(item["evidence_ids"])): item
                                for item in [*fallback["prompts"], *explicit]}.values())
        node["prompts"] += [item.model_dump() for item in group.prompts]
        page_map = {item["id"]: item for item in fallback["pages"] if item["id"] not in assigned_pages
                    and page_owners.get(item["id"], group.key) == group.key}
        for item in group.pages:
            check(item.source_page_id in page_sources, "Unknown original page reference")
            original = page_sources[item.source_page_id]
            page_map[item.source_page_id] = {
                "id": original["id"], "label": original["label"], "page_number": original["page_number"],
                "summary": {"text": item.summary, "origin": "llm", "review": "candidate", "attributed_to": "analyst",
                            "actor_id": None, "speech_act": "interpretation", "evidence_ids": [original["evidence_id"]]},
                "prompts": [recorded_prompt(prompt_sources[key]) for key in original["prompt_ids"]]
                           + [{**prompt.model_dump(), "evidence_ids": [original["evidence_id"]]} for prompt in item.prompts],
                "artifact_ids": original["artifact_ids"], "evidence_ids": [original["evidence_id"]]}
        node["pages"] = sorted(page_map.values(), key=lambda page: (page["page_number"] or 0, page["id"]))
        assigned_pages.update(page_map)
        for page in node["pages"]:
            node["artifact_ids"] += [key for key in page["artifact_ids"] if key not in node["artifact_ids"]]
        cited = [key for prompt in node["prompts"] for key in prompt["evidence_ids"]]
        cited += [key for page in node["pages"] for key in page["evidence_ids"]]
        cited += [key for page in node["pages"] for prompt in page["prompts"] for key in prompt["evidence_ids"]]
        cited += [key for key, artifact_id in material_evidence.items() if artifact_id in node["artifact_ids"]]
        node["evidence_ids"] = list(dict.fromkeys([*node["evidence_ids"], *cited]))
    restored_pages = restore_unassigned_pages(nodes, catalog, material_evidence)
    edges, verified_pairs = [], set()
    for raw in archive["graph"]["edges"]:
        source, target = owners.get(raw["source"]), owners.get(raw["target"])
        if source is None or target is None or source == target:
            continue
        edge = {"id": stable_id("edge", raw["id"]), "kind": raw["kind"], "source": source, "target": target,
                "state": "verified", "archive_edge_id": raw["id"],
                "explanation": {"text": "A 原始定义中的显式依赖。", "attributed_to": "analyst", "actor_id": None,
                                "speech_act": "observation", "evidence_ids": [context.node_evidence[raw["source"]],
                                                                                context.node_evidence[raw["target"]]],
                                "origin": "rule", "review": "accepted"}}
        if raw["kind"] == "data":
            edge["binding"] = {"source_node_id": raw["source"], "source_output": raw["source_output"],
                               "target_node_id": raw["target"], "target_input": raw["target_input"]}
        edges.append(edge)
        verified_pairs.add((raw["kind"], source, target))
    for index, item in enumerate(proposal.edges):
        check(item.source_key in keys and item.target_key in keys, "Unknown proposal edge group")
        source, target = keys[item.source_key], keys[item.target_key]
        if (item.kind, source, target) in verified_pairs:
            continue
        edge = {"id": stable_id("candidate", [index, source, target, item.kind]), "kind": item.kind,
                "source": source, "target": target, "explanation": item.explanation.model_dump()}
        if item.kind == "semantic":
            edge["relation"] = item.relation
        else:
            edge.update(state="candidate", archive_edge_id=None)
            if item.kind == "data":
                edge["binding"] = None
        edges.append(edge)
    variables = []
    for item in proposal.variables:
        check(item.group_key in keys, "Unknown variable group")
        location = item.locator
        check(owners.get(location.source_node_id) == keys[item.group_key], "Variable source is outside group")
        check(location.evidence_id == context.node_evidence[location.source_node_id], "Variable cites wrong source")
        value = pointer(raw_nodes[location.source_node_id], location.pointer)
        safe_value = pointer(redact(raw_nodes[location.source_node_id]), location.pointer)
        if location.decode == "json_string":
            check(isinstance(value, str), "JSON decoder requires a string")
            value = loads(value)
            check(isinstance(safe_value, str), "Redacted input cannot become a variable")
            safe_value = loads(safe_value)
        value = pointer(value, location.value_pointer)
        safe_value = pointer(safe_value, location.value_pointer)
        check(safe_value == value and "[REDACTED" not in str(value),
              "Sensitive values cannot become variables")
        kinds = {str: "string", list: "array", bool: "boolean", int: "integer", float: "number", dict: "object"}
        check(type(value) in kinds, "Unsupported variable type")
        variables.append({"id": stable_id("var", [keys[item.group_key], location.model_dump()]),
                          "step_id": keys[item.group_key], "label": item.label, "description": item.description.model_dump(),
                          "locator": location.model_dump(), "value_type": kinds[type(value)], "current_value": value,
                          "allowed_modes": ["keep", "override"], "separator": None, "review": "candidate"})
    coverage = copy.deepcopy(prepared["coverage"])
    if restored_pages:
        coverage["issues"].append(issue("SOURCE_PAGES_RESTORED",
            f"{restored_pages} 条未归入AI模块的分页记录由规则保留在源内容模块；不代表AI提炼或实际执行。"))
    if any(artifacts_by_id[key]["producer_node_id"] is None for node in nodes for key in node["artifact_ids"]) and not any(
            entry["code"] == "ARTIFACT_ONLY_SOURCE" for entry in coverage["issues"]):
        coverage["issues"].append(issue("ARTIFACT_ONLY_SOURCE", "部分步骤由未绑定原始调用的附件推断，实际执行来源缺失。"))
    coverage["unassigned_source_node_ids"] = sorted(selected - owners.keys())
    for item in proposal.unresolved:
        value = item.model_dump()
        check(set(value["node_ids"]) <= keys.keys(), "Unknown issue group")
        value["id"] = stable_id("model-issue", value["id"])
        value["node_ids"] = [keys[key] for key in value["node_ids"]]
        coverage["issues"].append(value)
    if coverage["unassigned_source_node_ids"]:
        coverage["issues"].append(issue("UNASSIGNED_SOURCE", "选定原始节点尚未全部归组。"))
    associated = {key for node in nodes for key in node["artifact_ids"]}
    orphan_materials = {fact["artifact_id"] for fact in prepared["facts"] if fact.get("artifact_id")
                        and artifacts_by_id[fact["artifact_id"]]["producer_node_id"] is None}
    missing = orphan_materials - associated
    if missing:
        entry = issue("UNASSIGNED_ARTIFACT", f"{len(missing)} 个无执行归属的附件尚未关联步骤；原始材料仍保留。")
        entry["evidence_ids"] = [key for key, artifact_id in material_evidence.items() if artifact_id in missing]
        coverage["issues"].append(entry)
    graph = {"format": "aica-semantic-graph/0.1", "graph_id": graph_id, "revision_id": revision_id,
             "parent_revision_id": None, "repository_branch_id": branch_id, "state": "draft", "executable": False,
             "source": prepared["source"], "parse": {"parser_version": PARSER_VERSION, "prompt_version": PROMPT_VERSION,
                 "model": model, "mode": "rules_llm", "input_sha256": prepared["input_sha256"]},
             "nodes": nodes, "edges": edges, "variables": variables, "evidence": context.evidence, "coverage": coverage}
    result = validate_document(graph, context.archive_bytes, context.history)
    for claim in claims(result):
        check(claim.origin != "human", "LLM cannot impersonate human review")
    return result.model_dump()
