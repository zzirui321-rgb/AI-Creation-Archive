"""Explicit edit boundaries inside an image node, independent of Graph granularity."""

import hashlib
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw

from .contracts import PreviewError
from .scene_layers import decode, rectangle


@dataclass
class ImageEdit:
    original_id: str
    mask_id: str | None
    original: Image.Image
    mask: Image.Image

    def metadata(self, artifacts):
        return {
            "edit_mode": "masked_replace",
            "base_sha256": artifacts[self.original_id]["sha256"],
            "edit_mask_sha256": hashlib.sha256(self.mask.tobytes()).hexdigest(),
            "mask_artifact_sha256": artifacts[self.mask_id]["sha256"] if self.mask_id else None,
            "protected_pixel_count": self.mask.histogram()[0],
            "editable_pixel_count": self.mask.width * self.mask.height - self.mask.histogram()[0],
            "preservation": "Original RGBA pixels are copied exactly wherever the edit mask is zero",
            "resolution_policy": "original_image",
        }


def resolve_image_edit(inputs, params, artifacts, store):
    def image_input(port):
        identifier = inputs.get(port)
        if not isinstance(identifier, str) or identifier not in artifacts:
            raise PreviewError("missing_edit_input", f"An image artifact is required at {port}")
        artifact = artifacts[identifier]
        if artifact["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}:
            raise PreviewError("invalid_edit_input", f"Unsupported raster image at {port}")
        return identifier, decode(store.read(artifact))

    original_id, original = image_input("original_image")
    matting = params.get("matting", {})
    if not isinstance(matting, dict) or set(matting) - {"tolerance", "feather"}:
        raise PreviewError("invalid_matting", "Unknown matting parameters")
    has_mask, has_rect = "edit_mask" in inputs, "edit_region" in params
    if has_mask == has_rect:
        raise PreviewError("missing_edit_boundary", "Provide exactly one edit_mask artifact or normalized edit_region")
    mask_id = None
    if has_mask:
        mask_id, source = image_input("edit_mask")
        if source.size != original.size:
            raise PreviewError("invalid_mask", "Edit mask must match original dimensions; resizing is not allowed")
        mask = ImageChops.multiply(source.convert("L"), source.getchannel("A"))
    else:
        box = rectangle(params["edit_region"], original.size)
        mask = Image.new("L", original.size)
        ImageDraw.Draw(mask).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    if mask.getbbox() is None:
        raise PreviewError("empty_edit_mask", "Edit boundary has no editable pixels")
    target = params.get("edit_target")
    if target is not None and (
        not isinstance(target, dict)
        or mask_id is None
        or target.get("source_sha256") != artifacts[original_id]["sha256"]
        or target.get("mask_sha256") != artifacts[mask_id]["sha256"]
    ):
        raise PreviewError("stale_edit_target", "Semantic target receipt must match the original and mask artifacts")
    return ImageEdit(original_id, mask_id, original, mask)


def blend_image_edit(output, request, store):
    from .contracts import AdapterOutput
    from .scene_layers import extract_foreground, placed_layer, png

    edit = resolve_image_edit(request.inputs, request.profile.params, request.artifacts, store)
    generated = decode(output.data)
    if generated.size != edit.original.size:
        raise PreviewError("invalid_edit_dimensions", "Edit provider must return the original image dimensions")
    effective_mask = edit.mask
    operation = request.profile.params.get("edit_operation", "replace")
    if operation == "overlay":
        generated, _ = extract_foreground(generated, **request.profile.params.get("matting", {}))
        x1, y1, x2, y2 = edit.mask.getbbox()
        width, height = edit.original.size
        layer = placed_layer(
            generated, edit.original.size, {"rect": [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]}
        )
        effective_mask = ImageChops.multiply(layer.getchannel("A"), edit.mask)
        layer.putalpha(effective_mask)
        result = Image.alpha_composite(edit.original, layer)
    else:
        # Image.composite also preserves transparent RGB/alpha bytes in the protected region.
        result = Image.composite(generated, edit.original, edit.mask)
    return AdapterOutput(
        png(result),
        "image/png",
        output.source,
        {
            **output.metadata,
            **edit.metadata(request.artifacts),
            "layer_prepared": True,
            "actual_width": result.width,
            "actual_height": result.height,
            "edit_region": request.profile.params.get("edit_region"),
            "edit_operation": operation,
            "effective_mask_sha256": hashlib.sha256(effective_mask.tobytes()).hexdigest(),
            "protected_pixel_count": effective_mask.histogram()[0],
            "editable_pixel_count": effective_mask.width * effective_mask.height - effective_mask.histogram()[0],
            **(
                {"edit_target": request.profile.params["edit_target"]}
                if "edit_target" in request.profile.params
                else {}
            ),
        },
        output.cache_hit,
    )
