from __future__ import annotations

import asyncio
import colorsys
import copy
import io
import math
import random
from dataclasses import replace

from PIL import Image, ImageChops, ImageDraw, PngImagePlugin

from ..contracts import AdapterOutput, ExecutionRequest, digest
from ..visuals import text_box
from .base import PreviewProvider

PALETTES = {
    "watercolor": ("#eef4ee", "#6b9b93", "#41526a", "#d58c8c"),
    "ink": ("#f1f0eb", "#343631", "#585e55", "#a64445"),
    "pixel": ("#252338", "#b4c85d", "#e1e6eb", "#d68aaf"),
    "minimal": ("#122b2b", "#90c7b0", "#edf5ee", "#486e68"),
    "nature": ("#e7f1e9", "#4d8765", "#234548", "#9b789d"),
    "space": ("#171b30", "#779fcf", "#e9edf3", "#c17a9d"),
}


def visual_spec(request):
    prompt = str(
        request.inputs.get(
            "resolved_prompt",
            request.inputs.get("prompt", request.inputs.get("content", request.node.get("label", ""))),
        )
    )
    consistency = request.inputs.get("consistency_context")
    if isinstance(consistency, dict):
        continuity = {
            key: consistency[key]
            for key in ("identity_hash", "camera", "composition", "shot", "framing", "style_version")
            if consistency.get(key) is not None
        }
        prompt += "\nContinuity constraints: " + str(continuity)

    # Only resolved values participate; unrelated archive variables must not invalidate this node.
    def scalar_inputs(value):
        if isinstance(value, str) and value in request.artifacts:
            return None
        if isinstance(value, dict):
            return {key: scalar_inputs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scalar_inputs(item) for item in value]
        return value

    identity = {"node": request.node["id"], "inputs": scalar_inputs(request.inputs), "seed": request.profile.seed}
    content_hash = digest(identity)
    language = (str(request.inputs.get("style", "")) + " " + prompt).lower()
    style = next(
        (
            name
            for name, words in {
                "ink": ("水墨", "ink", "黑白"),
                "watercolor": ("水彩", "watercolor", "绘本"),
                "pixel": ("像素", "pixel", "8-bit"),
                "minimal": ("极简", "minimal", "web", "code", "界面"),
                "nature": ("自然", "森林", "forest", "植物", "landscape"),
                "space": ("太空", "space", "科幻", "暗黑", "宇宙"),
            }.items()
            if any(word in language for word in words)
        ),
        "procedural",
    )
    colors = PALETTES.get(style)
    if colors is None:
        hue = int(content_hash[:6], 16) / 0xFFFFFF

        def color(h, s, light):
            return tuple(round(c * 255) for c in colorsys.hls_to_rgb(h % 1, light, s))

        colors = (
            color(hue, 0.25, 0.93),
            color(hue, 0.5, 0.42),
            color(hue + 0.4, 0.25, 0.2),
            color(hue + 0.45, 0.5, 0.62),
        )
    kind = f"{request.node.get('kind', '')} {request.node['execution']['tool']} {language}".lower()
    subject = next(
        (
            name
            for name, words in {
                "interface": ("code", "web", "html", "界面", "网站"),
                "waveform": ("audio", "music", "voice", "音频", "音轨"),
                "mechanical": ("robot", "mechanical", "机器人", "机械"),
                "ocean": ("jellyfish", "水母"),
                "creature": ("dragon", "fox", "cat", "creature", "猫", "龙", "狐狸", "生物"),
                "orbital": ("space", "planet", "orbit", "星", "太空"),
                "botanical": ("plant", "forest", "flower", "植物", "森林", "花"),
                "document": ("text", "json", "文档", "报告"),
                "layout": ("compose", "layout", "排版"),
                "food": ("burger", "sandwich", "汉堡"),
                "shield": ("shield", "护盾"),
            }.items()
            if any(word in kind for word in words)
        ),
        "abstract",
    )
    return prompt, content_hash, style, subject, colors


