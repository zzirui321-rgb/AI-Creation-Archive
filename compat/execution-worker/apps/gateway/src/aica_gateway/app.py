import hashlib
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from aica_mcp.files import ProjectLock
from aica_mcp.transfer import BUNDLE_LIMIT, CONTENT_TYPE, JSON_CONTENT_TYPE
from aica_runtime.engine import replay_plan
from aica_runtime.executors import default_registry
from aica_runtime.graph import golden_path
from aica_schema.protocol import PORTABLE_JSON_LIMIT, ProtocolError, loads, require, require_definition_snapshot
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from preview.request_config import ImageConfig, configured_registry
from starlette.concurrency import run_in_threadpool

from .jobs import JobManager, fork_project
from .store import PlatformStore


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)


class ForkBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    archive_id: str = Field(min_length=1, max_length=128)
    expected_revision: str = Field(min_length=1, max_length=128)
    operations: list[dict] = Field(max_length=1000)


class ExecutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    archive_id: str = Field(min_length=1, max_length=128)
    expected_revision: str = Field(min_length=1, max_length=128)
    target_node_ids: list[str] | None = Field(default=None, max_length=4096)
    stage: Literal["draft", "final"] = "draft"
    record: bool
    execute: bool
    image_config: ImageConfig | None = None


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archive_id: str = Field(min_length=1, max_length=128)
    stage: Literal["draft", "final"] = "draft"
    image_config: ImageConfig | None = None


