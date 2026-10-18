import re

ROLE_TERMS = {
    "creative_intent": r"创作意图|创作目标|创作需求|目标受众|creative[_ ]?intent|original[_ ]?user[_ ]?prompt|\bbrief\b",
    "character_identity": r"人物形象|人物设定|角色设定|角色一致|主角|\bcharacters?\b|character[_ ]?(?:design|sheet|identity)",
    "visual_style": r"视觉风格|画风|风格|配色|色板|visual[_ ]?style|art[_ ]?style|\bpalette\b",
    "page_details": r"分镜|台词|场景|\bpages?\b|\bpanels?\b|\bdialogues?\b|\bscenes?\b|storyboard",
}


def reuse_hints(facts):
    candidates = []
    for role, pattern in ROLE_TERMS.items():
        evidence, matches, locations = [], 0, []

        def visit(value, path, identifier, depth=0):
            nonlocal matches
            if depth > 12:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(child, path + "/" + key.replace("~", "~0").replace("/", "~1"), identifier, depth + 1)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, path + "/" + str(index), identifier, depth + 1)
            if re.search(pattern, path + (" " + value if isinstance(value, str) else ""), re.I):
                matches += 1
                if identifier not in evidence:
                    evidence.append(identifier)
                if len(locations) < 6:
                    locations.append({"evidence_id": identifier, "fact_pointer": path})

        for fact in facts:
            if fact.get("content_state") == "available":
                visit(fact["content"], "/content", fact["id"])
            elif not fact.get("artifact_id"):
                visit(fact.get("value"), "/value", fact["id"])
        if evidence:
            candidates.append({"role": role, "evidence_ids": evidence[:24], "match_count": matches,
                               "example_locations": locations})
    return {"policy": "reuse-first/0.1", "status": "heuristic_candidates_not_confirmed_semantics", "candidates": candidates}
