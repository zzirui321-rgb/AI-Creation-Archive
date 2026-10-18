from __future__ import annotations

import asyncio
import base64
import binascii
import io
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from ..artifact_store import ArtifactStore
from ..consistency import ensure_reference_consistency_context
from ..contracts import AdapterOutput, ExecutionRequest, PreviewError, PreviewSettings
from .base import PreviewProvider
from .prompt import image_prompt


def checked_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise PreviewError("invalid_api_url", "Expected an HTTP(S) image endpoint without embedded credentials")
    return value


class LiveAPIProvider(PreviewProvider):
    source = "live_api"

    def __init__(self, settings: PreviewSettings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def generate(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput | None:
        if not self.settings.api_key or not self.settings.api_base:
            return None
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                async with httpx.AsyncClient(
                    timeout=self.settings.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    return await self._generate(client, request, store)
        except (
            TimeoutError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            PreviewError,
            binascii.Error,
        ) as error:
            code = "timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else "provider_error"
            if isinstance(error, httpx.HTTPStatusError):
                status = error.response.status_code
                detail = "http_error"
                try:
                    body = error.response.json()
                    candidate = body.get("error", {}).get("code") if isinstance(body, dict) else None
                    if (
                        isinstance(candidate, str)
                        and candidate
                        and all(char.isalnum() or char in "_-" for char in candidate)
                    ):
                        detail = candidate[:80]
                except (ValueError, TypeError):
                    pass
                code = f"provider_http_{status}.{detail}"
            raise PreviewError(code, "Image service unavailable; using configured fallback") from error

    async def _generate(
        self, client: httpx.AsyncClient, request: ExecutionRequest, store: ArtifactStore
    ) -> AdapterOutput:
        settings, profile = self.settings, request.profile
        base = checked_url(settings.api_base).rstrip("/")
        style = settings.api_style
        if style == "auto":
            host = urlparse(base).hostname or ""
            style = "fal" if host.endswith("fal.run") else "siliconflow" if "siliconflow" in host else "openai"
        if not profile.model:
            raise PreviewError("missing_model", "Configure a stage model for live image generation")
        prompt = image_prompt(request)
        references = request.inputs.get("reference_artifact_ids", [])
        if isinstance(references, str):
            references = [references]
        if not isinstance(references, list):
            raise PreviewError("invalid_reference", "reference_artifact_ids must be a list")
        _, derived_consistency = ensure_reference_consistency_context(request.inputs, request.artifacts)
        node_params = request.node.get("execution", {}).get("params", {})
        edit_params = {**node_params, **profile.params}
        edit = None
        if edit_params.get("edit_mode") == "masked_replace":
            from ..image_edit import resolve_image_edit

            edit = resolve_image_edit(request.inputs, edit_params, request.artifacts, store)
            if style == "openai":
                references = [edit.original_id] if edit_params.get("edit_reference", True) else []
            elif not references and edit_params.get("edit_reference", True):
                references = [edit.original_id]
        reference_images = []
        for identifier in references:
            artifact = request.artifacts[identifier]
            if not artifact["mime_type"].startswith("image/"):
                raise PreviewError("invalid_reference", "Reference artifacts must be images")
            data = await asyncio.to_thread(store.read, artifact)
            reference_images.append("data:" + artifact["mime_type"] + ";base64," + base64.b64encode(data).decode())
        if reference_images and style == "siliconflow":
            raise PreviewError("unsupported_reference", "This SiliconFlow adapter does not support reference images")
        headers = {"Authorization": ("Key " if style == "fal" else "Bearer ") + settings.api_key}
        extra = {
            key: value
            for key, value in profile.params.items()
            if key
            not in {
                "width",
                "height",
                "steps",
                "seed",
                "layout",
                "title",
                "captions",
                "layer_role",
                "matting",
                "edit_mode",
                "edit_region",
                "edit_reference",
                "edit_operation",
                "edit_target",
            }
        }
        if style == "qwen":
            endpoint = (
                base if base.endswith("/generation") else base + "/services/aigc/multimodal-generation/generation"
            )
            content = [{"image": value} for value in reference_images]
            content.append({"text": prompt})
            parameters = {**extra, "seed": profile.seed, "n": 1}
            if not reference_images:
                parameters["size"] = f"{profile.width}*{profile.height}"
            payload = {
                "model": profile.model,
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": parameters,
            }
        elif style == "sdcpp":
            if len(reference_images) > 1:
                raise PreviewError("unsupported_reference", "This local provider supports one reference image")
            if reference_images:
                parsed = urlparse(base)
                endpoint = f"{parsed.scheme}://{parsed.netloc}/v1/img2img"
                payload = {
                    **extra,
                    "prompt": prompt,
                    "width": profile.width,
                    "height": profile.height,
                    "seed": profile.seed,
                    "steps": profile.params["steps"],
                    "ref_image": reference_images[0].split(",", 1)[1],
                }
            else:
                endpoint = base if base.endswith("/generations") else base + "/images/generations"
                payload = {
                    **extra,
                    "model": profile.model,
                    "prompt": prompt,
                    "seed": profile.seed,
                    "size": f"{profile.width}x{profile.height}",
                    "steps": profile.params["steps"],
                    "n": 1,
                    "response_format": "b64_json",
                }
        elif style == "fal":
            endpoint = base if urlparse(base).path not in {"", "/"} else base + "/" + profile.model
            payload = {
                **extra,
                "prompt": prompt,
                "seed": profile.seed,
                "num_inference_steps": profile.params["steps"],
                "image_size": {"width": profile.width, "height": profile.height},
                "num_images": 1,
            }
            if reference_images:
                payload["image_urls"] = reference_images
        else:
            endpoint = base if base.endswith(("/generations", "/edits")) else base + "/images/generations"
            if edit is not None and references and style == "openai":
                endpoint = (
                    endpoint.removesuffix("/generations") + "/edits" if endpoint.endswith("/generations") else endpoint
                )
            payload = {
                **extra,
                "model": profile.model,
                "prompt": prompt,
                "seed": profile.seed,
                "size": f"{profile.width}x{profile.height}",
                "n": 1,
            }
            if style == "siliconflow":
                payload.update(image_size=payload.pop("size"), num_inference_steps=profile.params["steps"])
            if reference_images and not (edit is not None and style == "openai"):
                # Reference support requires an explicitly configured compatible edit endpoint.
                if not endpoint.endswith("/edits"):
                    raise PreviewError("unsupported_reference", "Configure an image edit endpoint to send references")
                payload["images"] = [{"image_url": value} for value in reference_images]
        if edit is not None and references and style == "openai":
            from ..scene_layers import png

            # OpenAI edit masks use transparency for editable pixels, the inverse of B's mask.
            mask = Image.new("RGBA", edit.original.size, "white")
            mask.putalpha(edit.mask.point(lambda value: 255 - value))
            response = await client.post(
                endpoint,
                headers=headers,
                data={"model": profile.model, "prompt": prompt, "n": "1", "size": f"{profile.width}x{profile.height}"},
                files={
                    "image": ("reference.png", png(edit.original), "image/png"),
                    "mask": ("mask.png", png(mask), "image/png"),
                },
            )
        else:
            response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise PreviewError("invalid_response", "Image API response must be an object")
        if style == "fal" and body.get("status_url"):
            status_url, response_url = body["status_url"], body["response_url"]
            if any(urlparse(url).netloc != urlparse(endpoint).netloc for url in (status_url, response_url)):
                raise PreviewError("invalid_poll_url", "Fal polling URLs must remain on the configured API host")
            while True:
                status = await client.get(checked_url(status_url), headers=headers)
                status.raise_for_status()
                state = status.json().get("status")
                if state == "COMPLETED":
                    response = await client.get(checked_url(response_url), headers=headers)
                    response.raise_for_status()
                    body = response.json()
                    break
                if state in {"FAILED", "CANCELLED"}:
                    raise PreviewError("provider_failed", "Fal image task did not complete")
                await asyncio.sleep(0.2)
        if not isinstance(body, dict):
            raise PreviewError("invalid_response", "Image API result must be an object")
        if style == "qwen":
            items = [
                {"url": part["image"]}
                for choice in body.get("output", {}).get("choices", [])
                for part in choice.get("message", {}).get("content", [])
                if "image" in part
            ]
        elif style == "sdcpp" and body.get("image"):
            items = [{"b64_json": body["image"]}]
        else:
            items = body.get("data") or body.get("images")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise PreviewError("invalid_response", "Image API response contains no image")
        item = items[0]
        if item.get("b64_json"):
            encoded = item["b64_json"]
            if len(encoded) > settings.max_download_bytes * 4 // 3 + 8:
                raise PreviewError("oversized_image", "Image response exceeds byte limit")
            data = base64.b64decode(encoded, validate=True)
        else:
            data = await self._download(client, checked_url(item["url"]))
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 16_777_216:
                raise PreviewError("oversized_image", "Image response exceeds pixel limit")
            mime = Image.MIME.get(image.format or "")
            actual_width, actual_height = image.size
            image.verify()
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise PreviewError("invalid_image", "Image service returned an unsupported image")
        return AdapterOutput(
            data,
            mime,
            self.source,
            {
                "is_substitute": False,
                "api_style": style,
                "resolved_prompt": prompt,
                "reference_artifact_ids": references,
                **(
                    {"consistency_context": request.inputs.get("consistency_context", derived_consistency)}
                    if derived_consistency or "consistency_context" in request.inputs
                    else {}
                ),
                "actual_width": actual_width,
                "actual_height": actual_height,
                **(
                    {
                        "edit_transport": "generation_then_masked_blend"
                        if not references
                        else "native_mask"
                        if style == "openai"
                        else "reference_then_masked_blend"
                    }
                    if edit
                    else {}
                ),
            },
        )

    async def _download(self, client: httpx.AsyncClient, url: str) -> bytes:
        chunks, size = [], 0
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.settings.max_download_bytes:
                    raise PreviewError("oversized_image", "Image download exceeds byte limit")
                chunks.append(chunk)
        return b"".join(chunks)
