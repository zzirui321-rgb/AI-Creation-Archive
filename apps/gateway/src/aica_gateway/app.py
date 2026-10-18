import hashlib
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from aica_mcp.files import ProjectLock, private_directory
from aica_mcp.transfer import BUNDLE_LIMIT, CONTENT_TYPE, read_portable_json
from aica_runtime.deepseek import DeepSeekProvider
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import ProtocolError, dumps, loads, require
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .execution_proxy import ExecutionWorkers, attach_execution
from .frontend_v2 import FrontendV2, attach_frontend_v2
from .graphs import GraphService
from .platform import Platform, platform_router
from .repository import Repository
from .rodin import Rodin, attach_rodin
from .semantics import SemanticService


class NewProject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    import_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    node_ids: list[str] | None = Field(default=None, max_length=50)
    allow_external_processing: bool


class GraphInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    import_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    node_ids: list[str] | None = Field(default=None, max_length=200)


class GraphParseRequest(GraphInputRequest):
    request_id: str = Field(min_length=1, max_length=128)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    allow_external_processing: bool


class GraphResponse(BaseModel):
    status: Literal["success"]
    graph: GraphDocument


def create_app(data_dir=None, tokens=None, cors_origins=None, semantic_provider=None, graph_provider=None):
    configured = data_dir if data_dir is not None else os.environ.get("AICA_DATA_DIR")
    require(configured is not None, "AICA_DATA_DIR is required")
    directory = Path(configured).resolve()
    checkout = next((parent for parent in Path(__file__).resolve().parents
                     if (parent / "collaboration.json").is_file()), None)
    require(checkout is None or not directory.is_relative_to(checkout), "Keep platform data outside the checkout")
    tokens = tokens if tokens is not None else loads(os.environ.get("AICA_API_TOKENS", "{}"))
    require(isinstance(tokens, dict)
            and all(isinstance(subject, str) and 0 < len(subject) <= 128
                    and not subject.startswith("user:") and isinstance(token, str) and 32 <= len(token) <= 512
                    and all(33 <= ord(char) <= 126 for char in token) for subject, token in tokens.items())
            and len(set(tokens.values())) == len(tokens), "Configure distinct platform tokens of 32-512 ASCII characters")
    digests = {subject: hashlib.sha256(token.encode()).digest() for subject, token in tokens.items()}
    origins = cors_origins if cors_origins is not None else loads(os.environ.get("AICA_CORS_ORIGINS", "[]"))
    require(isinstance(origins, list) and all(isinstance(origin, str) and origin.startswith(("http://", "https://"))
                                            and "*" not in origin for origin in origins), "Use explicit CORS origins")

    @asynccontextmanager
    async def lifespan(app):
        private_directory(directory)
        lock = ProjectLock(directory / "gateway.lock")
        lock.acquire()
        try:
            app.state.repository = await run_in_threadpool(Repository, directory)
            app.state.platform = await run_in_threadpool(Platform, app.state.repository)
            app.state.frontend_v2 = await run_in_threadpool(FrontendV2, app.state.platform)
            app.state.rodin = await run_in_threadpool(Rodin, app.state.platform, app.state.frontend_v2)
            app.state.semantics = await run_in_threadpool(SemanticService, app.state.repository,
                                                         semantic_provider or DeepSeekProvider())
            app.state.graphs = await run_in_threadpool(GraphService, app.state.repository, graph_provider or GraphProvider(),
                                                     publisher=app.state.platform)
            app.state.execution = ExecutionWorkers(directory)
            try:
                yield
            finally:
                await app.state.rodin.close()
                await app.state.frontend_v2.close()
                await run_in_threadpool(app.state.execution.close)
                await run_in_threadpool(app.state.graphs.close)
        finally:
            lock.close()

    app = FastAPI(title="AiCA Graph platform API", version="0.1.0", lifespan=lifespan)
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-AiCA-Bundle-SHA256"],
                           expose_headers=["X-AiCA-SHA256", "Content-Disposition"])
    bearer = HTTPBearer(auto_error=False)

    def optional_principal(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None and "authorization" not in request.headers:
            return None
        if credentials is None or len(credentials.credentials) > 512:
            raise HTTPException(401, "Platform authentication required", headers={"WWW-Authenticate": "Bearer"})
        supplied = hashlib.sha256(credentials.credentials.encode()).digest()
        subject = next((subject for subject, expected in digests.items() if secrets.compare_digest(supplied, expected)), None)
        if subject is None:
            subject = request.app.state.platform.resolve(credentials.credentials)
        if subject is None:
            subject = request.app.state.platform.resolve_upload(credentials.credentials, request.url.path, request.method)
        if subject is None:
            raise HTTPException(401, "Platform authentication required", headers={"WWW-Authenticate": "Bearer"})
        return subject

    def principal(subject: str | None = Depends(optional_principal)):
        if subject is None:
            raise HTTPException(401, "Platform authentication required", headers={"WWW-Authenticate": "Bearer"})
        return subject

    @app.middleware("http")
    async def platform_limits(request: Request, call_next):
        if request.method in {"POST", "PATCH"} and request.url.path.startswith(("/api/auth/", "/api/repos")):
            maximum = 16384 if request.url.path.startswith("/api/auth/") else None
            data = bytearray()
            async for chunk in request.stream():
                if maximum is not None and len(data) + len(chunk) > maximum:
                    return JSONResponse({"status": "error", "code": "transfer_limit", "message": "Request too large"},
                                        status_code=413, headers={"Cache-Control": "no-store"})
                data.extend(chunk)
            request._body = bytes(data)
            if data:
                try:
                    dumps(loads(bytes(data).decode("utf-8")))
                except (ProtocolError, UnicodeError):
                    return JSONResponse({"status": "error", "code": "invalid_request", "message": "Invalid JSON request"},
                                        status_code=422, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    app.include_router(platform_router(principal, optional_principal))
    attach_execution(app, principal)
    attach_frontend_v2(app, principal, optional_principal)
    attach_rodin(app, principal, optional_principal)

    def repository(request: Request):
        return request.app.state.repository

    @app.exception_handler(ProtocolError)
    async def contract_error(request: Request, error: ProtocolError):
        status = {"not_found": 404, "id_conflict": 409, "project_busy": 409, "transfer_limit": 413,
                  "analysis_input_conflict": 409, "analysis_limit": 413, "not_authorized": 403,
                  "storage_corrupt": 503, "artifact_unavailable": 503, "provider_unavailable": 503,
                  "provider_connection": 503, "provider_rejected": 502, "invalid_analysis": 502,
                  "analysis_interrupted": 503}.get(error.code, 422)
        return JSONResponse({"status": "error", "code": error.code, "message": str(error)}, status_code=status)

    @app.exception_handler(RequestValidationError)
    async def input_error(request: Request, error: RequestValidationError):
        return JSONResponse({"status": "error", "code": "invalid_request", "message": "Invalid request parameters"},
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        return JSONResponse({"status": "error", "code": "http_error", "message": str(error.detail)},
                            status_code=error.status_code, headers=error.headers)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(request: Request, error: Exception):
        return JSONResponse({"status": "error", "code": "storage_error", "message": "Platform storage unavailable"},
                            status_code=503)

    @app.get("/health")
    def health():
        return {"status": "success", "service": "aica-gateway"}

    @app.get("/api/platform/ca-certificate")
    def ca_certificate():
        certificate = directory.parent / "tls" / "ca.pem"
        if not certificate.is_file():
            raise HTTPException(404, "LAN CA has not been configured")
        return FileResponse(certificate, media_type="application/x-pem-file", filename="aica-lan-ca.pem")

    @app.post("/api/projects", status_code=201)
    def new_project(body: NewProject, subject: str = Depends(principal), store: Repository = Depends(repository)):
        return {"status": "success", "project": store.create_project(subject, body.title)}

    @app.get("/api/projects")
    def list_projects(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      subject: str = Depends(principal), store: Repository = Depends(repository)):
        return {"status": "success", "projects": store.projects(subject, offset, limit)}

    @app.post("/api/projects/{project_id}/imports")
    @app.post("/api/projects/{project_id}/browser-imports")
    async def upload(project_id: UUID, request: Request, subject: str = Depends(principal),
                     store: Repository = Depends(repository)):
        project_id = str(project_id)
        await run_in_threadpool(store.authorize, subject, project_id)
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        require(content_type in {CONTENT_TYPE, "application/json"},
                "Expected an AiCA transfer bundle")
        require(request.headers.get("content-encoding", "identity").lower() == "identity", "Unsupported encoding")
        expected = request.headers.get("x-aica-bundle-sha256", "")
        key = request.headers.get("idempotency-key", "")
        browser_upload = request.url.path.endswith("/browser-imports")
        require(browser_upload or re.fullmatch(r"[a-f0-9]{64}", expected) is not None and key == "sha256:" + expected,
                "Matching bundle hash and idempotency headers are required")
        length = request.headers.get("content-length")
        require(length is None or len(length) <= 20 and length.isascii() and length.isdecimal(), "Invalid Content-Length")
        require(length is None or int(length) <= BUNDLE_LIMIT, "Upload exceeds bundle limit", "transfer_limit")
        descriptor, temporary = tempfile.mkstemp(dir=directory / "uploads")
        path = Path(temporary)
        try:
            size = 0
            received_hash = hashlib.sha256()
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    received_hash.update(chunk)
                    require(size <= BUNDLE_LIMIT, "Upload exceeds bundle limit", "transfer_limit")
                    await run_in_threadpool(stream.write, chunk)
                await run_in_threadpool(stream.flush)
                await run_in_threadpool(os.fsync, stream.fileno())
            require(length is None or size == int(length), "Content-Length differs from received bytes")
            if browser_upload:
                expected = received_hash.hexdigest()
                key = "sha256:" + expected
            if content_type == "application/json":
                await run_in_threadpool(read_portable_json, path)
                content = await run_in_threadpool(path.read_bytes)
                receipt, created = await run_in_threadpool(store.ingest_json, subject, project_id, content, expected, key)
            else:
                receipt, created = await run_in_threadpool(store.ingest, subject, project_id, path, expected, key)
            return JSONResponse(receipt, status_code=201 if created else 200)
        finally:
            path.unlink(missing_ok=True)

    @app.post("/api/projects/{project_id}/archive-json")
    async def upload_archive_json(project_id: UUID, request: Request, subject: str = Depends(principal),
                                  store: Repository = Depends(repository)):
        await run_in_threadpool(store.authorize, subject, str(project_id))
        require(request.headers.get("content-type", "").split(";")[0].strip().lower() == "application/json",
                "Expected an AiCA archive JSON")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
        receipt, created = await run_in_threadpool(store.ingest_json, subject, str(project_id), bytes(content))
        return JSONResponse(receipt, status_code=201 if created else 200)

    @app.get("/api/projects/{project_id}/archives")
    def list_archives(project_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                      subject: str = Depends(principal), store: Repository = Depends(repository)):
        return {"status": "success", "archives": store.imports(subject, str(project_id), offset, limit)}

    @app.get("/api/projects/{project_id}/archive")
    def read_archive(project_id: UUID, import_id: UUID | None = None, subject: str = Depends(principal),
                     store: Repository = Depends(repository)):
        snapshot = store.snapshot(subject, str(project_id), str(import_id) if import_id else None)
        return {"status": "success", **{key: value for key, value in snapshot.items() if key != "manifest"}}

    @app.get("/api/projects/{project_id}/graph")
    def graph(project_id: UUID, import_id: UUID | None = None, subject: str = Depends(principal),
              store: Repository = Depends(repository)):
        snapshot = store.snapshot(subject, str(project_id), str(import_id) if import_id else None)
        fields = ("graph", "variables", "interactive_questions", "artifacts", "runs", "snapshot")
        return {"status": "success", "import_id": snapshot["import_id"],
                **{field: snapshot["archive"][field] for field in fields}}

    @app.get("/api/projects/{project_id}/artifacts/{artifact_id}")
    def artifact(project_id: UUID, artifact_id: str, import_id: UUID | None = None,
                 subject: str = Depends(principal), store: Repository = Depends(repository)):
        record, path = store.artifact(subject, str(project_id), artifact_id, str(import_id) if import_id else None)
        safe_images = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}
        inline = record["mime_type"] in safe_images
        return FileResponse(path, media_type=record["mime_type"] if inline else "application/octet-stream",
                            filename="artifact-" + record["sha256"],
                            content_disposition_type="inline" if inline else "attachment",
                            headers={"X-AiCA-SHA256": record["sha256"], "X-Content-Type-Options": "nosniff",
                                     "Content-Security-Policy": "sandbox; default-src 'none'",
                                     "Cache-Control": "private, no-store"})

    @app.get("/api/semantic-provider")
    def provider_info(request: Request, subject: str = Depends(principal)):
        return {"status": "success", **request.app.state.semantics.provider.describe()}

    @app.get("/api/projects/{project_id}/analysis-input")
    def analysis_input(project_id: UUID, import_id: UUID, request: Request,
                       node_ids: list[str] | None = Query(None), subject: str = Depends(principal)):
        prepared = request.app.state.semantics.prepare(subject, str(project_id), str(import_id), node_ids)
        return {"status": "success", "input": prepared}

    @app.post("/api/projects/{project_id}/analyses")
    def analyze(project_id: UUID, body: AnalysisRequest, request: Request, subject: str = Depends(principal)):
        result, status = request.app.state.semantics.analyze(subject, str(project_id), body.model_dump())
        return JSONResponse({"status": "success", "analysis": result}, status_code=status)

    @app.get("/api/projects/{project_id}/analyses")
    def analyses(project_id: UUID, request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 subject: str = Depends(principal)):
        return {"status": "success", "analyses": request.app.state.semantics.list(subject, str(project_id), offset, limit)}

    @app.get("/api/projects/{project_id}/analyses/{analysis_id}")
    def analysis_detail(project_id: UUID, analysis_id: UUID, request: Request, subject: str = Depends(principal)):
        return {"status": "success", "analysis": request.app.state.semantics.detail(subject, str(project_id), str(analysis_id))}

    @app.get("/api/graph-parser")
    def graph_provider_info(request: Request, subject: str = Depends(principal)):
        return {"status": "success", **request.app.state.graphs.provider.describe()}

    @app.post("/api/projects/{project_id}/semantic-parse-input")
    def graph_input(project_id: UUID, body: GraphInputRequest, request: Request, subject: str = Depends(principal)):
        context = request.app.state.graphs.prepare(subject, str(project_id), body.import_id, body.node_ids)
        return {"status": "success", "input": context.prepared}

    @app.post("/api/projects/{project_id}/semantic-parses")
    def graph_parse(project_id: UUID, body: GraphParseRequest, request: Request, subject: str = Depends(principal)):
        job, status = request.app.state.graphs.submit(subject, str(project_id), body.model_dump())
        return JSONResponse({"status": "success", "parse": job}, status_code=status)

    @app.get("/api/projects/{project_id}/semantic-parses")
    def graph_parses(project_id: UUID, request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                     subject: str = Depends(principal)):
        return {"status": "success", "parses": request.app.state.graphs.list(subject, str(project_id), offset, limit)}

    @app.get("/api/projects/{project_id}/semantic-parses/{parse_id}")
    def graph_parse_detail(project_id: UUID, parse_id: UUID, request: Request, subject: str = Depends(principal)):
        return {"status": "success", "parse": request.app.state.graphs.detail(subject, str(project_id), str(parse_id))}

    @app.post("/api/projects/{project_id}/semantic-parses/{parse_id}/repository")
    def save_graph_parse(project_id: UUID, parse_id: UUID, request: Request, subject: str = Depends(principal)):
        request.app.state.graphs.detail(subject, str(project_id), str(parse_id))
        publication = request.app.state.platform.publish_parse(str(parse_id), subject)
        require(publication is not None, "A registered platform account is required", "not_authorized")
        return {"status": "success", "publication": publication}

    @app.get("/api/projects/{project_id}/semantic-graphs/{graph_id}", response_model=GraphResponse)
    def semantic_graph(project_id: UUID, graph_id: UUID, request: Request, revision_id: UUID = Query(...),
                       subject: str = Depends(principal)):
        graph = request.app.state.graphs.graph(subject, str(project_id), str(graph_id), str(revision_id))
        return {"status": "success", "graph": graph.model_dump()}

    @app.get("/api/projects/{project_id}/semantic-graphs/{graph_id}/evidence/{evidence_id}")
    def graph_evidence(project_id: UUID, graph_id: UUID, evidence_id: str, request: Request, revision_id: UUID = Query(...),
                       subject: str = Depends(principal)):
        return {"status": "success", **request.app.state.graphs.evidence(
            subject, str(project_id), str(graph_id), str(revision_id), evidence_id)}

    web_dir = os.environ.get("AICA_WEB_DIR")
    if web_dir:
        web = Path(web_dir).resolve()
        require((web / "index.html").is_file() and (web / "assets").is_dir(), "AICA_WEB_DIR must contain a built frontend")
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    return app
