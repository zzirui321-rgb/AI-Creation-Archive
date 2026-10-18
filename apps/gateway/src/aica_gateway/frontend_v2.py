import asyncio
import hashlib
import json
import re
import threading
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
from aica_schema.protocol import ProtocolError, loads, require
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from preview import ArtifactStore
from preview.contracts import ExecutionContext, ExecutionRequest, PreviewError, StageProfile
from preview.providers.live_api import LiveAPIProvider, checked_url
from preview.request_config import ImageConfig
from preview.scene_layers import decode, png, prepare_image_output
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .platform import NewRepository, validate_document
from .process_view import VERSION, stored_process_view
from .repo_media import media_path


def normalize_document(source):
    if isinstance(source, dict) and ("aica_version" in source or any(key in source for key in ("steps", "modules", "nodes"))):
        return source
    events = source if isinstance(source, list) else source.get("messages", source.get("events", [])) if isinstance(source, dict) else []
    require(isinstance(events, list) and events, "请选择有效AiCA档案、steps/modules或明确的messages/events记录。")
    nodes = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        role = event.get("role", event.get("type", "record"))
        content = event.get("content", event.get("text", event.get("prompt", "")))
        if isinstance(content, list):
            content = "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"})
        if not isinstance(content, str) or not content.strip():
            continue
        nodes.append({"id": "record-" + str(index), "title": str(event.get("label") or role)[:200],
                      "kind": "intent" if role == "user" else "decision" if role == "assistant" else "tool",
                      "prompt": content if role == "user" else "", "model": event.get("model") if isinstance(event.get("model"), str) else "",
                      "outputs": {"text": content} if role != "user" else {}, "provenance": "extracted",
                      "evidence": [{"source_index": index}]})
    require(nodes, "记录中没有可展示的明确文本内容；未推测缺失步骤。")
    return {"format": "aica-ui-module-record", "title": source.get("title", "导入的对话记录") if isinstance(source, dict) else "导入的对话记录",
            "modules": nodes, "edges": [], "golden_path": []}


class PreviewInput(BaseModel):
    project_id: str
    request_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    node_id: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=20000)
    model: str = Field(min_length=1, max_length=200)
    kind: str = "image"
    stage: str = "draft"
    provider: str = "qwen"
    api_base: str
    api_key: str = Field(min_length=1, max_length=2048, repr=False)
    params: dict = Field(default_factory=dict)
    reference: str | None = None
    local_edit: dict | None = None


