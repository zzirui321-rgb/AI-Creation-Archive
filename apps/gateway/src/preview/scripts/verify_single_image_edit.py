"""Explicit local-model acceptance for one full-image node and historical image references."""

import argparse
import asyncio
import json
import math
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .. import ArtifactStore, ExecutionContext, PreviewEngine, PreviewSettings
from ..scene_layers import decode, png, rectangle
from .generic_cases import literal, node


def check(before, after, mask):
    assert before.size == after.size
    difference = ImageChops.difference(before, after)
    protected = mask.point(lambda value: 255 if value == 0 else 0)
    for channel in difference.split():
        assert ImageChops.multiply(channel, protected).getbbox() is None, "Protected RGBA pixel changed"
    assert any(channel.getbbox() for channel in difference.split()), "No actual visual change"
    return {"protected_pixels": mask.histogram()[0], "protected_max_error": 0, "image_changed": True}


async def verify(args):
    outline = args.protected_outline
    if outline is not None:
        if (
            not isinstance(outline, list)
            or not 3 <= len(outline) <= 256
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                    for value in point
                )
                for point in outline
            )
        ):
            raise ValueError("protected-outline must contain 3..256 normalized [x,y] points")
    args.output.mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(args.output / "store")
    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            provider="live_api",
            api_style="sdcpp",
            api_base=args.endpoint,
            api_key="local-test",
            draft_model=args.model,
            hq_model=args.model,
            timeout_seconds=180,
            allow_fallback=False,
        ),
    )
    definition = node(
        "complete_image",
        "image_gen",
        {
            "prompt": literal(
                "Watercolor wide landscape, a small blue robot in the center of a meadow, distant mountains and trees, full body"
            )
        },
        params={"seed": 73},
    )
    context = ExecutionContext(run_id="single-original", cache_key="single-image-acceptance", seed=73)
    original = await engine.execute_node(definition, {}, context=context)
    assert original.metadata["source"] == "live_api"
    artifacts = {original.artifacts[0]["id"]: original.artifacts[0]}
    base = decode(store.read(original.artifacts[0]))
    (args.output / "original.png").write_bytes(store.read(original.artifacts[0]))
    region = [0.28, 0.08, 0.44, 0.84]
    mask = Image.new("L", base.size)
    x1, y1, x2, y2 = rectangle(region, base.size)
    ImageDraw.Draw(mask).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
    report = {"graph_node_count": 1, "phases": {}}
    subject = None
    for stage in ("draft", "final"):
        result = await engine.execute_image_edit(
            definition,
            artifacts,
            context=replace(
                context,
                run_id="subject-" + stage,
                mode=stage,
                resolved_inputs={
                    "prompt": "Replace the central blue robot with a fox wearing a green scarf. Keep the watercolor landscape."
                },
            ),
            original_artifact_id=original.artifacts[0]["id"],
            region=region,
        )
        assert result.metadata["source"] == "live_api" and not result.metadata["is_substitute"]
        data = store.read(result.artifacts[0])
        picture = decode(data)
        report["phases"]["subject-" + stage] = {
            **check(base, picture, mask),
            "metadata": result.metadata,
            "sha256": result.artifacts[0]["sha256"],
        }
        (args.output / f"subject-{stage}.png").write_bytes(data)
        subject = result
    artifacts[subject.artifacts[0]["id"]] = subject.artifacts[0]
    subject_image = decode(store.read(subject.artifacts[0]))
    append_region = [0.70, 0.0, 0.30, 0.42]
    definition["execution"]["params"]["edit_operation"] = "overlay"
    appended = await engine.execute_image_edit(
        definition,
        artifacts,
        context=replace(
            context,
            run_id="append-detail",
            resolved_inputs={
                "prompt": "A small red hot air balloon with a wicker basket, delicate watercolor illustration"
            },
        ),
        original_artifact_id=subject.artifacts[0]["id"],
        region=append_region,
    )
    assert appended.metadata["source"] == "live_api" and not appended.metadata["is_substitute"]
    append_image = decode(store.read(appended.artifacts[0]))
    append_mask = Image.new("L", base.size)
    x1, y1, x2, y2 = rectangle(append_region, base.size)
    ImageDraw.Draw(append_mask).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
    report["phases"]["append"] = {**check(subject_image, append_image, append_mask), "metadata": appended.metadata}
    (args.output / "append.png").write_bytes(store.read(appended.artifacts[0]))
    artifacts[appended.artifacts[0]["id"]] = appended.artifacts[0]
    if outline is not None:
        mask = Image.new("L", base.size)
        ImageDraw.Draw(mask).polygon([(round(x * base.width), round(y * base.height)) for x, y in outline], fill=255)
        changes = ImageChops.difference(subject_image, append_image).split()
        for channel in changes:
            mask = ImageChops.lighter(mask, channel.point(lambda value: 255 if value else 0))
    background_mask = mask.point(lambda value: 255 - value)
    background_mask.save(args.output / "background-edit-mask.png")
    report["background_boundary"] = (
        "explicit human-supplied outline plus appended-object support"
        if outline
        else "coarse rectangle; visible protected-background seams are expected"
    )
    background_artifact = store.put(png(background_mask), "image/png", node_id=None, run_id=None)
    artifacts[background_artifact["id"]] = background_artifact
    definition["execution"]["params"]["edit_reference"] = False
    definition["execution"]["params"]["edit_operation"] = "replace"
    changed = await engine.execute_image_edit(
        definition,
        artifacts,
        context=replace(
            context,
            run_id="background-change",
            resolved_inputs={
                "prompt": "Watercolor winter landscape, white snow covering the meadow and pine trees, snowy mountains, snowflakes, empty environment, no animals, no people."
            },
        ),
        original_artifact_id=appended.artifacts[0]["id"],
        mask_artifact_id=background_artifact["id"],
    )
    assert changed.metadata["source"] == "live_api" and not changed.metadata["is_substitute"]
    report["phases"]["background"] = {
        **check(append_image, decode(store.read(changed.artifacts[0])), background_mask),
        "metadata": changed.metadata,
    }
    (args.output / "background.png").write_bytes(store.read(changed.artifacts[0]))
    report["semantic_validation"] = "Inspect images separately; pixel assertions do not prove requested object identity"
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {key: {k: v for k, v in value.items() if k != "metadata"} for key, value in report["phases"].items()}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/images/generations")
    parser.add_argument("--model", default="flux3-image-klein")
    parser.add_argument(
        "--protected-outline",
        type=json.loads,
        help="Optional human-annotated normalized polygon for the protected subject",
    )
    asyncio.run(verify(parser.parse_args()))
