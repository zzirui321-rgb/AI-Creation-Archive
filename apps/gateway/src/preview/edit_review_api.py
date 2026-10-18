"""Loopback-only review assembly, separate from D's authenticated production routes."""

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .artifact_store import ArtifactStore, atomic_write
from .contracts import ExecutionContext, PreviewError, digest
from .engine import PreviewEngine
from .request_config import ImageConfig
from .scene_layers import decode
from .semantic_edit import EditIntent, HTTPSegmenter


class ProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    intent: EditIntent


class ExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    proposal_id: str
    candidate_id: str | None = None
    stage: Literal["draft", "final"] = "draft"
    new_content: bool = False
    image_config: ImageConfig


class ImageBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    image_base64: str = Field(max_length=28 * 1024 * 1024)


def create_review_app(directory, *, segmenter=None):
    app = FastAPI(title="AiCA B semantic edit review / local only")
    store = ArtifactStore(Path(directory) / "artifacts")
    segmenter = segmenter or HTTPSegmenter()

    def remember(artifact):
        atomic_write(store.root / "review-images" / (artifact["id"] + ".json"), json.dumps(artifact).encode())

    def source(identifier):
        if not re.fullmatch(r"artifact-[a-f0-9]{32}", identifier):
            raise HTTPException(404, "Source image unavailable")
        try:
            return json.loads((store.root / "review-images" / (identifier + ".json")).read_bytes())
        except (OSError, ValueError) as error:
            raise HTTPException(404, "Source image unavailable") from error

    def public_artifact(artifact):
        return {**artifact, "uri": "/edit-api/artifacts/" + artifact["sha256"], "locations": []}

    async def read_body(request, schema):
        parts, size = [], 0
        async for part in request.stream():
            size += len(part)
            if size > 28 * 1024 * 1024:
                raise HTTPException(413, "Review request exceeds byte limit")
            parts.append(part)
        try:
            return schema.model_validate_json(b"".join(parts))
        except ValidationError as error:
            raise HTTPException(422, "Invalid review request") from error

    @app.middleware("http")
    async def origin_and_errors(request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:5177", "http://localhost:5177", "http://127.0.0.1:8102"}:
            return JSONResponse({"detail": "Origin is not allowed for local review"}, status_code=403)
        try:
            return await call_next(request)
        except PreviewError as error:
            return JSONResponse({"code": error.code, "detail": str(error)}, status_code=409)

    @app.get("/edit-api/health")
    def health():
        return {"status": "ready", "scope": "local B review; not a platform archive"}

    @app.post("/edit-api/images")
    async def image(request: Request):
        body = await read_body(request, ImageBody)
        try:
            data = base64.b64decode(body.image_base64, validate=True)
            decode(data)
        except (binascii.Error, ValueError) as error:
            raise HTTPException(422, "Invalid image") from error
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as opened:
            mime = Image.MIME.get(opened.format)
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(422, "Use a PNG, JPEG or WebP image")
        artifact = store.put(data, mime, node_id=None, run_id=None)
        remember(artifact)
        return {"artifact": public_artifact(artifact)}

    @app.post("/edit-api/proposals")
    async def propose(request: Request):
        body = await read_body(request, ProposalBody)
        proposal = await PreviewEngine(store, segmenter=segmenter).propose_image_edit(
            source(body.source_id), body.intent
        )
        return {
            **proposal,
            "candidates": [
                {**item, "mask_artifact": public_artifact(item["mask_artifact"])} for item in proposal["candidates"]
            ],
        }

    @app.post("/edit-api/execute")
    async def execute(request: Request):
        body = await read_body(request, ExecuteBody)
        original = source(body.source_id)
        try:
            settings = body.image_config.settings()
        except ValueError as error:
            raise HTTPException(422, "Invalid image provider settings") from error
        node = {
            "id": "full-image",
            "label": "Local image edit review",
            "kind": "model",
            "branch_id": "main",
            "provenance": {"mode": "recorded", "evidence": [{"source": "explicit-local-edit-review"}]},
            "execution": {
                "kind": "model",
                "tool": "image_gen",
                "version": "b-generic-3",
                "inputs": {},
                "params": {"seed": 73, "edit_reference": not body.new_content},
                "outputs": ["image"],
            },
        }
        result = await PreviewEngine(store, settings=settings, segmenter=segmenter).execute_proposed_edit(
            node,
            {original["id"]: original},
            context=ExecutionContext(
                run_id="review-" + __import__("uuid").uuid4().hex,
                cache_key=digest({"source": original["sha256"], "node": node["id"]}),
                mode=body.stage,
                seed=73,
            ),
            original_artifact_id=original["id"],
            proposal_id=body.proposal_id,
            candidate_id=body.candidate_id,
        )
        artifact = result.artifacts[0]
        remember(artifact)
        metadata = {
            **result.metadata,
            "provider_cache": result.cache,
            "edit_proposal": {
                **result.metadata["edit_proposal"],
                "mask_artifact": public_artifact(result.metadata["edit_proposal"]["mask_artifact"]),
            },
        }
        return {"artifact": public_artifact(artifact), "metadata": metadata}

    @app.get("/edit-api/artifacts/{sha256}")
    def artifact(sha256: str):
        if not re.fullmatch("[a-f0-9]{64}", sha256):
            raise HTTPException(404, "Artifact unavailable")
        paths = list((store.root / "objects" / sha256[:2]).glob(sha256 + ".*"))
        if len(paths) != 1 or hashlib.sha256(paths[0].read_bytes()).hexdigest() != sha256:
            raise HTTPException(404, "Artifact unavailable")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(paths[0].suffix)
        if mime is None:
            raise HTTPException(404, "Not a review image")
        return FileResponse(
            paths[0], media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )

    return app
