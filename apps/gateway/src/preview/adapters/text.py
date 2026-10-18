import asyncio

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest, PreviewError, canonical_json
from .base import BaseAdapter


class TextArtifactAdapter(BaseAdapter):
    async def execute(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput:
        cached = await asyncio.to_thread(store.load_cache, request, "text_materialize")
        if cached:
            return cached
        execution = request.node["execution"]
        mime = execution.get("params", {}).get(
            "mime_type", "application/json" if execution["tool"] == "json_materialize" else "text/plain"
        )
        if mime not in {
            "text/plain",
            "text/markdown",
            "text/html",
            "application/json",
            "text/x-python",
            "text/javascript",
            "text/css",
        }:
            raise PreviewError("unsupported_mime", "Unsupported text materialization MIME type")
        if "content" not in request.inputs:
            raise PreviewError("missing_content", "text/json materialization requires resolved_inputs.content")
        content = request.inputs["content"]
        if mime == "application/json":
            data = canonical_json(content)
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            raise PreviewError("invalid_content", "Non-JSON text content must be a string")
        return AdapterOutput(data, mime, "text_materialize", {"is_substitute": False})
