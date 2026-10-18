import json

from aica_runtime.graph.materials import TEXT_MIMES, read_material
from aica_runtime.graph.module_content import content_catalog, fallback_content
from aica_schema.protocol import ProtocolError, require

IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}


def module_content(service, commit, node_id):
    document = json.loads(commit["document"])
    node = next((node for node in document.get("nodes", []) if node["id"] == node_id), None)
    require(node is not None, "Semantic module not found", "not_found")
    result = {"prompts": node.get("prompts", []), "pages": node.get("pages", []), "artifacts": []}
    if not commit["project_id"]:
        result["notice"] = "此版本没有关联已上传的源档案，不能读取引用图片。"
        return result
    with service.store.connection() as db:
        project = db.execute("SELECT subject FROM projects WHERE id=?", (commit["project_id"],)).fetchone()
        require(project is not None, "Artifact source missing", "storage_corrupt")
        row = db.execute("SELECT input FROM semantic_graph_parses WHERE graph_id=? AND revision_id=?",
                         (document["graph_id"], document["revision_id"])).fetchone()
    snapshot = service.store.snapshot(project["subject"], commit["project_id"], commit["import_id"])
    facts = json.loads(row["input"])["facts"] if row else []
    for fact in facts:
        partial = fact.get("content_preview") or isinstance(fact.get("content"), dict) and fact["content"].get("selection") == "partial_head_middle_tail_not_complete"
        if fact.get("artifact_id") not in node["artifact_ids"] or fact.get("content_state") == "available" and not partial:
            continue
        artifact = next((item for item in snapshot["archive"]["artifacts"] if item["id"] == fact["artifact_id"]), None)
        if artifact and artifact["mime_type"].split(";")[0] in TEXT_MIMES:
            def reader(identifier, limit):
                _, path = service.store.artifact_in_snapshot(snapshot, identifier)
                with path.open("rb") as stream:
                    return stream.read(limit + 1)
            try:
                full = []
                content, _ = read_material(artifact, reader, 24 * 1024, on_material=full.append)
            except (OSError, ValueError, UnicodeError):
                continue
            fact.update(content=full[0] if full else content, content_state="available")
    fallback = fallback_content(node, content_catalog(facts, snapshot["archive"]))
    result["prompts"] = list({(item["kind"], item["text"]): item
                              for item in [*fallback["prompts"], *result["prompts"]]}.values())
    result["pages"] = sorted({item["id"]: item for item in [*fallback["pages"], *result["pages"]]}.values(),
                             key=lambda page: (page.get("page_number") or 0, page["id"]))
    identifiers = set(node["artifact_ids"]) | {key for page in result["pages"] for key in page["artifact_ids"]}
    for artifact in snapshot["archive"]["artifacts"]:
        if artifact["id"] not in identifiers or artifact["mime_type"] not in IMAGE_MIMES:
            continue
        try:
            service.store.artifact_in_snapshot(snapshot, artifact["id"])
        except (OSError, ProtocolError):
            available = False
        else:
            available = True
        result["artifacts"].append({"id": artifact["id"], "mime": artifact["mime_type"],
                                    "label": artifact["uri"], "available": available})
    return result
