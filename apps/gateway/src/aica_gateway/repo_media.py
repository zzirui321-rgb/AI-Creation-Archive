import asyncio
import hashlib
import io
import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID

from aica_schema.protocol import digest, new_id, require
from fastapi import Depends, Query, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageDraw
from preview import ArtifactStore
from preview.contracts import ExecutionContext, ExecutionRequest, PreviewError, StageProfile
from preview.providers.live_api import LiveAPIProvider
from preview.request_config import ImageConfig
from preview.scene_layers import decode, png, prepare_image_output
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .platform import Commit, validate_document


class EditImage(BaseModel):
    branch: str = Field(default="main", max_length=80)
    expected_head: UUID
    request_id: UUID
    original_artifact_id: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=4000)
    region: list[float] | None = Field(default=None, min_length=4, max_length=4)
    reference_artifact_id: str | None = Field(default=None, max_length=200)
    image_config: ImageConfig


def source_snapshot(service, commit):
    require(commit["project_id"] is not None, "此版本没有上传的源图片", "not_found")
    with service.store.connection() as db:
        project = db.execute("SELECT subject FROM projects WHERE id=?", (commit["project_id"],)).fetchone()
    require(project is not None, "Artifact source missing", "not_found")
    return service.store.media_snapshot(project["subject"], commit["project_id"], commit["import_id"])


def edit_store(service, repo_id):
    return ArtifactStore(service.store.directory / "repository-previews" / repo_id)


def generated_artifact(service, repo_id, document, artifact_id):
    document = document.get("source_semantic_graph") or document
    edits = [edit for node in document.get("nodes", []) for edit in node.get("image_edits", [])]
    require(any(edit["result_artifact_id"] == artifact_id for edit in edits), "Artifact not in this version", "not_found")
    with service.store.connection() as db:
        row = db.execute("SELECT artifact FROM repository_image_edits WHERE repo_id=? AND artifact_id=?", (repo_id, artifact_id)).fetchone()
        if not row:
            row = db.execute("SELECT artifact FROM repository_image_edits WHERE artifact_id=? AND repo_id IN "
                             "(WITH RECURSIVE parents(id,parent_repo) AS (SELECT id,parent_repo FROM repositories WHERE id=? "
                             "UNION ALL SELECT r.id,r.parent_repo FROM repositories r JOIN parents p ON r.id=p.parent_repo) SELECT id FROM parents)",
                             (artifact_id, repo_id)).fetchone()
    require(row is not None, "Generated artifact not found", "not_found")
    return json.loads(row["artifact"])


def media_path(service, repo_id, commit, artifact_id):
    document = json.loads(commit["document"])
    document = document.get("source_semantic_graph") or document
    if any(edit["result_artifact_id"] == artifact_id for node in document.get("nodes", []) for edit in node.get("image_edits", [])):
        artifact = generated_artifact(service, repo_id, document, artifact_id)
        parsed = urlparse(artifact["uri"])
        path = Path(unquote(parsed.path).lstrip("/") if os.name == "nt" else unquote(parsed.path)).resolve()
        require(path.is_relative_to((service.store.directory / "repository-previews").resolve()), "Invalid artifact location", "not_found")
        return artifact, path
    return service.store.artifact_in_snapshot(source_snapshot(service, commit), artifact_id)


def media_response(record, path, thumbnail):
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"], "Image unavailable", "not_found")
    require(record["mime_type"] in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}, "Unsupported image", "not_found")
    headers = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff", "X-AiCA-SHA256": record["sha256"]}
    if thumbnail:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((480, 480))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82)
        return Response(output.getvalue(), media_type="image/jpeg", headers=headers)
    return FileResponse(path, media_type=record["mime_type"], headers=headers)


def image_aliases(artifacts):
    producers = {}
    for item in artifacts:
        if item.get("producer_node_id") and item.get("sha256"):
            producers.setdefault(item["sha256"], set()).add(item["id"])
    return {item["id"]: next(iter(producers[item["sha256"]])) for item in artifacts
            if not item.get("producer_node_id") and len(producers.get(item.get("sha256"), ())) == 1}