class FrontendV2:
    def __init__(self, platform):
        self.platform = platform
        self.tasks = set()
        self.import_lock = threading.Lock()
        with platform.store.connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS frontend_jobs (id TEXT PRIMARY KEY, subject TEXT NOT NULL, "
                       "kind TEXT NOT NULL, repo_id TEXT, state TEXT NOT NULL, result TEXT, error TEXT, created_at REAL NOT NULL)")
            db.execute("UPDATE frontend_jobs SET state='error',error='服务重启中断了任务；不会自动重试模型请求。' "
                       "WHERE state IN ('queued','running')")

    async def close(self):
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def assets(self, subject):
        directory = hashlib.sha256(subject.encode()).hexdigest()
        return ArtifactStore(self.platform.store.directory / "frontend-assets" / directory)

    def job(self, identifier, subject):
        with self.platform.store.connection() as db:
            row = db.execute("SELECT * FROM frontend_jobs WHERE id=?", (identifier,)).fetchone()
            require(row is not None, "Task not found", "not_found")
            if row["subject"] != subject:
                allowed = False
                candidates = db.execute("SELECT r.id,c.document FROM repositories r JOIN repo_branches b ON b.repo_id=r.id "
                                        "JOIN repo_commits c ON c.id=b.head WHERE (r.owner=? OR r.visibility='public') "
                                        "AND instr(c.document,?)>0", (subject, identifier)).fetchall()
                for candidate in candidates:
                    ancestors = db.execute("WITH RECURSIVE parents(id,parent_repo) AS (SELECT id,parent_repo FROM repositories "
                                           "WHERE id=? UNION ALL SELECT r.id,r.parent_repo FROM repositories r JOIN parents p "
                                           "ON r.id=p.parent_repo) SELECT id FROM parents", (candidate["id"],)).fetchall()
                    if row["repo_id"] in {item["id"] for item in ancestors}:
                        allowed = True
                        break
                require(allowed, "Task not found", "not_found")
        return dict(row)

    def update(self, identifier, state, result=None, error=None):
        with self.platform.store.connection(write=True) as db:
            db.execute("UPDATE frontend_jobs SET state=?,result=COALESCE(?,result),error=? WHERE id=?",
                       (state, json.dumps(result, ensure_ascii=False) if result is not None else None, error, identifier))

    def import_document(self, subject, content):
        with self.import_lock:
            return self._import_document(subject, content)

    def import_result(self, job):
        analysis = json.loads(job["result"])
        if analysis["process_graph"].get("parser_version") == VERSION:
            return analysis
        with self.platform.store.connection() as db:
            commit = self.platform.commit(db, job["repo_id"], analysis["repository"]["commit_id"])
        source = json.loads(commit["document"])
        if "aica_version" in source:
            analysis["process_graph"] = stored_process_view(self.platform, commit, source)
            self.update(job["id"], "ready", analysis)
        return analysis

    def _import_document(self, subject, content):
        source = loads(content.decode("utf-8-sig"))
        source = normalize_document(source)
        raw, format_name = validate_document(source)
        identifier = hashlib.sha256(subject.encode() + b"\0" + content).hexdigest()[:32]
        with self.platform.store.connection() as db:
            prior = db.execute("SELECT id FROM frontend_jobs WHERE id=?", (identifier,)).fetchone()
        if prior:
            return identifier
        title = str(source.get("metadata", {}).get("title") or source.get("title") or "导入的创作档案")[:200]
        if "aica_version" in source:
            project = self.platform.store.create_project(subject, title)
            receipt, _ = self.platform.store.ingest_json(subject, project["id"], content)
            with self.platform.store.connection() as db:
                publication = db.execute("SELECT repo_id,commit_id FROM import_publications WHERE import_id=?",
                                         (receipt["import_id"],)).fetchone()
            repo_id, commit_id = publication["repo_id"], publication["commit_id"]
            snapshot = self.platform.store.media_snapshot(subject, project["id"], receipt["import_id"])
            present = {item["artifact_id"] for item in snapshot["manifest"]["artifacts"]}
            artifacts = source["artifacts"]
            nodes = source["graph"]["nodes"]
            edges = source["graph"]["edges"]
            runs = source.get("runs", [])
            context = {"golden_path_node_ids": source["graph"]["golden_path"], "branches": source["graph"]["branches"]}
        else:
            with self.platform.store.connection(write=True) as db:
                repo = self.platform.create(db, subject, NewRepository(name="import-" + identifier, title=title))
            from .platform import Commit

            commit = self.platform.append(subject, repo["id"], Commit(expected_head=None, message="导入前端创作记录", document=source),
                                          raw, format_name)
            repo_id, commit_id = repo["id"], commit["id"]
            artifacts, present, runs, context = [], set(), [], {}
            nodes = source.get("steps", source.get("modules", source.get("nodes", [])))
            edges = source.get("edges", [])
        process = []
        for node in nodes:
            execution = node.get("execution") or {}
            run = next((item for item in reversed(runs) if item.get("node_id") == node["id"]), {})
            inputs = execution.get("inputs", node.get("inputs", {}))
            params = execution.get("params", node.get("params", {}))
            prompt = node.get("prompt") or params.get("prompt") or inputs.get("prompt") or ""
            if isinstance(prompt, dict):
                prompt = prompt.get("value", prompt.get("template", ""))
            model = node.get("model") or execution.get("model")
            if isinstance(model, dict):
                model = model.get("id") or model.get("name")
            provenance = node.get("provenance", "recorded")
            if isinstance(provenance, dict):
                provenance = provenance.get("mode", "recorded")
            process.append({"id": node["id"], "label": node.get("label", node.get("title", node["id"])),
                            "kind": node.get("kind", "tool"), "prompt": str(prompt), "model": model,
                            "mode": execution.get("kind", node.get("mode")), "harness": node.get("harness_id", node.get("harness")),
                            "params": params, "inputs": inputs, "outputs": run.get("outputs", node.get("outputs", execution.get("outputs", []))),
                            "status": run.get("state"), "provenance": provenance, "evidence_ids": [], "branch_id": node.get("branch_id"),
                            "artifact_ids": [item["id"] for item in artifacts if item.get("producer_node_id") == node["id"]],
                            "inline_artifacts": node.get("artifacts", [])})
        analysis = {"format": "aica-graph-analysis/0.1", "title": title, "description": source.get("description", "原始记录展示，未调用AI提炼。"),
                    "source": {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "kind": "archive"},
                    "process_graph": {"format": "aica-process-view/0.1", "nodes": process,
                                      "edges": [{**edge, "id": edge.get("id", f"edge-{i}"), "state": "verified", "kind": edge.get("kind", "control"),
                                                 "explanation": edge.get("explanation", "原始档案登记的关系")} for i, edge in enumerate(edges)],
                                      "evidence": [], "warnings": [], "source_context": context},
                    "artifacts": [{"id": item["id"], "mime": item["mime_type"], "available": item["id"] in present} for item in artifacts],
                    "issues": [], "repository": {"id": repo_id, "commit_id": commit_id},
                    "ui_state": {"preview_history": source.get("preview_history", []), "cover_artifact": source.get("cover_artifact")}}
        if "aica_version" in source:
            with self.platform.store.connection() as db:
                commit = self.platform.commit(db, repo_id, commit_id)
            analysis["process_graph"] = stored_process_view(self.platform, commit, source)
        with self.platform.store.connection(write=True) as db:
            db.execute("INSERT OR IGNORE INTO frontend_jobs VALUES (?,?,?,?,?,?,NULL,?)",
                       (identifier, subject, "import", repo_id, "ready", json.dumps(analysis, ensure_ascii=False), time.time()))
        return identifier

    def source_image(self, subject, uri):
        parsed = urlsplit(uri)
        preview = re.fullmatch(r"/api/creation-parser/previews/([a-f0-9]{32})/(artifact|source)", parsed.path)
        if preview:
            job = self.job(preview[1], subject)
            result = json.loads(job["result"] or "{}")
            record = result.get("stored_source" if preview[2] == "source" else "stored_artifact")
            require(record is not None, "Reference image unavailable", "not_found")
            return decode(self.assets(job["subject"]).read(record))
        imported = re.fullmatch(r"/api/creation-parser/jobs/([a-f0-9]{32})/artifacts/([^/]+)", parsed.path)
        if imported:
            from urllib.parse import unquote

            job = self.job(imported[1], subject)
            result = json.loads(job["result"])
            with self.platform.store.connection() as db:
                commit = self.platform.commit(db, job["repo_id"], result["repository"]["commit_id"])
            record, path = media_path(self.platform, job["repo_id"], commit, unquote(imported[2]))
            require(record["mime_type"].startswith("image/"), "Reference is not an image")
            return decode(path.read_bytes())
        match = re.fullmatch(r"/api/repos/([a-f0-9-]{36})/(artifacts|images)/([^/]+)", parsed.path)
        require(match is not None, "参考图必须是当前平台已登记的图片", "invalid_contract")
        from urllib.parse import unquote

        commit_id = parse_qs(parsed.query).get("commit_id", [None])[0]
        with self.platform.store.connection() as db:
            self.platform.access(db, match[1], subject)
            commit = self.platform.commit(db, match[1], commit_id)
        record, path = media_path(self.platform, match[1], commit, unquote(match[3]))
        require(record["mime_type"].startswith("image/"), "Reference is not an image")
        return decode(path.read_bytes())

    async def preview(self, identifier, subject, body, transport):
        try:
            self.update(identifier, "running")
            if body.kind == "video":
                await self.video(identifier, subject, body, transport)
                return
            store = self.assets(subject)
            artifacts, inputs, params = {}, {"prompt": body.prompt}, {}
            original = None
            crop_box = None
            source_uri = body.local_edit.get("source_uri") if body.local_edit else body.reference
            result = json.loads(self.job(identifier, subject)["result"] or "{}")
            if source_uri:
                original = await run_in_threadpool(self.source_image, subject, source_uri)
                reference = store.put(png(original), "image/png", node_id=None, run_id=None)
                artifacts[reference["id"]] = reference
                inputs.update(reference_artifact_ids=[reference["id"]], original_image=reference["id"])
                result["stored_source"] = reference
            if body.local_edit:
                edit = body.local_edit
                points = edit.get("points")
                if not points:
                    rect = edit.get("rect", {})
                    x, y, w, h = (rect.get(key, 0) for key in ("x", "y", "width", "height"))
                    points = [{"x": x, "y": y}, {"x": x + w, "y": y}, {"x": x + w, "y": y + h}, {"x": x, "y": y + h}]
                require(isinstance(points, list) and 3 <= len(points) <= 4096 and
                        all(isinstance(p, dict) and all(isinstance(p.get(k), (int, float)) and 0 <= p[k] <= 1 for k in ("x", "y")) for p in points),
                        "无效局部编辑选区")
                mask = Image.new("L", original.size, 0)
                ImageDraw.Draw(mask).polygon([(min(original.width - 1, round(p["x"] * original.width)),
                                              min(original.height - 1, round(p["y"] * original.height))) for p in points], fill=255)
                require(mask.getbbox() is not None, "选区为空")
                feather = edit.get("feather_px", 0)
                require(isinstance(feather, (int, float)) and 0 <= feather <= 12, "无效边缘过渡")
                if feather:
                    mask = ImageChops.multiply(mask, mask.filter(ImageFilter.GaussianBlur(feather)))
                saved_mask = store.put(png(mask), "image/png", node_id=None, run_id=None)
                artifacts[saved_mask["id"]] = saved_mask
                inputs["edit_mask"] = saved_mask["id"]
                params["edit_mode"] = "masked_replace"
                left, top, right, bottom = mask.getbbox()
                padding = max(32, round(max(right - left, bottom - top) * .2))
                crop_box = (max(0, left - padding), max(0, top - padding), min(original.width, right + padding), min(original.height, bottom + padding))
                cropped = store.put(png(original.crop(crop_box)), "image/png", node_id=None, run_id=None)
                artifacts[cropped["id"]] = cropped
                inputs["reference_artifact_ids"] = [cropped["id"]]
                inputs["prompt"] = "根据原图，仅修改所选区域：" + body.prompt + "。保留人物身份、构图、文字和未要求改变的细节。"
            config = ImageConfig(provider=body.provider, api_base=body.api_base, api_key=body.api_key, model=body.model,
                                 timeout_seconds=120, allow_fallback=False)
            settings = config.settings()
            model = "qwen-image-2.0" if body.provider == "qwen" and body.model in {"qwen-image", "qwen-image-edit-plus"} else body.model
            settings = replace(settings, hq_model=model, draft_model=model, allow_fallback=False)
            width, height = original.size if original else (1024, 1024)
            generation_width, generation_height = (crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]) if crop_box else (width, height)
            signature = hashlib.sha256(identifier.encode()).hexdigest()
            profile = StageProfile("high_quality", "final", "hq", int(signature[:8], 16) % (2**31), generation_width, generation_height, model,
                                   {"width": generation_width, "height": generation_height, "steps": 20})
            context = ExecutionContext(run_id=str(uuid4()), cache_key=signature, mode="final", resolved_inputs=inputs)
            result["generation_request"] = {"prompt": inputs["prompt"], "model": model, "params": params,
                                            "provider": body.provider, "kind": "image"}
            work = ExecutionRequest({"id": body.node_id, "execution": {"params": params}}, artifacts, inputs, context, profile, signature, signature)
            candidate = await LiveAPIProvider(settings, transport).generate(work, store)
            require(candidate is not None, "模型没有返回图片")
            image = decode(candidate.data)
            if crop_box:
                aligned = image.resize((generation_width, generation_height), Image.Resampling.LANCZOS)
                full = original.copy()
                full.paste(aligned, (crop_box[0], crop_box[1]))
                candidate.data = png(full)
            elif original and image.size != original.size:
                candidate.data = png(image.resize(original.size, Image.Resampling.LANCZOS))
            output = prepare_image_output(candidate, work, store)
            record = store.put(png(decode(output.data)), "image/png", node_id=None, run_id=None)
            base = f"/api/creation-parser/previews/{identifier}"
            result.update(artifact={"url": base + "/artifact", "mime": "image/png", "sha256": record["sha256"]}, stored_artifact=record,
                          model=model, protected_pixels=output.metadata.get("protected_pixel_count"))
            if body.local_edit:
                result["local_edit"] = {"source_url": base + "/source", "source_sha256": result["stored_source"]["sha256"],
                                        "source_width": width, "source_height": height}
            self.update(identifier, "ready", result)
        except asyncio.CancelledError:
            self.update(identifier, "cancelled", error="已停止等待，供应商任务可能仍在运行并计费。")
        except Exception as error:
            code = error.code if isinstance(error, (PreviewError, ProtocolError)) else "invalid_preview"
            self.update(identifier, "error", error=f"生成失败（{code}）；请检查模型、权限、参数和额度，未自动重试。")


    async def video(self, identifier, subject, body, transport):
        require(body.provider == "qwen" and "t2v" in body.model.lower(), "此入口支持千问文生视频，其他模型请使用团队网关。")
        require(not body.reference and not body.local_edit, "当前视频入口仅支持文生视频，未忽略参考图调用模型。")
        root = checked_url(body.api_base).rstrip("/")
        require(not urlsplit(root).query and not urlsplit(root).fragment, "模型地址不能包含查询参数。")
        parameters = {key: value for key, value in body.params.items() if key in {"size", "resolution", "ratio", "duration", "seed", "prompt_extend", "watermark"}}
        if body.model.startswith("wan2.7"):
            parameters.pop("size", None)
        else:
            parameters.pop("resolution", None)
            parameters.pop("ratio", None)
        headers = {"Authorization": "Bearer " + body.api_key}
        async with asyncio.timeout(360), httpx.AsyncClient(timeout=120, trust_env=False, follow_redirects=False, transport=transport) as client:
            response = await client.post(root + "/services/aigc/video-generation/video-synthesis", headers={**headers, "X-DashScope-Async": "enable"},
                                         json={"model": body.model, "input": {"prompt": body.prompt}, "parameters": parameters})
            response.raise_for_status()
            upstream = response.json().get("output", {}).get("task_id", "")
            require(isinstance(upstream, str) and re.fullmatch(r"[A-Za-z0-9-]{1,100}", upstream), "视频服务没有返回有效任务ID。")
            metadata = json.loads(self.job(identifier, subject)["result"] or "{}")
            metadata["upstream_task_id"] = upstream
            metadata["generation_request"] = {"prompt": body.prompt, "model": body.model, "params": parameters,
                                              "provider": body.provider, "kind": "video"}
            self.update(identifier, "running", metadata)
            while True:
                response = await client.get(root + "/tasks/" + upstream, headers=headers)
                response.raise_for_status()
                output = response.json().get("output", {})
                if output.get("task_status") == "SUCCEEDED":
                    break
                require(output.get("task_status") in {"PENDING", "RUNNING"}, "供应商视频任务未成功完成。")
                await asyncio.sleep(15)
            data = bytearray()
            async with client.stream("GET", checked_url(output["video_url"])) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    require(len(data) + len(chunk) <= 64 * 1024 * 1024, "视频产物超过64MiB保存上限。")
                    data.extend(chunk)
            require(len(data) >= 12 and data[4:8] == b"ftyp", "供应商没有返回有效MP4文件。")
        record = self.assets(subject).put(bytes(data), "video/mp4", node_id=None, run_id=None)
        metadata.update(artifact={"url": f"/api/creation-parser/previews/{identifier}/artifact", "mime": "video/mp4", "sha256": record["sha256"]},
                        stored_artifact=record, model=body.model)
        self.update(identifier, "ready", metadata)


