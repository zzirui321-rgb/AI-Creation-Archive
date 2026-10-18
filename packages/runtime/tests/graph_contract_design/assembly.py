import copy

from aica_schema.protocol import dumps, loads

from .contract import ModelProposal
from .validation import check, claims, pointer, resolve_evidence, sha256, unique, validate_document


def stable_id(prefix, value):
    return prefix + "-" + sha256(dumps(value).encode())[:24]


def prepare_context(case):
    graph = validate_document(case["graph"], case["archive_bytes"], case["history"])
    resolved = resolve_evidence(graph, case["archive"], case["archive_bytes"], case["history"])
    facts = [{"id": key, **value} for key, value in resolved.items()]
    context = {"source_node_ids": graph.coverage.selected_source_node_ids, "facts": facts,
               "declared_dependencies": case["archive"]["graph"]["edges"], "runs": case["archive"]["runs"],
               "coverage": graph.coverage.model_dump()}
    check(len(dumps(context).encode()) <= 128 * 1024, "Design context exceeds 128 KiB")
    return context


def assemble(proposal_content, case, model=None):
    proposal = ModelProposal.model_validate(proposal_content)
    unique([group.key for group in proposal.groups], "Duplicate proposal group key")
    source = case["archive"]
    raw_nodes = {node["id"]: node for node in source["graph"]["nodes"]}
    keys, owners, nodes = {}, {}, []
    for group in proposal.groups:
        check(group.source_node_ids or group.evidence_ids, "Empty group has no source anchor")
        step_id = stable_id("step", [sorted(group.source_node_ids) or sorted(group.evidence_ids), group.kind])
        keys[group.key] = step_id
        for node_id in group.source_node_ids:
            check(node_id in raw_nodes and node_id not in owners, "Unknown or multiply owned proposal source")
            owners[node_id] = step_id
        nodes.append({"id": step_id, "kind": group.kind, "label": group.label,
                      "purpose": group.purpose.model_dump(), "requirements": [item.model_dump() for item in group.requirements],
                      "source_node_ids": group.source_node_ids,
                      "run_ids": [run["id"] for run in source["runs"] if run["node_id"] in group.source_node_ids
                                  and run["revision_id"] == source["graph"]["revision_id"]],
                      "artifact_ids": [item["id"] for item in source["artifacts"] if item["producer_node_id"] in group.source_node_ids],
                      "evidence_ids": group.evidence_ids})
    node_evidence = {}
    for item in case["graph"]["evidence"]:
        if item["kind"] == "archive":
            value = pointer(source, item["pointer"])
            if isinstance(value, dict) and value.get("id") in raw_nodes:
                node_evidence[value["id"]] = item["id"]
    edges, verified_pairs = [], set()
    for raw in source["graph"]["edges"]:
        source_step, target_step = owners.get(raw["source"]), owners.get(raw["target"])
        if source_step is None or target_step is None or source_step == target_step:
            continue
        edge = {"id": stable_id("edge", raw["id"]), "kind": raw["kind"], "source": source_step,
                "target": target_step, "state": "verified", "archive_edge_id": raw["id"],
                "explanation": {"text": "A 源定义中的显式依赖。", "attributed_to": "analyst", "actor_id": None,
                                "speech_act": "observation", "evidence_ids": [node_evidence[raw["source"]], node_evidence[raw["target"]]],
                                "origin": "rule", "review": "accepted"}}
        if raw["kind"] == "data":
            edge["binding"] = {"source_node_id": raw["source"], "source_output": raw["source_output"],
                               "target_node_id": raw["target"], "target_input": raw["target_input"]}
        edges.append(edge)
        verified_pairs.add((raw["kind"], source_step, target_step))
    for index, item in enumerate(proposal.edges):
        check(item.source_key in keys and item.target_key in keys, "Proposal edge uses an unknown group")
        source_step, target_step = keys[item.source_key], keys[item.target_key]
        if (item.kind, source_step, target_step) in verified_pairs:
            continue
        edge = {"id": stable_id("candidate", [index, source_step, target_step, item.kind]), "kind": item.kind,
                "source": source_step, "target": target_step, "explanation": item.explanation.model_dump()}
        if item.kind == "semantic":
            edge["relation"] = item.relation
        else:
            edge.update(state="candidate", archive_edge_id=None)
            if item.kind == "data":
                edge["binding"] = None
        edges.append(edge)
    variables = []
    for item in proposal.variables:
        check(item.group_key in keys, "Variable uses an unknown group")
        location = item.locator
        check(location.source_node_id in raw_nodes, "Variable uses an unknown source")
        value = pointer(raw_nodes[location.source_node_id], location.pointer)
        if location.decode == "json_string":
            check(isinstance(value, str), "JSON decoder requires a string")
            value = loads(value)
        value = pointer(value, location.value_pointer)
        kinds = {str: "string", list: "array", bool: "boolean", int: "integer", float: "number", dict: "object"}
        check(type(value) in kinds, "Unsupported variable type")
        variables.append({"id": stable_id("var", [keys[item.group_key], location.model_dump()]),
                          "step_id": keys[item.group_key], "label": item.label, "description": item.description.model_dump(),
                          "locator": location.model_dump(), "value_type": kinds[type(value)], "current_value": value,
                          "allowed_modes": ["keep", "override"], "separator": None, "review": "candidate"})
    graph = copy.deepcopy(case["graph"])
    graph.update(nodes=nodes, edges=edges, variables=variables)
    graph["parse"].update(mode="rules_llm", model=model, input_sha256=sha256(dumps(prepare_context(case)).encode()))
    graph["coverage"]["unassigned_source_node_ids"] = sorted(set(raw_nodes) - owners.keys())
    issues = [item.model_dump() for item in proposal.unresolved]
    for issue in issues:
        issue["node_ids"] = [keys.get(identifier, identifier) for identifier in issue["node_ids"]]
    graph["coverage"]["issues"].extend(issues)
    historical_count = sum(run["revision_id"] != source["graph"]["revision_id"] for run in source["runs"])
    if historical_count and not any(item["code"] == "HISTORICAL_RUN_SCOPE" for item in graph["coverage"]["issues"]):
        graph["coverage"]["issues"].append({"id": "issue-historical-scope", "code": "HISTORICAL_RUN_SCOPE", "severity": "warning",
                                              "message": f"{historical_count} 次执行属于其他定义修订，未归入本图；原始档案仍保留。",
                                              "node_ids": [], "evidence_ids": []})
    if graph["coverage"]["unassigned_source_node_ids"]:
        graph["coverage"]["issues"].append({"id": "issue-unassigned", "code": "UNASSIGNED_SOURCE", "severity": "warning",
                                              "message": "部分原始节点尚未归入步骤。", "node_ids": [], "evidence_ids": []})
    result = validate_document(graph, case["archive_bytes"], case["history"])
    for item in claims(result):
        check(item.origin != "human", "Model proposal cannot impersonate human review")
    return result.model_dump()