def effective_images(document, artifacts=()):
    document = document.get("source_semantic_graph") or document
    aliases = image_aliases(artifacts)
    replacements = {}
    for node in document.get("nodes", []):
        for edit in node.get("image_edits", []):
            original = edit["original_artifact_id"]
            replacements[aliases.get(original, original)] = edit["result_artifact_id"]
    def current(identifier):
        seen = set()
        while identifier in replacements and identifier not in seen:
            seen.add(identifier)
            identifier = replacements[identifier]
        return identifier
    resolved = {identifier: current(identifier) for identifier in replacements}
    for alias, identifier in aliases.items():
        if identifier in resolved:
            resolved[alias] = resolved[identifier]
    return resolved


def propagation_targets(document, node_id, artifacts, source_edges=()):
    nodes = {node["id"]: node for node in document.get("nodes", [])}
    require(node_id in nodes, "Module not found", "not_found")
    adjacency = {key: set() for key in nodes}
    explicit = {(edge["source"], edge["target"]) for edge in document.get("edges", []) if edge["kind"] in {"data", "control"}}
    for edge in document.get("edges", []):
        source, target = edge["source"], edge["target"]
        if edge["kind"] == "semantic":
            if edge["relation"] == "alternative_to":
                continue
            if edge["relation"] in {"refines", "continues", "inspired_by"}:
                source, target = target, source
            if (target, source) in explicit:
                continue
        if source in nodes and target in nodes:
            adjacency[source].add(target)
    order, seen = [node_id], {node_id}
    for identifier in order:
        for target in sorted(adjacency[identifier]):
            if target not in seen:
                seen.add(target)
                order.append(target)
    raster = {item["id"] for item in artifacts if item["mime_type"] in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}}
    replacements, used, targets = effective_images(document, artifacts), set(), []
    aliases = image_aliases(artifacts)
    for identifier in order:
        node = nodes[identifier]
        source_ids = set(node.get("source_node_ids", []))
        producers = source_ids | {edge["target"] for edge in source_edges if edge["source"] in source_ids and edge["kind"] in {"data", "control"}}
        produced = [item["id"] for item in artifacts if item.get("producer_node_id") in producers]
        image_ids = list(dict.fromkeys(produced + [key for page in node.get("pages", []) for key in page["artifact_ids"]] + node["artifact_ids"]))
        for original in image_ids:
            original = aliases.get(original, original)
            if original in raster and original not in used:
                used.add(original)
                targets.append({"node_id": identifier, "module_label": node["label"], "original_id": original,
                                "artifact_id": replacements.get(original, original)})
    return targets


