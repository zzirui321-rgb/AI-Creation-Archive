from aica_schema.protocol import dumps


def proposal_for(prepared, merge_retry=True):
    node_facts = [fact for fact in prepared["facts"]
                  if isinstance(fact["value"], dict) and fact["value"].get("id") in prepared["source_node_ids"]
                  and "branch_id" in fact["value"]]
    groups, node_groups = {}, {}
    for fact in node_facts:
        node = fact["value"]
        key = "character-group" if merge_retry and node["id"] in {"raw-character-first", "raw-character-revised"} else node["id"]
        node_groups[node["id"]] = key
        if key not in groups:
            groups[key] = {"key": key, "kind": "generation" if node.get("execution") else "intent",
                           "label": node["label"], "purpose": claim("合成测试中的步骤解释。", [fact["id"]]),
                           "requirements": [], "source_node_ids": [], "evidence_ids": []}
        groups[key]["source_node_ids"].append(node["id"])
        groups[key]["evidence_ids"].append(fact["id"])
    variables = [{"group_key": node_groups[location["source_node_id"]], "label": "输入参数",
                  "description": claim("可审核的合成参数建议。", [location["evidence_id"]]), "locator": location}
                 for location in prepared["parameter_locations"]]
    if not groups:
        message = next((fact for fact in prepared["facts"] if fact["role"] == "user"), None)
        if message:
            groups["message-intent"] = {"key": "message-intent", "kind": "intent", "label": "创作目标",
                "purpose": claim("用户给出创作要求。", [message["id"]], "user"), "requirements": [],
                "source_node_ids": [], "evidence_ids": [message["id"]]}
    return {"groups": list(groups.values()), "edges": [], "variables": variables, "unresolved": []}


def claim(text, evidence_ids, role="analyst", actor_id=None):
    return {"text": text, "attributed_to": role, "actor_id": actor_id, "speech_act": "interpretation",
            "evidence_ids": evidence_ids, "origin": "llm", "review": "candidate"}


def model_reply(proposal):
    return {"choices": [{"finish_reason": "stop", "message": {"content": dumps(proposal)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180}}
