"""Deterministic pixel composition; model generation never controls protected pixels."""

import hashlib
import io
import math
from statistics import median

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from .contracts import AdapterOutput, PreviewError


def png(image):
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def decode(data):
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 16_777_216:
                raise PreviewError("oversized_image", "Scene image exceeds pixel limit")
            return ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, ValueError) as error:
        raise PreviewError("invalid_image", "Cannot decode scene layer") from error


def extract_foreground(image, *, tolerance=38, feather=1.2):
    if not isinstance(tolerance, (int, float)) or not 0 <= tolerance <= 150:
        raise PreviewError("invalid_matting", "Matting tolerance must be within 0..150")
    if not isinstance(feather, (int, float)) or not math.isfinite(feather) or not 0 <= feather <= 8:
        raise PreviewError("invalid_matting", "Feather radius must be within 0..8")
    image = image.convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 255:
        return image, "provided_alpha"
    rgb = image.convert("RGB")
    width, height = image.size
    radius = max(1, min(width, height) // 48)
    patches = [
        (0, 0, radius, radius),
        (width - radius, 0, width, radius),
        (0, height - radius, radius, height),
        (width - radius, height - radius, width, height),
    ]
    colors = [tuple(int(median(channel.getdata())) for channel in rgb.crop(box).split()) for box in patches]
    distance = Image.new("L", image.size, 255)
    for color in colors:
        channels = ImageChops.difference(rgb, Image.new("RGB", image.size, color)).split()
        distance = ImageChops.darker(
            distance, ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        )
    connected = distance.point(lambda value: 0 if value <= tolerance else 255)
    stride = max(1, min(width, height) // 128)
    seeds = [(x, y) for x in range(0, width, stride) for y in (0, height - 1)]
    seeds += [(x, y) for y in range(0, height, stride) for x in (0, width - 1)]
    for point in seeds:
        if connected.getpixel(point) == 0:
            ImageDraw.floodfill(connected, point, 128, thresh=0)
    mask = connected.point(lambda value: 0 if value == 128 else 255)
    removed = mask.histogram()[0] / (width * height)
    if removed < 0.02 or removed > 0.995:
        raise PreviewError(
            "foreground_extraction_failed", "No reliable isolated subject; provide an alpha PNG or explicit mask"
        )
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    image.putalpha(mask)
    return image, "corner_floodfill_feather"


def prepare_image_output(output, request, store):
    params = request.node["execution"].get("params", {})
    if params.get("edit_mode") == "masked_replace" and not output.metadata.get("layer_prepared"):
        from .image_edit import blend_image_edit

        return blend_image_edit(output, request, store)
    role = request.node["execution"].get("params", {}).get("layer_role", "scene")
    if role not in {"scene", "background", "subject", "detail"}:
        raise PreviewError("invalid_layer_role", "Expected scene, background, subject or detail")
    if role not in {"subject", "detail"} or output.metadata.get("layer_prepared"):
        return output
    options = request.node["execution"].get("params", {}).get("matting", {})
    if not isinstance(options, dict) or set(options) - {"tolerance", "feather"}:
        raise PreviewError("invalid_matting", "Unknown matting parameters")
    image, method = extract_foreground(decode(output.data), **options)
    return AdapterOutput(
        png(image),
        "image/png",
        output.source,
        {
            **output.metadata,
            "layer_role": role,
            "layer_prepared": True,
            "alpha_method": method,
            "alpha_bbox": image.getchannel("A").getbbox(),
            "actual_width": image.width,
            "actual_height": image.height,
        },
        output.cache_hit,
    )


def rectangle(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PreviewError("invalid_layer_rect", "rect must be normalized [x, y, width, height]")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise PreviewError("invalid_layer_rect", "Layer rectangle must contain finite numbers")
    x, y, width, height = value
    if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.000001 or y + height > 1.000001:
        raise PreviewError("invalid_layer_rect", "Layer rectangle must fit inside the base canvas")
    box = (round(x * size[0]), round(y * size[1]), round((x + width) * size[0]), round((y + height) * size[1]))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise PreviewError("invalid_layer_rect", "Layer rectangle is smaller than a pixel")
    return box


def input_image(request, store, port):
    identifier = request.inputs.get(port)
    if not isinstance(identifier, str) or identifier not in request.artifacts:
        raise PreviewError("missing_layer", f"Missing image artifact at input {port}")
    return identifier, decode(store.read(request.artifacts[identifier]))


def placed_layer(source, size, spec):
    box = rectangle(spec.get("rect", [0, 0, 1, 1]), size)
    if spec.get("crop", True):
        bounds = source.getchannel("A").getbbox()
        if not bounds:
            return Image.new("RGBA", size)
        source = source.crop(bounds)
    width, height = box[2] - box[0], box[3] - box[1]
    source = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", size)
    layer.paste(source, (box[0] + (width - source.width) // 2, box[1] + (height - source.height) // 2))
    return layer


def layer_stack(request, store):
    layout = request.node["execution"]["params"].get("layout", {})
    base_port = layout.get("base_input", "background")
    base_id, canvas = input_image(request, store, base_port)
    base_bytes = canvas.tobytes()
    specifications = layout.get("layers", [])
    if not isinstance(specifications, list) or len(specifications) > 64:
        raise PreviewError("invalid_layers", "layers must contain at most 64 overlays")
    regions = [
        {
            "artifact_id": base_id,
            "node_id": request.artifacts[base_id].get("producer_node_id"),
            "role": "background",
            "caption": base_port,
            "rect": {"x": 0, "y": 0, "width": canvas.width, "height": canvas.height},
        }
    ]
    masks, identifiers = [], [base_id]
    for spec in specifications:
        if not isinstance(spec, dict) or "input" not in spec:
            raise PreviewError("invalid_layers", "Every layer requires an input port")
        identifier, source = input_image(request, store, spec["input"])
        if spec.get("extract", False):
            source, _ = extract_foreground(source, **spec.get("matting", {}))
        layer = placed_layer(source, canvas.size, spec)
        if spec.get("mask_input"):
            mask_id, mask_image = input_image(request, store, spec["mask_input"])
            if mask_image.size != canvas.size:
                raise PreviewError("invalid_mask", "Mask must match base dimensions; masks are never silently resized")
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask_image.convert("L")))
            identifiers.append(mask_id)
        if request.node["execution"]["params"].get("layout", {}).get("mode") == "masked_replace" and not spec.get(
            "mask_input"
        ):
            raise PreviewError("missing_mask", "masked_replace requires an explicit mask_input")
        alpha = layer.getchannel("A")
        masks.append(alpha)
        canvas = Image.alpha_composite(canvas, layer)
        identifiers.append(identifier)
        artifact = request.artifacts[identifier]
        bounds = alpha.getbbox() or (0, 0, 0, 0)
        regions.append(
            {
                "artifact_id": identifier,
                "node_id": artifact.get("producer_node_id"),
                "role": spec.get("role", "overlay"),
                "caption": spec.get("label", spec["input"]),
                "rect": {
                    "x": bounds[0],
                    "y": bounds[1],
                    "width": bounds[2] - bounds[0],
                    "height": bounds[3] - bounds[1],
                },
            }
        )
    bubbles = request.inputs.get("bubbles", [])
    if not isinstance(bubbles, list) or len(bubbles) > 64:
        raise PreviewError("invalid_bubbles", "bubbles must be a list of at most 64 entries")
    if bubbles:
        from .visuals import text_box

        layer = Image.new("RGBA", canvas.size)
        draw = ImageDraw.Draw(layer)
        for bubble in bubbles:
            box = rectangle(bubble["rect"], canvas.size)
            draw.rounded_rectangle(box, radius=8, fill="white", outline="#202333", width=2)
            text_box(
                draw, str(bubble["text"]), (box[0] + 8, box[1] + 5, box[2] - 8, box[3] - 5), size=16, fill="#202333"
            )
        masks.append(layer.getchannel("A"))
        canvas = Image.alpha_composite(canvas, layer)
    combined = Image.new("L", canvas.size)
    for mask in masks:
        combined = ImageChops.lighter(combined, mask)
    return AdapterOutput(
        png(canvas),
        "image/png",
        "composite_layout",
        {
            "is_substitute": False,
            "composition_mode": layout.get("mode", "layer_stack"),
            "base_artifact_id": base_id,
            "base_sha256": request.artifacts[base_id]["sha256"],
            "base_pixel_hash": hashlib.sha256(base_bytes).hexdigest(),
            "preservation": "Exact base pixels wherever the union of overlay alpha masks is zero",
            "overlay_mask_sha256": hashlib.sha256(combined.tobytes()).hexdigest(),
            "protected_pixel_count": combined.histogram()[0],
            "regions": regions,
            "canvas": {"width": canvas.width, "height": canvas.height},
            "input_artifact_ids": identifiers,
            "attachment_artifact_ids": [],
            "contains_substitute": any(
                request.context.artifact_metadata.get(item, {}).get("is_substitute")
                or request.context.artifact_metadata.get(item, {}).get("contains_substitute")
                for item in identifiers
            ),
        },
    )