def attach_repo_media(router, account, optional_principal, platform):
    locks = {}

    def resolve(service, repo_id, subject, commit_id=None, write=False, branch="main"):
        with service.store.connection() as db:
            service.access(db, repo_id, subject, write=write)
            identifier = str(commit_id) if commit_id else service.branch(db, repo_id, branch)["head"]
            return service.commit(db, repo_id, identifier)

    def initialize(service):
        with service.store.connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS repository_image_edits (repo_id TEXT NOT NULL, request_id TEXT NOT NULL, "
                       "request_hash TEXT NOT NULL, artifact_id TEXT NOT NULL, artifact TEXT NOT NULL, result TEXT NOT NULL, "
                       "PRIMARY KEY(repo_id,request_id))")

    @router.get("/repos/{repo_id}/archive-info")
    def archive_info(repo_id: UUID, commit_id: UUID, subject=Depends(optional_principal), service=Depends(platform)):
        commit = resolve(service, str(repo_id), subject, commit_id)
        snapshot = source_snapshot(service, commit)
        present = {item["artifact_id"] for item in snapshot["manifest"]["artifacts"]}
        return {"commit_id": commit["id"], "import_id": snapshot["import_id"], "archive_sha256": snapshot["archive_sha256"],
                "artifacts": [{"id": item["id"], "label": item["uri"], "mime": item["mime_type"], "size": item["size"],
                               "available": item["id"] in present} for item in snapshot["archive"]["artifacts"]]}

    @router.get("/repos/{repo_id}/handoff")
    def handoff(repo_id: UUID, commit_id: UUID, format: str = Query("aica", pattern="^(aica|bundle)$"),
                subject=Depends(optional_principal), service=Depends(platform)):
        from .archive_export import export_bundle

        commit = resolve(service, str(repo_id), subject, commit_id)
        return export_bundle(service, str(repo_id), commit, portable=format == "aica")

    @router.get("/repos/{repo_id}/archive")
    def download_archive(repo_id: UUID, commit_id: UUID, request: Request, subject=Depends(optional_principal), service=Depends(platform)):
        commit = resolve(service, str(repo_id), subject, commit_id)
        if "application/vnd.aica.bundle+zip" in request.headers.get("accept", ""):
            from .archive_export import export_bundle

            return export_bundle(service, str(repo_id), commit)
        require(commit["project_id"] is not None, "No source archive", "not_found")
        with service.store.connection() as db:
            project = db.execute("SELECT subject FROM projects WHERE id=?", (commit["project_id"],)).fetchone()
        require(project is not None, "Artifact source missing", "not_found")
        content = service.store.archive_bytes(project["subject"], commit["project_id"], commit["import_id"])
        return Response(content, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="aica.json"',
                        "X-AiCA-SHA256": hashlib.sha256(content).hexdigest(), "X-Content-Type-Options": "nosniff"})

    @router.get("/repos/{repo_id}/modules/{node_id}/image-edit-plan")
    def plan(repo_id: UUID, node_id: str, branch: str = "main", subject=Depends(account), service=Depends(platform)):
        commit = resolve(service, str(repo_id), subject, write=True, branch=branch)
        snapshot = source_snapshot(service, commit)
        document = json.loads(commit["document"])
        return {"commit_id": commit["id"], "targets": propagation_targets(document, node_id, snapshot["archive"]["artifacts"], snapshot["archive"].get("graph", {}).get("edges", []))}

    @router.get("/repos/{repo_id}/images")
    def images(repo_id: UUID, commit_id: UUID | None = None, branch: str = "main",
               subject=Depends(optional_principal), service=Depends(platform)):
        initialize(service)
        commit = resolve(service, str(repo_id), subject, commit_id, branch=branch)
        document = json.loads(commit["document"])
        snapshot = source_snapshot(service, commit)
        images = [{"id": item["id"], "label": item["uri"], "mime": item["mime_type"],
                   "node_ids": [node["id"] for node in document.get("nodes", []) if item["id"] in node["artifact_ids"]]}
                  for item in snapshot["archive"]["artifacts"] if item["mime_type"].startswith("image/")]
        replacements = effective_images(document, snapshot["archive"]["artifacts"])
        aliases = image_aliases(snapshot["archive"]["artifacts"])
        images = [item for item in images if item["id"] not in aliases]
        for item in images:
            item["original_id"] = item["id"]
            item["id"] = replacements.get(item["id"], item["id"])
            if item["original_id"] != item["id"]:
                item["label"] = "修改结果 · " + item["label"]
        with service.store.connection() as db:
            latest = db.execute("WITH RECURSIVE lineage(id,parent,depth) AS (SELECT id,parent,0 FROM repo_commits WHERE id=? "
                                "UNION ALL SELECT c.id,c.parent,l.depth+1 FROM repo_commits c JOIN lineage l ON c.id=l.parent) "
                                "SELECT e.artifact_id FROM repository_image_edits e JOIN lineage l ON json_extract(e.result,'$.commit_id')=l.id "
                                "WHERE e.repo_id=? ORDER BY l.depth LIMIT 1", (commit["id"], str(repo_id))).fetchone()
        return {"commit_id": commit["id"], "images": images, "latest_result_id": latest["artifact_id"] if latest else None}

    @router.get("/repos/{repo_id}/images/{artifact_id}")
    def image(repo_id: UUID, artifact_id: str, commit_id: UUID, thumbnail: bool = Query(False),
              subject=Depends(optional_principal), service=Depends(platform)):
        commit = resolve(service, str(repo_id), subject, commit_id)
        initialize(service)
        record, path = media_path(service, str(repo_id), commit, artifact_id)
        return media_response(record, path, thumbnail)

    @router.post("/repos/{repo_id}/modules/{node_id}/image-edits")
    async def edit(repo_id: UUID, node_id: str, body: EditImage, request: Request,
                   subject=Depends(account), service=Depends(platform)):
        async with locks.setdefault(str(repo_id), asyncio.Lock()):
            initialize(service)
            repo_id = str(repo_id)
            resolve(service, repo_id, subject, body.expected_head, write=True)
            config = body.image_config
            require(config.provider != "local_fallback", "原图编辑需要支持参考图的真实模型，不能用离线占位图替代", "invalid_contract")
            public = config.model_dump(exclude={"api_key"})
            signature = digest({"node": node_id, "base": str(body.expected_head), "image": body.original_artifact_id,
                                "instruction": body.instruction, "region": body.region, "reference": body.reference_artifact_id, "config": public})
            with service.store.connection() as db:
                prior = db.execute("SELECT * FROM repository_image_edits WHERE repo_id=? AND request_id=?", (repo_id, str(body.request_id))).fetchone()
            if prior:
                require(prior["request_hash"] == signature, "Request ID changed", "id_conflict")
                return json.loads(prior["result"])
            commit = resolve(service, repo_id, subject, write=True, branch=body.branch)
            require(commit["id"] == str(body.expected_head), "Branch head changed; reload before editing", "id_conflict")
            document = json.loads(commit["document"])
            node = next((node for node in document.get("nodes", []) if node["id"] == node_id), None)
            require(node is not None, "Module not found", "not_found")
            require(body.instruction.strip(), "请描述想要的改动", "invalid_contract")
            region = body.region
            require(region is None or all(0 <= n <= 1 for n in region) and region[2] > 0 and region[3] > 0
                    and region[0] + region[2] <= 1.000001 and region[1] + region[3] <= 1.000001, "Invalid edit region", "invalid_contract")
            record, path = await run_in_threadpool(media_path, service, repo_id, commit, body.original_artifact_id)
            require(record["mime_type"].startswith("image/"), "请选择图片产物", "invalid_contract")
            try:
                original = decode(path.read_bytes())
            except PreviewError:
                require(False, "原图不可解码或超过图像像素限制", "invalid_contract")
            store = edit_store(service, repo_id)
            base = store.put(png(original), "image/png", node_id=None, run_id=None)
            artifacts = {base["id"]: base}
            prompt = "根据原图进行修改：" + body.instruction + "。保留未要求修改的内容、布局、人物身份与细节，不添加无关元素。"
            inputs = {"prompt": prompt, "reference_artifact_ids": [base["id"]], "original_image": base["id"]}
            if body.reference_artifact_id and body.reference_artifact_id != body.original_artifact_id:
                _, reference_path = await run_in_threadpool(media_path, service, repo_id, commit, body.reference_artifact_id)
                reference = store.put(png(decode(reference_path.read_bytes())), "image/png", node_id=None, run_id=None)
                artifacts[reference["id"]] = reference
                if config.provider == "qwen":
                    inputs["reference_artifact_ids"].insert(0, reference["id"])
                    roles = "第一张图是修改后的人物设定参考；第二张图是需要编辑的当前页面。"
                else:
                    inputs["reference_artifact_ids"].append(reference["id"])
                    roles = "第一张图是需要编辑的当前页面；第二张图是修改后的人物设定参考。"
                inputs["prompt"] += roles + "仅把同一人物的指定特征同步到当前页面，保留当前页面构图、文字和其他人物，不把设定图复制成页面。没有该人物时保持原样。"
            params = {}
            if region:
                mask = Image.new("L", original.size, 0)
                x, y, width, height = region
                left, top = min(original.width - 1, int(x * original.width)), min(original.height - 1, int(y * original.height))
                box = (left, top, max(left, min(original.width - 1, round((x + width) * original.width) - 1)),
                       max(top, min(original.height - 1, round((y + height) * original.height) - 1)))
                ImageDraw.Draw(mask).rectangle(box, fill=255)
                mask_artifact = store.put(png(mask), "image/png", node_id=None, run_id=None)
                artifacts[mask_artifact["id"]] = mask_artifact
                inputs["edit_mask"] = mask_artifact["id"]
                params["edit_mode"] = "masked_replace"
            try:
                settings = config.settings()
                model = settings.hq_model
                if config.provider == "qwen" and model in {"qwen-image", "qwen-image-edit-plus"}:
                    model = "qwen-image-2.0"
                settings = replace(settings, hq_model=model, draft_model=model, allow_fallback=False)
                profile = StageProfile("high_quality", "final", "hq", int(signature[:8], 16) % (2**31),
                                       original.width, original.height, model,
                                       {"width": original.width, "height": original.height, "steps": 20})
                execution = {"id": node_id, "execution": {"params": params}}
                context = ExecutionContext(run_id=new_id(), cache_key=signature, mode="final", resolved_inputs=inputs)
                work = ExecutionRequest(execution, artifacts, inputs, context, profile, signature, signature)
                candidate = await LiveAPIProvider(settings, getattr(request.app.state, "image_edit_transport", None)).generate(work, store)
                require(candidate is not None, "Image provider returned no result", "provider_rejected")
                generated = decode(candidate.data)
                if generated.size != original.size:
                    candidate.data = png(generated.resize(original.size, Image.Resampling.LANCZOS))
                    candidate.mime_type = "image/png"
                output = prepare_image_output(candidate, work, store)
            except (PreviewError, ValueError) as error:
                code = error.code if isinstance(error, PreviewError) else "invalid_provider_config"
                hints = {"401": "API Key无效或所属地域/服务不匹配", "403": "账号未开通该编辑模型或权限不足",
                         "404": "编辑接口路径或模型不存在", "400": "模型或请求参数不被该服务支持",
                         "429": "服务限流或额度不足"}
                hint = next((text for status, text in hints.items() if code.startswith("provider_http_" + status)),
                            "请求超时，可检查网络或调高超时" if code == "timeout" else "请检查编辑模型和API配置")
                require(False, "原图编辑失败（" + code + "）：" + hint + "；未生成新图片，未自动重试", "provider_rejected")
            artifact = store.put(png(decode(output.data)), "image/png", node_id=None, run_id=None)
            edit_id = new_id()
            node.setdefault("image_edits", []).append({"id": edit_id, "original_artifact_id": body.original_artifact_id,
                "result_artifact_id": artifact["id"], "instruction": body.instruction, "region": region, "model": model, "source": output.source})
            raw, format_name = validate_document(document)
            result = {"artifact_id": artifact["id"], "source": output.source, "model": model,
                      "protected_pixels": output.metadata.get("protected_pixel_count")}

            def publish(db, saved):
                db.execute("UPDATE repositories SET visibility='private' WHERE id=?", (repo_id,))
                result.update(commit_id=saved["id"], url=f"/api/repos/{repo_id}/images/{artifact['id']}?commit_id={saved['id']}")
                db.execute("INSERT INTO repository_image_edits VALUES (?,?,?,?,?,?)", (repo_id, str(body.request_id), signature,
                    artifact["id"], json.dumps(artifact), json.dumps(result)))

            service.append(subject, repo_id, Commit(branch=body.branch, expected_head=body.expected_head,
                message="原图增量编辑：" + body.instruction[:100], document=document), raw, format_name, on_commit=publish)
            return result
