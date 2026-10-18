from __future__ import annotations

import asyncio
import io
import math
from typing import Any

from PIL import Image, ImageDraw, ImageOps, PngImagePlugin, UnidentifiedImageError

from ..artifact_store import ArtifactStore
from ..contracts import AdapterOutput, ExecutionRequest, PreviewError
from ..scene_layers import layer_stack
from ..visuals import text_box
from .base import BaseAdapter


def composition_inputs(request: ExecutionRequest) -> list[str]:
    ports = request.node["execution"].get("params", {}).get("layout", {}).get("input_ports")
    if "artifact_ids" in request.inputs:
        return request.inputs["artifact_ids"]
    if ports is None:
        return list(request.artifacts)
    if (
        not isinstance(ports, list)
        or not ports
        or any(not isinstance(port, str) or port not in request.inputs for port in ports)
    ):
        raise PreviewError("invalid_layout", "input_ports must select existing input names")
    result = []
    for port in ports:
        value = request.inputs[port]
        result.extend(value if isinstance(value, list) else [value])
    return result


def compose(request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput:
    params = request.node["execution"].get("params", {})
    layout = params.get("layout", {})
    if not isinstance(layout, dict):
        raise PreviewError("invalid_layout", "layout must be an object")
    if layout.get("mode") in {"layer_stack", "masked_replace"}:
        return layer_stack(request, store)
    identifiers = composition_inputs(request)
    if not isinstance(identifiers, list) or not identifiers or len(identifiers) > 64:
        raise PreviewError("invalid_layout", "artifact_ids must contain between 1 and 64 artifact IDs")
    if any(not isinstance(identifier, str) or identifier not in request.artifacts for identifier in identifiers):
        raise PreviewError("missing_input", "Composition references an unknown artifact")
    arrangement = layout.get("direction", "grid")
    if arrangement not in {"grid", "vertical", "horizontal"}:
        raise PreviewError("invalid_layout", "direction must be grid, vertical or horizontal")
    columns = (
        1
        if arrangement == "vertical"
        else len(identifiers)
        if arrangement == "horizontal"
        else (layout.get("columns", math.ceil(math.sqrt(len(identifiers)))))
    )
    cell_width = layout.get("cell_width", request.profile.width)
    cell_height = layout.get("cell_height", request.profile.height)
    gap, margin = layout.get("gap", 12), layout.get("margin", 20)
    values = (columns, cell_width, cell_height, gap, margin)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise PreviewError("invalid_layout", "Layout dimensions must be integers")
    if not 1 <= columns <= 64 or min(cell_width, cell_height) < 64 or min(gap, margin) < 0:
        raise PreviewError("invalid_layout", "Invalid grid or cell dimensions")
    title = str(request.inputs.get("title", params.get("title", "")))
    header = 76 if title else 36
    caption_height = 64
    rows = math.ceil(len(identifiers) / columns)
    width = margin * 2 + columns * cell_width + (columns - 1) * gap
    height = margin * 2 + header + rows * (cell_height + caption_height) + (rows - 1) * gap
    if width > 16384 or height > 16384 or width * height > 32_000_000:
        raise PreviewError("oversized_layout", "Composite exceeds dimension/pixel limits")
    image = Image.new("RGB", (width, height), layout.get("background", "#f2f4f7"))
    draw = ImageDraw.Draw(image)
    stage = "DRAFT" if request.profile.mode == "draft" else "HQ"
    text_box(
        draw,
        f"{title}\n{stage}" if title else stage,
        (margin, margin, width - margin, margin + header),
        size=20,
        fill="#202333",
        bold=True,
    )
    captions = request.inputs.get("captions", {})
    if not isinstance(captions, (dict, list)):
        raise PreviewError("invalid_layout", "captions must be an object keyed by artifact ID or an ordered list")
    regions: list[dict[str, Any]] = []
    for index, identifier in enumerate(identifiers):
        artifact = request.artifacts[identifier]
        data = store.read(artifact)
        x = margin + (index % columns) * (cell_width + gap)
        y = margin + header + (index // columns) * (cell_height + caption_height + gap)
        mime = artifact["mime_type"]
        if mime in {"image/png", "image/jpeg", "image/webp"}:
            try:
                with Image.open(io.BytesIO(data)) as source:
                    if source.width * source.height > 16_777_216:
                        raise PreviewError("oversized_image", "Composition input exceeds pixel limit")
                    panel = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"), (cell_width, cell_height))
                    image.paste(panel, (x + (cell_width - panel.width) // 2, y + (cell_height - panel.height) // 2))
            except (UnidentifiedImageError, OSError) as error:
                raise PreviewError("invalid_image", f"Cannot decode input {identifier}") from error
        elif mime.startswith("text/") or mime == "application/json":
            text_box(
                draw,
                data.decode("utf-8"),
                (x + 12, y + 12, x + cell_width - 12, y + cell_height - 12),
                size=18,
                fill="#202333",
            )
        else:
            raise PreviewError("unsupported_composition_input", f"Cannot compose {mime}")
        draw.rectangle((x, y, x + cell_width - 1, y + cell_height - 1), outline="#68738a", width=2)
        caption = (
            captions.get(identifier, "")
            if isinstance(captions, dict)
            else (captions[index] if index < len(captions) else "")
        )
        node_id = artifact.get("producer_node_id")
        state = request.context.node_states.get(node_id, "unknown")
        text_box(
            draw,
            str(caption),
            (x + 8, y + cell_height + 6, x + cell_width - 8, y + cell_height + caption_height),
            size=16,
            fill="#202333",
        )
        regions.append(
            {
                "artifact_id": identifier,
                "node_id": node_id,
                "rect": {"x": x, "y": y, "width": cell_width, "height": cell_height},
                "cache_decision": state,
                "prompt": request.context.node_prompts.get(node_id, ""),
                "caption": str(caption),
            }
        )
    buffer = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("stage", request.profile.schema_stage)
    image.save(buffer, "PNG", pnginfo=info)
    return AdapterOutput(
        buffer.getvalue(),
        "image/png",
        "composite_layout",
        {
            "is_substitute": False,
            "regions": regions,
            "canvas": {"width": width, "height": height},
            "input_artifact_ids": identifiers,
            "attachment_artifact_ids": [
                identifier for identifier in request.artifacts if identifier not in identifiers
            ],
            "input_sources": {
                identifier: request.context.artifact_metadata.get(identifier, {}).get("source", "unknown")
                for identifier in identifiers
            },
            "contains_substitute": any(
                request.context.artifact_metadata.get(identifier, {}).get("is_substitute")
                or request.context.artifact_metadata.get(identifier, {}).get("contains_substitute")
                for identifier in identifiers
            ),
        },
    )


class CompositeLayoutAdapter(BaseAdapter):
    version = "3"

    async def execute(self, request: ExecutionRequest, store: ArtifactStore) -> AdapterOutput:
        if request.node["execution"].get("params", {}).get("layout", {}).get("mode") in {
            "layer_stack",
            "masked_replace",
        }:
            return await asyncio.to_thread(compose, request, store)
        cached = await asyncio.to_thread(store.load_cache, request, "composite_layout")
        if cached:
            identifiers = composition_inputs(request)
            regions = cached.metadata.get("regions", [])
            if len(identifiers) != len(regions):
                return await asyncio.to_thread(compose, request, store)
            for region, identifier in zip(regions, identifiers, strict=True):
                node_id = request.artifacts[identifier].get("producer_node_id")
                region.update(
                    artifact_id=identifier,
                    node_id=node_id,
                    cache_decision=request.context.node_states.get(node_id, "unknown"),
                    prompt=request.context.node_prompts.get(node_id, ""),
                )
            cached.metadata["input_artifact_ids"] = identifiers
            cached.metadata["attachment_artifact_ids"] = [
                identifier for identifier in request.artifacts if identifier not in identifiers
            ]
            cached.metadata["input_sources"] = {
                identifier: request.context.artifact_metadata.get(identifier, {}).get("source", "unknown")
                for identifier in identifiers
            }
            cached.metadata["contains_substitute"] = any(
                request.context.artifact_metadata.get(identifier, {}).get("is_substitute")
                or request.context.artifact_metadata.get(identifier, {}).get("contains_substitute")
                for identifier in identifiers
            )
            return cached
        return await asyncio.to_thread(compose, request, store)


async def rebuild_comic_layout(engine, node, artifacts, *, context):
    return await engine.execute_node(node, artifacts, context=context)