def draw_subject(draw, subject, box, colors, rng):
    left, top, right, bottom = box
    shift = rng.uniform(-0.06, 0.06) * (right - left)
    inset = rng.uniform(0, 0.1) * (bottom - top)
    left, right, top, bottom = left + shift, right + shift, top + inset, bottom - inset
    width, height = right - left, bottom - top
    cx, cy = (left + right) / 2, (top + bottom) / 2
    _, accent, ink, secondary = colors
    if subject == "food":
        draw.pieslice((left, top, right, bottom), 180, 360, fill="#deb965", outline=ink, width=3)
        draw.rectangle((left, cy, right, cy + height * 0.12), fill="#528d69")
        draw.rectangle((left, cy + height * 0.13, right, cy + height * 0.32), fill="#a55264")
        draw.rounded_rectangle(
            (left, cy + height * 0.34, right, bottom), radius=16, fill="#deb965", outline=ink, width=3
        )
    elif subject == "shield":
        draw.polygon(
            [(left, top), (right, top), (right, cy), (cx, bottom), (left, cy)], fill=accent, outline=ink, width=4
        )
    elif subject in {"interface", "document", "layout"}:
        draw.rectangle(box, outline=ink, width=3)
        columns = 2 if subject == "layout" else 1
        for column in range(columns):
            for row in range(5):
                x = left + width * (0.06 + column * 0.5)
                y = top + height * (0.12 + row * 0.16)
                end = x + width * rng.uniform(0.2, 0.35 if columns == 2 else 0.75)
                draw.rectangle((x, y, min(end, right - 8), y + height * 0.06), fill=accent if row % 2 else secondary)
    elif subject == "waveform":
        for index in range(48):
            x = left + index * width / 48
            amplitude = height * rng.uniform(0.08, 0.45)
            draw.line((x, cy - amplitude, x, cy + amplitude), fill=accent if index % 3 else secondary, width=4)
    elif subject == "botanical":
        for index in range(5):
            x = left + width * (0.18 + index * 0.16)
            y = top + height * rng.uniform(0.15, 0.5)
            draw.line((x, bottom, x, y), fill=ink, width=3)
            draw.ellipse((x - width * 0.1, y, x, y + height * 0.25), fill=accent)
            draw.ellipse((x, y - height * 0.08, x + width * 0.1, y + height * 0.17), fill=secondary)
    elif subject == "ocean":
        draw.pieslice((cx - width * 0.2, cy - height * 0.4, cx + width * 0.2, cy + height * 0.1), 180, 360, fill=accent)
        for index in range(7):
            x = cx + (index - 3) * width * 0.05
            draw.line(
                [(x, cy - height * 0.15), (x + width * 0.04, cy + height * 0.15), (x - width * 0.03, bottom)],
                fill=secondary,
                width=3,
            )
    elif subject == "orbital":
        radius = min(width, height) * 0.28
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=accent)
        draw.ellipse((left, cy - height * 0.1, right, cy + height * 0.1), outline=secondary, width=4)
        for _ in range(20):
            x, y = rng.uniform(left, right), rng.uniform(top, bottom)
            draw.ellipse((x, y, x + 3, y + 3), fill=ink)
    elif subject == "creature":
        draw.ellipse((cx - width * 0.25, cy - height * 0.2, cx + width * 0.25, bottom), fill=accent)
        draw.polygon([(cx - width * 0.25, cy), (cx - width * 0.2, top), (cx, cy - height * 0.25)], fill=secondary)
        draw.polygon([(cx + width * 0.25, cy), (cx + width * 0.2, top), (cx, cy - height * 0.25)], fill=secondary)
        for offset in (-0.1, 0.1):
            x = cx + width * offset
            draw.ellipse((x - 5, cy - 5, x + 5, cy + 5), fill=ink)
    elif subject == "mechanical":
        draw.rectangle(
            (cx - width * 0.26, top, cx + width * 0.26, cy + height * 0.03), fill=secondary, outline=ink, width=3
        )
        draw.rectangle(
            (cx - width * 0.22, cy + height * 0.08, cx + width * 0.22, bottom), fill=accent, outline=ink, width=3
        )
        for offset in (-0.13, 0.13):
            x = cx + width * offset
            draw.rectangle((x - 11, top + height * 0.15, x + 11, top + height * 0.23), fill=ink)
        draw.line((cx, top, cx, top - 15), fill=accent, width=4)
    else:
        vertices = rng.randint(4, 10)
        points = [
            (
                cx + math.cos(i * math.tau / vertices) * width * rng.uniform(0.2, 0.48),
                cy + math.sin(i * math.tau / vertices) * height * rng.uniform(0.2, 0.48),
            )
            for i in range(vertices)
        ]
        draw.polygon(points, fill=accent, outline=ink)
        for _ in range(6):
            x, y = rng.uniform(left, right), rng.uniform(top, bottom)
            radius = rng.uniform(5, min(width, height) * 0.12)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=secondary, width=3)


