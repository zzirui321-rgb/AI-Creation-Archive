import json
import re

from aica_schema.protocol import digest

from ..evidence import redact

PROMPT_KEY = re.compile(r"prompt|提示词", re.I)
PAGE_LABEL = re.compile(r"(?:第\s*(\d+)\s*页|\bpage[\s_-]*(\d+)\b)", re.I)


def identity(prefix, value):
    return prefix + "-" + digest(value)[:24]


def content_catalog(facts, archive):
    prompts, pages = [], []
    artifacts = {item["id"]: item for item in archive["artifacts"]}
    images = {key: value for key, value in artifacts.items() if value["mime_type"].startswith("image/")}
    references = {value: key for key, image in images.items() for value in (key, image["uri"])}

    def image_references(value, active=False, depth=0):
        if depth > 20:
            return []
        if isinstance(value, str):
            return [references[value]] if active and value in references else []
        if isinstance(value, list):
            return [key for item in value for key in image_references(item, active, depth + 1)]
        if isinstance(value, dict):
            return [key for name, item in value.items() for key in image_references(
                item, active or bool(re.search(r"image|artifact|output|file|uri|图片|图像", name, re.I)), depth + 1)]
        return []

    for fact in facts:
        root = fact.get("content") if fact.get("content_state") == "available" else fact.get("value")
        if root is None or isinstance(root, dict) and root.get("selection") == "partial_head_middle_tail_not_complete":
            continue
        prefix = "/content" if "content" in fact else "/value"

        def walk(value, path, parent_page=None, list_page=None, depth=0):
            if depth > 24:
                return
            if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
                try:
                    decoded = json.loads(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    walk(decoded, path + "/@json", parent_page, list_page, depth + 1)
            if isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, path + "/" + str(index), parent_page, index + 1 if list_page else None, depth + 1)
                return
            if not isinstance(value, dict):
                return
            page_number = list_page
            for key in ("page_number", "pageNumber", "page_no", "page_index", "page", "page_id", "pageId", "页码"):
                number = value.get(key)
                if isinstance(number, str):
                    match_number = re.fullmatch(r"(?:page[\s_-]*|p)?(\d+)", number, re.I)
                    number = int(match_number.group(1)) if match_number else None
                if type(number) is int and number >= (0 if key == "page_index" else 1):
                    page_number = number + (1 if key == "page_index" else 0)
                    break
            label = next((value[key] for key in ("title", "label", "name", "heading", "标题")
                          if isinstance(value.get(key), str)), "")
            match = PAGE_LABEL.search(label)
            if match:
                page_number = int(match.group(1) or match.group(2))
            page = parent_page
            if page_number and (parent_page is None or parent_page["page_number"] != page_number):
                page_id = identity("page-source", [fact["id"], path])
                linked = list(dict.fromkeys(image_references(value)))
                if path == prefix:
                    linked += [key for key, image in images.items() if image["producer_node_id"]
                               and image["producer_node_id"] in fact.get("node_ids", [])]
                description = next((value[key] for key in ("summary", "description", "scene", "content", "text", "内容")
                                    if isinstance(value.get(key), str) and value[key].strip()), label or f"第{page_number}页")
                page = {"id": page_id, "label": (label or f"第{page_number}页")[:200], "page_number": page_number,
                        "description": description[:2800], "evidence_id": fact["id"], "pointer": path,
                        "node_ids": fact.get("node_ids", []), "prompt_ids": [], "artifact_ids": list(dict.fromkeys(linked))}
                pages.append(page)
            for key, child in value.items():
                child_path = path + "/" + key.replace("~", "~0").replace("/", "~1")
                literal = isinstance(child, dict) and child.get("kind") == "literal"
                prompt = child.get("value") if literal else child
                prompt_path = child_path + "/value" if literal else child_path
                entries = [(prompt_path, prompt)] if isinstance(prompt, str) else []
                if isinstance(prompt, list):
                    entries = [(prompt_path + "/" + str(index), text) for index, text in enumerate(prompt)
                               if isinstance(text, str)]
                if isinstance(prompt, dict):
                    entries = [(prompt_path + "/" + name, text) for name, text in prompt.items()
                               if name in {"text", "content", "template", "positive", "negative"} and isinstance(text, str)]
                for location, text in entries if PROMPT_KEY.search(key) else []:
                    if not text.strip():
                        continue
                    item = {"id": identity("prompt-source", [fact["id"], location]), "text": redact(text),
                            "evidence_id": fact["id"], "pointer": location, "node_ids": fact.get("node_ids", []),
                            "page_id": page["id"] if page else None}
                    prompts.append(item)
                    if page:
                        page["prompt_ids"].append(item["id"])
                walk(child, child_path, page, key.lower() in {"pages", "页面", "分页"}, depth + 1)

        walk(root, prefix)
    return {"prompts": prompts, "pages": pages}


def recorded_prompt(item):
    return {"text": item["text"], "kind": "recorded", "evidence_ids": [item["evidence_id"]],
            "source_pointer": item["pointer"]}


def fallback_content(node, catalog):
    evidence = set(node["evidence_ids"])
    sources = set(node["source_node_ids"])
    prompts = [item for item in catalog["prompts"] if item["evidence_id"] in evidence
               or sources.intersection(item["node_ids"])]
    pages = [item for item in catalog["pages"] if sources.intersection(item["node_ids"])
             or node["kind"] in {"generation", "composition", "transform"} and item["evidence_id"] in evidence]
    return {"prompts": [recorded_prompt(item) for item in prompts if not item["page_id"]],
            "pages": [{"id": item["id"], "label": item["label"], "page_number": item["page_number"],
                       "summary": {"text": item["description"], "origin": "rule", "review": "candidate",
                                   "attributed_to": "analyst", "actor_id": None, "speech_act": "observation",
                                   "evidence_ids": [item["evidence_id"]]},
                       "prompts": [recorded_prompt(prompt) for prompt in prompts if prompt["id"] in item["prompt_ids"]],
                       "artifact_ids": item["artifact_ids"], "evidence_ids": [item["evidence_id"]]} for item in pages]}


def restore_unassigned_pages(nodes, catalog, material_evidence):
    assigned = {page["id"] for node in nodes for page in node.get("pages", [])}
    missing = [page for page in catalog["pages"] if page["id"] not in assigned]
    for evidence_id in dict.fromkeys(page["evidence_id"] for page in missing):
        pages = [page for page in missing if page["evidence_id"] == evidence_id]
        artifacts = list(dict.fromkeys([material_evidence[evidence_id]] if evidence_id in material_evidence else []))
        artifacts += [key for page in pages for key in page["artifact_ids"] if key not in artifacts]
        node = {"id": identity("step-source-pages", evidence_id), "kind": "generation", "label": "源档案分页内容",
                "purpose": {"text": "保留尚未归入AI模块的源档案分页内容，未推断执行过程。", "attributed_to": "analyst",
                            "actor_id": None, "speech_act": "observation", "evidence_ids": [evidence_id],
                            "origin": "rule", "review": "candidate"},
                "requirements": [], "source_node_ids": [], "run_ids": [], "artifact_ids": list(dict.fromkeys(artifacts)),
                "evidence_ids": [evidence_id], "prompts": [], "pages": []}
        restored = fallback_content(node, {"pages": pages, "prompts": catalog["prompts"]})
        node["pages"] = restored["pages"]
        node["evidence_ids"] += [key for key, artifact_id in material_evidence.items()
                                 if artifact_id in node["artifact_ids"] and key not in node["evidence_ids"]]
        nodes.append(node)
    return len(missing)
