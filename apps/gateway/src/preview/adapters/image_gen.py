from __future__ import annotations

import asyncio

import httpx

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest, PreviewError, PreviewSettings
from ..providers import LiveAPIProvider, LocalFallbackProvider, PreviewProvider, PrewarmedProvider
from ..scene_layers import prepare_image_output
from .base import BaseAdapter


class ImageGenAdapter(BaseAdapter):
    version = "6"

    def __init__(self, settings: PreviewSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        prewarmed, live, local = PrewarmedProvider(), LiveAPIProvider(settings, transport), LocalFallbackProvider()
        self.providers: list[PreviewProvider] = (
            [local]
            if settings.provider in {"mock_svg", "local_fallback"}
            else [live, local]
            if settings.provider == "live_api"
            else [prewarmed, live, local]
        )
        if not settings.allow_fallback and settings.provider not in {"mock_svg", "local_fallback"}:
            self.providers = [provider for provider in self.providers if provider.source != "local_fallback"]

    async def execute(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput:
        if (
            request.node["execution"].get("params", {}).get("layer_role") == "detail"
            and not str(request.inputs.get("prompt", "")).strip()
        ):
            from PIL import Image

            from ..scene_layers import png

            return AdapterOutput(
                png(Image.new("RGBA", (request.profile.width, request.profile.height))),
                "image/png",
                "local_materialize",
                {"is_substitute": False, "layer_role": "detail", "layer_prepared": True, "empty_layer": True},
            )
        reasons: list[str] = []
        for provider in self.providers:
            cached = (
                None
                if provider.source == "prewarmed_cache"
                else await asyncio.to_thread(store.load_cache, request, provider.source)
            )
            if cached is not None:
                if "reference_artifact_ids" in cached.metadata:
                    references = request.inputs.get("reference_artifact_ids", [])
                    if request.profile.params.get("edit_mode") == "masked_replace" and request.profile.params.get(
                        "edit_reference", True
                    ):
                        references = [request.inputs["original_image"]]
                    cached.metadata["reference_artifact_ids"] = (
                        [references] if isinstance(references, str) else references
                    )
                cached.metadata = {**cached.metadata, "fallback_reasons": reasons}
                return cached
            try:
                output = await provider.generate(request, store)
                if output is not None:
                    output = await asyncio.to_thread(prepare_image_output, output, request, store)
            except PreviewError as error:
                reasons.append(error.code)
                continue
            if output is not None:
                output.metadata = {**output.metadata, "fallback_reasons": reasons}
                return output
            if provider.source == "live_api":
                reasons.append("live_api_unconfigured")
        raise PreviewError("no_provider", "No image provider could produce an artifact")
