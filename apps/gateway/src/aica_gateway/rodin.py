"""Image-to-3D jobs with immutable source recipes and server-only credentials."""
import asyncio
import hashlib
import io
import ipaddress
import json
import os
import socket
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import httpx
from aica_schema.protocol import require
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

API = "https://api.hyper3d.com/api/v2"
TIER = "Gen-2"
MAX_MODEL = 256 * 1024 * 1024


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_head: UUID
    branch: str = Field(default="main", min_length=1, max_length=80)
    node_id: str = Field(min_length=1, max_length=180)
    artifact_id: str = Field(min_length=1, max_length=200)
    source_uri: str = Field(min_length=1, max_length=2000)
    prompt: str = Field(default="", max_length=2000)
    consent: bool = False


def credential():
    path = os.environ.get("AICA_HYPER3D_KEY_FILE")
    return Path(path).read_text(encoding="utf-8-sig").strip() if path else os.environ.get("HYPER3D_API_KEY", "").strip()


async def public_download(client, url):
    for _ in range(4):
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username and parsed.port in {None, 443}, "无效模型下载地址")
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM)
        require(addresses and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses), "模型下载地址不可访问")
        async with client.stream("GET", url, headers={"Accept": "model/gltf-binary"}) as response:
            if response.is_redirect:
                from urllib.parse import urljoin
                url = urljoin(url, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                require(len(content) + len(chunk) <= MAX_MODEL, "模型文件超出256MB限制")
                content.extend(chunk)
            require(len(content) >= 12 and content[:4] == b"glTF", "供应商没有返回有效GLB")
            return bytes(content)
    raise ValueError("Too many redirects")


class Rodin:
    def __init__(self, platform, frontend):
        self.platform, self.frontend, self.tasks = platform, frontend, {}
        with platform.store.connection(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS rodin_jobs (id TEXT PRIMARY KEY, subject TEXT NOT NULL, repo_id TEXT NOT NULL, "
                       "request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, recipe TEXT NOT NULL, state TEXT NOT NULL, "
                       "remote_id TEXT, subscription TEXT, artifact TEXT, error TEXT, created_at REAL NOT NULL, "
                       "UNIQUE(subject,repo_id,request_id))")
            db.execute("UPDATE rodin_jobs SET state='unknown',error='提交期间服务中断；请先在 Hyper3D 控制台确认，勿重复生成。' "
                       "WHERE state='submitting'")
            db.execute("UPDATE rodin_jobs SET state='pending',error='服务重启后请继续查询原任务，不会重新提交生成。' "
                       "WHERE remote_id IS NOT NULL AND state IN ('running','downloading')")
            db.execute("UPDATE rodin_jobs SET state='failed',error='提交前服务已重启，此请求未发送。' WHERE state='queued'")

    def row(self, identifier):
        with self.platform.store.connection() as db:
            row = db.execute("SELECT * FROM rodin_jobs WHERE id=?", (identifier,)).fetchone()
        require(row is not None, "3D任务不存在", "not_found")
        return dict(row)

    def access(self, identifier, subject, repo_id=None):
        row = self.row(identifier)
        with self.platform.store.connection() as db:
            target = repo_id or row["repo_id"]
            self.platform.access(db, target, subject)
            if target != row["repo_id"]:
                ancestors = db.execute("WITH RECURSIVE p(id,parent_repo) AS (SELECT id,parent_repo FROM repositories WHERE id=? "
                                       "UNION ALL SELECT r.id,r.parent_repo FROM repositories r JOIN p ON r.id=p.parent_repo) SELECT id FROM p", (target,)).fetchall()
                require(row["repo_id"] in {r["id"] for r in ancestors}, "3D任务不存在", "not_found")
                docs = db.execute("SELECT document FROM repo_commits WHERE repo_id=? AND instr(document,?)>0", (target, identifier)).fetchall()
                require(docs, "3D任务未保存在此Fork中", "not_found")
        return row

    def view(self, row, repo_id=None):
        result = {key: row[key] for key in ("id", "repo_id", "state", "created_at", "error")}
        result["recipe"] = json.loads(row["recipe"])
        if row["artifact"]:
            record = json.loads(row["artifact"])
            result["artifact"] = {"id": row["id"], "uri": f"/api/3d/jobs/{row['id']}/model?repo_id={repo_id or row['repo_id']}",
                                  "mime": "model/gltf-binary", "label": "Rodin · GLB", "sha256": record["sha256"]}
        return result

    def update(self, identifier, **values):
        with self.platform.store.connection(write=True) as db:
            db.execute("UPDATE rodin_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?", (*values.values(), identifier))

    def start(self, identifier, image=None):
        if identifier in self.tasks:
            return
        task = asyncio.create_task(self.run(identifier, image))
        self.tasks[identifier] = task
        task.add_done_callback(lambda _: self.tasks.pop(identifier, None))

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, identifier, image=None):
        submitted = False
        try:
            row = self.row(identifier)
            recipe = json.loads(row["recipe"])
            key = credential()
            require(key, "后端尚未配置 Hyper3D Key")
            async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
                headers = {"Authorization": "Bearer " + key}
                if not row["remote_id"]:
                    self.update(identifier, state="submitting")
                    submitted = True
                    response = await client.post(API + "/rodin", headers=headers, files={"images": ("reference.png", image, "image/png")},
                                                 data={"tier": recipe["tier"], "mesh_mode": "Raw", "quality": "medium", "material": "PBR",
                                                       "geometry_file_format": "glb", "prompt": recipe["prompt"]})
                    response.raise_for_status()
                    data = response.json()
                    if data.get("error"):
                        self.update(identifier, state="failed", error="Hyper3D拒绝生成，请检查额度、图片内容或 API 权限；未自动重试。")
                        return
                    require(data.get("uuid") and data.get("jobs", data.get("job", {})).get("subscription_key"), "供应商响应缺少任务标识")
                    subscription = data.get("jobs", data.get("job", {}))["subscription_key"]
                    recipe["consumed_credits"] = data.get("consumed")
                    self.update(identifier, state="running", remote_id=data["uuid"], subscription=subscription, recipe=json.dumps(recipe))
                    row = self.row(identifier)
                for _ in range(240):
                    await asyncio.sleep(float(os.environ.get("AICA_RODIN_POLL_SECONDS", "5")))
                    response = await client.post(API + "/status", headers=headers, json={"subscription_key": row["subscription"]})
                    response.raise_for_status()
                    statuses = [str(item.get("status", "")).lower() for item in response.json().get("jobs", [])]
                    if any(value in {"failed", "error", "cancelled"} for value in statuses):
                        self.update(identifier, state="failed", error="Hyper3D生成失败；未自动重试。")
                        return
                    if statuses and all(value == "done" for value in statuses):
                        break
                else:
                    self.update(identifier, state="pending", error="供应商仍在处理，可继续查询；无需重复提交生成。")
                    return
                self.update(identifier, state="downloading", error=None)
                response = await client.post(API + "/download", headers=headers, json={"task_uuid": row["remote_id"]})
                response.raise_for_status()
                files = response.json().get("list", [])
                entry = next((item for item in files if str(item.get("name", "")).lower().endswith(".glb") or urlsplit(item.get("url", "")).path.lower().endswith(".glb")), None)
                require(entry is not None, "供应商暂未返回GLB，可继续查询下载结果")
                content = await public_download(client, entry["url"])
                directory = self.platform.store.directory / "rodin-models"
                directory.mkdir(exist_ok=True)
                path = directory / (identifier + ".glb")
                await asyncio.to_thread(path.write_bytes, content)
                self.update(identifier, state="succeeded", artifact=json.dumps({"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}), error=None)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            row = self.row(identifier)
            if row["remote_id"]:
                self.update(identifier, state="pending", error="查询或下载暂未完成；可继续查询原任务，不会重新计费提交。")
            else:
                code = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
                uncertain = submitted and code not in {400, 401, 402, 403, 404, 422, 429}
                self.update(identifier, state="unknown" if uncertain else "failed",
                            error="提交结果不确定，请先检查 Hyper3D 控制台，勿重复生成。" if uncertain else f"Hyper3D拒绝请求（HTTP {code or '连接失败'}）；未自动重试。")


def attach_rodin(app, account, optional_principal):
    router = APIRouter(prefix="/api/3d")
    def service(request: Request):
        return request.app.state.rodin

    @router.get("/capabilities")
    def capabilities():
        return {"configured": bool(credential()), "provider": "Hyper3D", "tier": TIER, "format": "glb", "material": "PBR"}

    @router.post("/repos/{repo_id}/jobs", status_code=202)
    async def create(repo_id: UUID, body: Generation, subject=Depends(account), rodin=Depends(service)):
        repo_id = str(repo_id)
        recipe = {"provider": "hyper3d", "tier": TIER, "mesh_mode": "Raw", "quality": "medium", "material": "PBR", "format": "glb",
                  "source_repo": repo_id, "source_commit": str(body.expected_head), "source_node": body.node_id,
                  "source_artifact": body.artifact_id, "source_uri": body.source_uri, "prompt": body.prompt}
        fingerprint = hashlib.sha256(json.dumps({**recipe, "branch": body.branch}, sort_keys=True).encode()).hexdigest()
        with rodin.platform.store.connection() as db:
            rodin.platform.access(db, repo_id, subject, write=True)
            old = db.execute("SELECT * FROM rodin_jobs WHERE subject=? AND repo_id=? AND request_id=?", (subject, repo_id, str(body.request_id))).fetchone()
            if old:
                require(old["fingerprint"] == fingerprint, "请求ID已用于不同参数", "id_conflict")
                return rodin.view(dict(old))
            require(rodin.platform.branch(db, repo_id, body.branch)["head"] == str(body.expected_head), "版本已变化，请先保存并刷新", "id_conflict")
            commit = rodin.platform.commit(db, repo_id, str(body.expected_head))
            document = json.loads(commit["document"])
            nodes = document.get("steps", document.get("modules", document.get("nodes", document.get("graph", {}).get("nodes", []))))
            node = next((node for node in nodes if node["id"] == body.node_id), None)
            require(node is not None, "源图片节点不存在", "not_found")
            produced = [item for item in document.get("artifacts", []) if item.get("producer_node_id") == body.node_id]
            identities, uris = set(), set()
            def references(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if isinstance(item, str) and key in {"uri", "url", "src"}:
                            uris.add(item)
                        if isinstance(item, str) and key in {"artifact_id", "id"}:
                            identities.add(item)
                        if key == "artifact_ids" and isinstance(item, list):
                            identities.update(key for key in item if isinstance(key, str))
                        references(item)
                elif isinstance(value, list):
                    for item in value:
                        references(item)
            references(node)
            identities.update(item["id"] for item in produced)
            require(body.artifact_id in identities or body.source_uri in uris, "请先保存图片到当前节点")
            parsed = urlsplit(body.source_uri)
            if parsed.path.startswith("/api/repos/"):
                require(parsed.path.split('/')[3] == repo_id and unquote(parsed.path.split('/')[-1]) == body.artifact_id,
                        "源图片与当前仓库或产物身份不一致")
        require(body.consent, "请确认将图片发送至 Hyper3D 并消耗生成额度")
        require(credential(), "后端尚未配置 Hyper3D Key")
        require(body.source_uri.startswith("/api/") and not urlsplit(body.source_uri).netloc, "仅支持平台内已保存图片")
        image = await run_in_threadpool(rodin.frontend.source_image, subject, body.source_uri)
        require(isinstance(image, Image.Image), "输入必须是图片")
        image.thumbnail((2048, 2048))
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        content = output.getvalue()
        recipe["input_sha256"] = hashlib.sha256(content).hexdigest()
        identifier = uuid4().hex
        with rodin.platform.store.connection(write=True) as db:
            old = db.execute("SELECT * FROM rodin_jobs WHERE subject=? AND repo_id=? AND request_id=?", (subject, repo_id, str(body.request_id))).fetchone()
            if old:
                require(old["fingerprint"] == fingerprint, "请求ID已用于不同参数", "id_conflict")
                return rodin.view(dict(old))
            require(not db.execute("SELECT 1 FROM rodin_jobs WHERE subject=? AND state IN ('queued','submitting','running','downloading','unknown')", (subject,)).fetchone(), "已有3D任务处理中或提交结果不确定，请先核查原任务", "id_conflict")
            db.execute("INSERT INTO rodin_jobs VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)", (identifier, subject, repo_id, str(body.request_id), fingerprint, json.dumps(recipe), "queued", time.time()))
        rodin.start(identifier, content)
        return rodin.view(rodin.row(identifier))

    @router.get("/repos/{repo_id}/jobs")
    def jobs(repo_id: UUID, subject=Depends(account), rodin=Depends(service)):
        with rodin.platform.store.connection() as db:
            rodin.platform.access(db, str(repo_id), subject)
            rows = db.execute("SELECT * FROM rodin_jobs WHERE repo_id=? ORDER BY created_at DESC LIMIT 40", (str(repo_id),)).fetchall()
        return {"jobs": [rodin.view(dict(row)) for row in rows]}

    @router.get("/jobs/{identifier}")
    def job(identifier: str, repo_id: UUID | None = None, subject=Depends(optional_principal), rodin=Depends(service)):
        return rodin.view(rodin.access(identifier, subject, str(repo_id) if repo_id else None), str(repo_id) if repo_id else None)

    @router.post("/jobs/{identifier}/resume")
    async def resume(identifier: str, subject=Depends(account), rodin=Depends(service)):
        row = rodin.row(identifier)
        require(row["subject"] == subject, "3D任务不存在", "not_found")
        require(row["remote_id"] and row["state"] in {"pending", "running", "downloading"}, "此任务不能重新查询")
        rodin.start(identifier)
        return rodin.view(row)

    @router.get("/jobs/{identifier}/model")
    def model(identifier: str, repo_id: UUID | None = None, subject=Depends(optional_principal), rodin=Depends(service)):
        row = rodin.access(identifier, subject, str(repo_id) if repo_id else None)
        require(row["state"] == "succeeded" and row["artifact"], "模型尚未生成", "not_found")
        path = rodin.platform.store.directory / "rodin-models" / (row["id"] + ".glb")
        record = json.loads(row["artifact"])
        require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"], "模型不可读取", "not_found")
        return FileResponse(path, media_type="model/gltf-binary", filename="aica-rodin.glb", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    app.include_router(router)