def create_app(data_dir=None, tokens=None, registry=None, cors_origins=None):
    data_dir = data_dir or os.environ.get("AICA_DATA_DIR")
    require(data_dir is not None, "Configure AICA_DATA_DIR outside the repository")
    tokens = tokens if tokens is not None else loads(os.environ.get("AICA_API_TOKENS", "{}"))
    require(isinstance(tokens, dict) and bool(tokens)
            and all(isinstance(user, str) and user and isinstance(token, str) and 32 <= len(token) <= 512
                    and all(33 <= ord(char) <= 126 for char in token) for user, token in tokens.items())
            and len(set(tokens.values())) == len(tokens), "Configure distinct platform API tokens of at least 32 characters")
    token_hashes = {user: hashlib.sha256(token.encode()).digest() for user, token in tokens.items()}
    store = PlatformStore(data_dir)
    registry = registry or default_registry()
    manager = None
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal manager
        lock = ProjectLock(store.directory / "gateway.lock")
        lock.acquire()
        manager = JobManager(store, registry)
        try:
            await run_in_threadpool(manager.recover)
            yield
        finally:
            await run_in_threadpool(manager.shutdown)
            lock.close()

    app = FastAPI(title="AiCA development gateway", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.registry = registry
    origins = cors_origins if cors_origins is not None else loads(os.environ.get("AICA_CORS_ORIGINS", "[]"))
    require(isinstance(origins, list) and all(isinstance(origin, str) and origin != "*" for origin in origins),
            "CORS origins must be explicit addresses")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-AiCA-Bundle-SHA256"])

    def principal(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        require(credentials is not None and credentials.scheme.lower() == "bearer",
                "Platform authentication required", "unauthenticated")
        candidate = hashlib.sha256(credentials.credentials.encode()).digest()
        user = next((user for user, value in token_hashes.items() if secrets.compare_digest(candidate, value)), None)
        require(user is not None, "Platform authentication required", "unauthenticated")
        return user

    @app.exception_handler(ProtocolError)
    async def protocol_error(request: Request, error: ProtocolError):
        code = {"not_found": 404, "unauthenticated": 401, "not_authorized": 403,
                "revision_conflict": 409, "id_conflict": 409, "artifact_conflict": 409,
                "project_busy": 409, "transfer_limit": 413}.get(error.code, 422)
        headers = {"WWW-Authenticate": "Bearer"} if code == 401 else None
        response = JSONResponse({"status": "error", "code": error.code, "message": str(error),
                                 "details": error.details}, status_code=code, headers=headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        response = JSONResponse({"status": "error", "code": "invalid_request", "message": "Invalid request parameters"},
                                status_code=422)
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        response = JSONResponse({"status": "error", "code": "http_error", "message": str(error.detail)},
                                status_code=error.status_code)
        return response

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(request: Request, error: Exception):
        response = JSONResponse({"status": "error", "code": "storage_error", "message": "Platform storage unavailable"},
                                status_code=503)
        return response

    @app.get("/health")
    def health():
        return {"status": "success", "service": "aica-gateway", "version": "0.1.0"}

    @app.get("/api/executors")
    def executors(user: str = Depends(principal)):
        return {"status": "success", "executors": registry.describe()}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectBody, user: str = Depends(principal)):
        result = store.create_project(user, body.title)
        return result

    @app.get("/api/projects")
    def projects(user: str = Depends(principal), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        result = store.list_projects(user, offset, limit)
        return result

    @app.post("/api/projects/{project_id}/imports", status_code=201)
    async def import_bundle(project_id: str, request: Request, user: str = Depends(principal)):
        def authorize():
            with store.transaction() as database:
                store.project(database, project_id, user)

        await run_in_threadpool(authorize)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        require(content_type in {CONTENT_TYPE, JSON_CONTENT_TYPE}, "Expected an AiCA JSON or transfer ZIP request body")
        limit = PORTABLE_JSON_LIMIT if content_type == JSON_CONTENT_TYPE else BUNDLE_LIMIT
        require(request.headers.get("content-encoding", "identity") == "identity", "Encoded uploads are unsupported")
        expected = request.headers.get("x-aica-bundle-sha256", "")
        request_id = request.headers.get("idempotency-key", "")
        require(re.fullmatch(r"[a-f0-9]{64}", expected) is not None and request_id == "sha256:" + expected,
                "Provide matching bundle hash and idempotency headers")
        length = request.headers.get("content-length")
        require(length is None or length.isdecimal(), "Invalid Content-Length")
        require(length is None or int(length) <= limit, "Upload exceeds the selected format limit", "transfer_limit")
        descriptor, temporary = tempfile.mkstemp(dir=store.directory / "uploads")
        path = Path(temporary)
        try:
            size = 0
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    require(size <= limit, "Upload exceeds the selected format limit", "transfer_limit")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            require(length is None or int(length) == size, "Content-Length differs from request bytes")
            importer = store.import_json if content_type == JSON_CONTENT_TYPE else store.import_bundle
            result = await run_in_threadpool(importer, user, project_id, path, expected, request_id)
        finally:
            path.unlink(missing_ok=True)
        return result

    @app.get("/api/projects/{project_id}/archives")
    def archives(project_id: str, user: str = Depends(principal), offset: int = Query(0, ge=0),
                 limit: int = Query(50, ge=1, le=200)):
        result = store.list_archives(user, project_id, offset, limit)
        return result

    @app.get("/api/projects/{project_id}/archive")
    def archive(project_id: str, archive_id: str | None = None, user: str = Depends(principal)):
        result = store.read_archive(user, project_id, archive_id)
        return result

    @app.get("/api/projects/{project_id}/graph")
    def graph(project_id: str, archive_id: str | None = None, user: str = Depends(principal)):
        result = store.read_archive(user, project_id, archive_id)
        document = result["archive"]
        return {"status": "success", "archive_id": result["archive_id"], "graph": document["graph"],
                "variables": document["variables"], "interactive_questions": document["interactive_questions"],
                "artifacts": document["artifacts"], "runs": document["runs"], "snapshot": document["snapshot"]}

    @app.get("/api/projects/{project_id}/revisions/{revision_id}")
    def revision(project_id: str, revision_id: str, archive_id: str | None = None, user: str = Depends(principal)):
        result = store.read_archive(user, project_id, archive_id)
        document = result["archive"]
        if revision_id == document["graph"]["revision_id"]:
            definition = {key: document.get(key, [])
                          for key in ("graph", "variables", "interactive_questions", "variable_operations")}
        else:
            reference = next((item for item in document.get("history", [])
                              if item["kind"] == "revision" and item.get("revision_id") == revision_id), None)
            require(reference is not None, "Revision not found", "not_found")
            artifact = next(item for item in document["artifacts"] if item["id"] == reference["artifact_id"])
            definition = loads(store.read_blob(project_id, artifact["sha256"]).decode("utf-8"))
        require_definition_snapshot(definition, document["artifacts"], revision_id)
        return {"status": "success", "revision_id": revision_id, "definition": definition}

    @app.get("/api/projects/{project_id}/artifacts/{artifact_id}")
    def artifact(project_id: str, artifact_id: str, user: str = Depends(principal)):
        record, path = store.artifact(user, project_id, artifact_id)
        safe = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}
        mime_type = record["mime_type"] if record["mime_type"] in safe else "application/octet-stream"
        response = FileResponse(path, media_type=mime_type, filename=record["id"],
                                content_disposition_type="inline" if mime_type in safe else "attachment",
                                headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                         "Cache-Control": "private, no-store", "X-AiCA-SHA256": record["sha256"]})
        return response

    @app.post("/api/projects/{project_id}/forks", status_code=201)
    def fork(project_id: str, body: ForkBody, user: str = Depends(principal)):
        result = fork_project(store, user, project_id, body.model_dump())
        return result

    @app.get("/api/projects/{project_id}/plan")
    def plan(project_id: str, archive_id: str | None = None, target_node_ids: list[str] | None = Query(None),
             stage: Literal["draft", "final"] = "draft", user: str = Depends(principal)):
        document = store.read_archive(user, project_id, archive_id)["archive"]
        result = replay_plan(document, registry, lambda key: store.cache_get(project_id, key),
                             lambda sha256: store.read_blob(project_id, sha256), stage, target_node_ids)
        return result

    @app.get("/api/projects/{project_id}/golden-path")
    def recommended_path(project_id: str, archive_id: str | None = None, target_node_ids: list[str] | None = Query(None),
                         user: str = Depends(principal)):
        document = store.read_archive(user, project_id, archive_id)["archive"]
        return {"status": "success", "node_ids": golden_path(document, target_node_ids),
                "revision_id": document["graph"]["revision_id"], "method": "latest-successful-dependency-closure"}

    @app.post("/api/projects/{project_id}/plan")
    def configured_plan(project_id: str, body: PlanBody, user: str = Depends(principal)):
        document = store.read_archive(user, project_id, body.archive_id)["archive"]
        try:
            selected_registry = configured_registry(body.image_config, registry)
        except ValueError:
            raise HTTPException(422, "Invalid image provider configuration") from None
        return replay_plan(document, selected_registry, lambda key: store.cache_get(project_id, key),
                           lambda sha256: store.read_blob(project_id, sha256), body.stage)

    @app.post("/api/projects/{project_id}/executions", status_code=202)
    def execute(project_id: str, body: ExecutionBody, user: str = Depends(principal)):
        require(body.record is True and body.execute is True, "Execution and recording require explicit consent",
                "not_authorized")
        require(manager is not None, "Gateway lifecycle has not started", "service_unavailable")
        try:
            selected_registry = configured_registry(body.image_config, registry)
            identity = body.image_config.public_identity() if body.image_config else None
        except ValueError:
            raise HTTPException(422, "Invalid image provider configuration") from None
        payload = body.model_dump(exclude={"image_config"})
        if identity:
            payload["provider_identity"] = identity
        result = manager.submit(user, project_id, payload, registry=selected_registry)
        return result

    @app.get("/api/projects/{project_id}/executions")
    def executions(project_id: str, user: str = Depends(principal), offset: int = Query(0, ge=0),
                   limit: int = Query(50, ge=1, le=200)):
        result = store.list_jobs(user, project_id, offset, limit)
        return result

    @app.get("/api/projects/{project_id}/executions/{job_id}")
    def execution(project_id: str, job_id: str, user: str = Depends(principal)):
        return {"status": "success", "execution": store.read_job(user, project_id, job_id)}

    @app.post("/api/projects/{project_id}/executions/{job_id}/cancel")
    def cancel(project_id: str, job_id: str, user: str = Depends(principal)):
        result = store.request_cancel(user, project_id, job_id)
        return result

    return app