def attach_frontend_v2(app, principal, optional_principal):
    router = APIRouter(prefix="/api/creation-parser")

    def service(request: Request):
        return request.app.state.frontend_v2

    def account(subject=Depends(principal), bridge=Depends(service)):
        bridge.platform.user(subject)
        return subject

    def public_job(job):
        result = json.loads(job["result"] or "{}") if job["kind"] == "preview" else {}
        return {"id": job["id"], "status": job["state"], "created_at": int(job["created_at"] * 1000), "error": job["error"],
                "message": {"queued": "正在等待生成…", "running": "供应商正在生成…", "ready": "已保存结果"}.get(job["state"], "任务未完成"),
                **{key: value for key, value in result.items() if not key.startswith("stored_") and key != "request_hash"}}

    @router.get("/health")
    def health():
        return {"status": "ok", "features": ["archive-import", "local-image-edit", "local-image-edit-shapes"]}

    @router.post("/imports", status_code=201)
    async def import_file(request: Request, subject=Depends(account), bridge=Depends(service)):
        identifier = await run_in_threadpool(bridge.import_document, subject, await request.body())
        return {"id": identifier, "status": "ready"}

    @router.get("/jobs/{identifier}")
    def import_status(identifier: str, subject=Depends(optional_principal), bridge=Depends(service)):
        return public_job(bridge.job(identifier, subject))

    @router.get("/jobs/{identifier}/result")
    def import_result(identifier: str, subject=Depends(optional_principal), bridge=Depends(service)):
        job = bridge.job(identifier, subject)
        require(job["kind"] == "import", "Import task not found", "not_found")
        return bridge.import_result(job)

    @router.get("/jobs/{identifier}/artifacts/{artifact_id}")
    def import_artifact(identifier: str, artifact_id: str, subject=Depends(optional_principal), bridge=Depends(service)):
        job = bridge.job(identifier, subject)
        analysis = json.loads(job["result"])
        with bridge.platform.store.connection() as db:
            commit = bridge.platform.commit(db, job["repo_id"], analysis["repository"]["commit_id"])
        record, path = media_path(bridge.platform, job["repo_id"], commit, artifact_id)
        return Response(path.read_bytes(), media_type=record["mime_type"], headers={"X-AiCA-SHA256": record["sha256"], "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'",
                                                                                   "Content-Disposition": "attachment"})

    @router.get("/previews")
    def preview_history(project_id: str, subject=Depends(account), bridge=Depends(service)):
        with bridge.platform.store.connection() as db:
            bridge.platform.access(db, project_id, subject, write=True)
            rows = db.execute("SELECT * FROM frontend_jobs WHERE repo_id=? AND subject=? AND kind='preview' AND state='ready' "
                              "ORDER BY created_at DESC LIMIT 200", (project_id, subject)).fetchall()
        return {"previews": [public_job(dict(row)) for row in rows]}

    @router.post("/previews", status_code=202)
    async def preview(body: PreviewInput, request: Request, subject=Depends(account), bridge=Depends(service)):
        require(body.kind in {"image", "video"}, "Unsupported preview kind")
        require(body.provider != "local_fallback", "此入口只接受真实模型配置，不返回模拟图片。")
        with bridge.platform.store.connection(write=True) as db:
            bridge.platform.access(db, body.project_id, subject, write=True)
            identifier = hashlib.sha256((subject + body.project_id + body.request_id).encode()).hexdigest()[:32] if body.request_id else uuid4().hex
            signature = hashlib.sha256(json.dumps(body.model_dump(exclude={"api_key"}), sort_keys=True).encode()).hexdigest()
            prior = db.execute("SELECT * FROM frontend_jobs WHERE id=?", (identifier,)).fetchone()
            if prior:
                require(json.loads(prior["result"] or "{}").get("request_hash") == signature, "请求ID对应的内容已变化", "id_conflict")
                return public_job(dict(prior))
            metadata = {"node_id": body.node_id, "prompt": body.prompt, "model": body.model, "stage": body.stage,
                        "request_hash": signature, "edit_request": body.local_edit}
            db.execute("INSERT INTO frontend_jobs VALUES (?,?,?,?,?,?,NULL,?)", (identifier, subject, "preview", body.project_id, "queued", json.dumps(metadata), time.time()))
        task = asyncio.create_task(bridge.preview(identifier, subject, body, getattr(request.app.state, "image_edit_transport", None)))
        bridge.tasks.add(task)
        task.add_done_callback(bridge.tasks.discard)
        return public_job(bridge.job(identifier, subject))

    @router.get("/previews/{identifier}")
    def preview_status(identifier: str, subject=Depends(optional_principal), bridge=Depends(service)):
        return public_job(bridge.job(identifier, subject))

    @router.post("/previews/{identifier}/cancel")
    def cancel(identifier: str, subject=Depends(account), bridge=Depends(service)):
        job = bridge.job(identifier, subject)
        require(job["subject"] == subject, "Task not found", "not_found")
        return {"status": "success", "message": "客户端已停止等待；供应商任务可能继续运行，完成结果仍会保存。"}

    @router.get("/previews/{identifier}/{kind}")
    def preview_asset(identifier: str, kind: str, subject=Depends(optional_principal), bridge=Depends(service)):
        require(kind in {"artifact", "source"}, "Artifact not found", "not_found")
        job = bridge.job(identifier, subject)
        record = json.loads(job["result"] or "{}").get("stored_source" if kind == "source" else "stored_artifact")
        require(record is not None, "Artifact not ready", "not_found")
        store = bridge.assets(job["subject"])
        content = store.read(record)
        if record["mime_type"].startswith("video/"):
            return FileResponse(store.local_path(record["uri"]), media_type=record["mime_type"], headers={"X-AiCA-SHA256": record["sha256"]})
        return Response(content, media_type=record["mime_type"],
                        headers={"X-AiCA-SHA256": record["sha256"], "X-Content-Type-Options": "nosniff"})

    app.include_router(router)
