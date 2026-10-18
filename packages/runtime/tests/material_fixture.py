import hashlib

from aica_schema.protocol import dumps, empty_archive
from graph_parser_fixture import claim


def write_material_source(root):
    root.mkdir()
    archive = empty_archive("Synthetic artifact-only creative project")
    contents = {"story.json": dumps({"title": "Test lighthouse story", "pages": [{"number": 1, "scene": "Harbor at night"}]}).encode(),
                "prompts.json": dumps([{"asset": "cover", "prompt": "Lighthouse above the sea"}]).encode(),
                "cover.png": b"synthetic-png-bytes-not-an-image",
                "user-prompt.txt": "这是事后登记的需求材料，不是有角色的消息事件。".encode()}
    for name, raw in contents.items():
        (root / name).write_bytes(raw)
        archive["artifacts"].append({"id": name.replace(".", "-"), "uri": name,
            "mime_type": "application/json" if name.endswith(".json") else "text/plain" if name.endswith(".txt") else "image/png",
            "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "availability": "available",
            "producer_node_id": None, "producer_run_id": None})
    (root / "aica.json").write_bytes(dumps(archive).encode())
    return archive


def material_proposal(prepared):
    materials = [fact for fact in prepared["facts"] if fact.get("artifact_id")]
    groups = [{"key": "material-step-" + str(index), "kind": "generation", "label": "材料支持的候选创作步骤",
               "purpose": claim("依据登记材料推断步骤，未记录实际工具执行。", [fact["id"]]), "requirements": [],
               "source_node_ids": [], "evidence_ids": [fact["id"]]} for index, fact in enumerate(materials)]
    return {"groups": groups, "edges": [], "variables": [], "unresolved": []}
