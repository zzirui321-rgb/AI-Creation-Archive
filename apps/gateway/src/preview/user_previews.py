import hashlib
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import ArtifactStore, ExecutionContext, PreviewEngine
from .contracts import PreviewError, digest
from .request_config import ImageConfig


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    prompt: str = Field(min_length=1, max_length=20000)
    kind: Literal["image"] = "image"
    stage: Literal["draft", "final"] = "draft"
    params: dict = Field(default_factory=dict)
    image_config: ImageConfig


def attach_user_previews(app, directory, tokens):
    store = ArtifactStore(Path(directory) / "node-previews")

    def authorize(authorization: str = Header(default="")):
        candidate = hashlib.sha256(authorization.encode()).digest()
        if not any(
            secrets.compare_digest(candidate, hashlib.sha256(("Bearer " + value).encode()).digest())
            for value in tokens.values()
        ):
            raise HTTPException(401, "Platform authentication required")

    @app.post("/api/previews")
    async def preview(body: PreviewBody, authorization: str = Header(default="")):
        authorize(authorization)
        try:
            settings = body.image_config.settings()
        except ValueError:
            raise HTTPException(422, "Invalid image provider configuration") from None
        node = {
            "id": body.node_id,
            "kind": "model",
            "label": body.node_id,
            "branch_id": "main",
            "provenance": {"mode": "recorded", "evidence": [{"source": "explicit-user-preview"}]},
            "execution": {
                "kind": "model",
                "tool": "image_gen",
                "version": "b-generic-3",
                "inputs": {"prompt": {"kind": "literal", "value": body.prompt}},
                "params": body.params,
                "outputs": ["image"],
            },
        }
        try:
            result = await PreviewEngine(store, settings=settings).execute_node(
                node,
                {},
                context=ExecutionContext(
                    run_id="preview-" + secrets.token_hex(12),
                    cache_key=digest({"project": body.project_id, "node": body.node_id}),
                    mode=body.stage,
                    resolved_inputs={"prompt": body.prompt},
                ),
            )
        except PreviewError:
            raise HTTPException(502, "Image generation failed; no model artifact was returned") from None
        artifact = result.artifacts[0]
        return {
            "artifact": {
                "url": "/runtime-api/preview-artifacts/" + artifact["sha256"],
                "mime": artifact["mime_type"],
                "sha256": artifact["sha256"],
            },
            "metadata": result.metadata,
        }

    @app.get("/api/preview-artifacts/{sha256}")
    def image(sha256: str, authorization: str = Header(default="")):
        authorize(authorization)
        if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
            raise HTTPException(404, "Artifact not found")
        paths = list((store.root / "objects" / sha256[:2]).glob(sha256 + ".*"))
        if len(paths) != 1 or hashlib.sha256(paths[0].read_bytes()).hexdigest() != sha256:
            raise HTTPException(404, "Artifact unavailable or corrupt")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(paths[0].suffix)
        if mime is None:
            raise HTTPException(404, "Unsupported preview type")
        return FileResponse(
            paths[0],
            media_type=mime,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-AiCA-SHA256": sha256,
            },
        )