def render_local_image(request: ExecutionRequest) -> bytes:
    prompt, identity, style, subject, colors = visual_spec(request)
    width, height = request.profile.width, request.profile.height
    role = request.node["execution"].get("params", {}).get("layer_role", "scene")
    values = request.inputs.get("variables", {})
    style_value = request.inputs.get("style", values.get("style", "") if isinstance(values, dict) else "")
    environment = request.inputs.get("environment", request.inputs.get("background", request.inputs.get("scene", "")))
    background_prompt = prompt if role == "background" else str(environment)
    style_words = (str(style_value) + " " + background_prompt).lower()
    bg_style = next(
        (
            name
            for name, words in {
                "watercolor": ("watercolor", "水彩"),
                "ink": ("水墨", " ink"),
                "pixel": ("pixel", "像素"),
                "minimal": ("minimal", "极简", "web"),
                "nature": ("forest", "自然", "森林"),
                "space": ("space", "太空", "科幻"),
            }.items()
            if any(word in style_words for word in words)
        ),
        "watercolor",
    )
    if not style_value and not environment and role == "scene" and style in PALETTES:
        bg_style = style
    background_identity = digest({"style": style_value or bg_style, "environment": background_prompt})
    colors = PALETTES[bg_style]
    image = Image.new("RGBA", (960, 640), colors[0])
    draw = ImageDraw.Draw(image)
    rng = random.Random(int(background_identity[:12], 16))
    if bg_style in {"watercolor", "nature"}:
        for _ in range(110):
            x, y = rng.randrange(960), rng.randrange(640)
            draw.ellipse((x, y, x + rng.randrange(2, 20), y + 4), outline=colors[3])
    elif bg_style in {"pixel", "minimal"}:
        for x in range(0, 960, 32):
            draw.line((x, 0, x, 640), fill=colors[3])
    draw.rectangle((18, 18, 942, 622), outline=colors[1], width=3)
    if role == "background":
        draw_subject(
            draw,
            "botanical" if bg_style == "nature" else "orbital" if bg_style == "space" else "abstract",
            (30, 110, 930, 610),
            colors,
            rng,
        )
    else:
        if role in {"subject", "detail"}:
            image = Image.new("RGBA", (960, 640))
        subject_layer = Image.new("RGBA", (960, 640))
        subject_draw = ImageDraw.Draw(subject_layer)
        subject_values = {
            key: value
            for key, value in request.inputs.items()
            if key not in {"detail", "append", "details", "variables", "environment", "background", "scene"}
        }
        subject_prompt = str(
            request.inputs.get("subject", request.inputs.get("character", request.inputs.get("prompt", "")))
        )
        subject_identity = digest({"subject": subject_prompt, "values": subject_values})
        subject_colors = PALETTES.get(style, PALETTES["watercolor"])
        subject_box = (490, 170, 900, 450) if role == "scene" else (180, 60, 780, 600)
        draw_subject(
            subject_draw,
            subject,
            subject_box,
            subject_colors,
            random.Random(int(subject_identity[:12], 16)),
        )
        clip = Image.new("L", subject_layer.size)
        ImageDraw.Draw(clip).rectangle(subject_box, fill=255)
        subject_layer.putalpha(ImageChops.multiply(subject_layer.getchannel("A"), clip))
        image = Image.alpha_composite(image, subject_layer)
        if role == "scene":
            detail = request.inputs.get("detail", request.inputs.get("append", request.inputs.get("details", "")))
            if detail:
                detail_layer = Image.new("RGBA", image.size)
                detail_rng = random.Random(int(digest({"detail": detail})[:12], 16))
                detail_kind = (
                    "food"
                    if any(word in str(detail).lower() for word in ("burger", "汉堡"))
                    else "shield"
                    if any(word in str(detail).lower() for word in ("shield", "护盾"))
                    else "abstract"
                )
                draw_subject(ImageDraw.Draw(detail_layer), detail_kind, (90, 120, 350, 340), subject_colors, detail_rng)
                image = Image.alpha_composite(image, detail_layer)
    draw = ImageDraw.Draw(image)
    if role != "scene":
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        info = PngImagePlugin.PngInfo()
        info.add_text("source", "local_fallback")
        info.add_text("stage", request.profile.schema_stage)
        info.add_text("resolvedPrompt", prompt)
        info.add_text("backgroundHash", background_identity)
        result = io.BytesIO()
        image.save(result, "PNG", pnginfo=info)
        return result.getvalue()
    text_box(
        draw,
        f"{request.node['id']} / {request.node.get('label', '')}",
        (36, 30, 924, 76),
        size=23,
        fill=colors[2],
        bold=True,
    )
    text_box(
        draw,
        f"{request.profile.schema_stage.upper()} / LOCAL FALLBACK",
        (36, 86, 924, 122),
        size=18,
        fill=colors[2],
    )
    text_box(
        draw,
        f"{width}x{height} | LOCAL FALLBACK",
        (36, 550, 924, 600),
        size=19,
        fill=colors[2],
    )
    image = image.resize((width, height), Image.Resampling.NEAREST if style == "pixel" else Image.Resampling.LANCZOS)
    info = PngImagePlugin.PngInfo()
    for key, value in {
        "source": "local_fallback",
        "stage": request.profile.schema_stage,
        "seed": str(request.profile.seed),
        "resolvedPrompt": prompt,
        "inputHash": identity,
        "style": style,
        "subject": subject,
    }.items():
        info.add_text(key, value)
    result = io.BytesIO()
    image.save(result, "PNG", pnginfo=info)
    return result.getvalue()


class LocalFallbackProvider(PreviewProvider):
    source = "local_fallback"

    async def generate(self, request: ExecutionRequest, store) -> AdapterOutput:
        prompt, identity, style, subject, _ = visual_spec(request)
        render_request = request
        if request.profile.params.get("edit_operation") == "overlay":
            node = copy.deepcopy(request.node)
            node["execution"]["params"]["layer_role"] = "detail"
            render_request = replace(request, node=node)
        return AdapterOutput(
            await asyncio.to_thread(render_local_image, render_request),
            "image/png",
            self.source,
            {
                "is_substitute": True,
                "resolved_prompt": prompt,
                "input_hash": identity,
                "visual_style": style,
                "subject": subject,
                "reference_artifact_ids": request.inputs.get("reference_artifact_ids", []),
                **(
                    {"consistency_context": request.inputs["consistency_context"]}
                    if "consistency_context" in request.inputs
                    else {}
                ),
            },
        )
