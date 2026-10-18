import argparse
import asyncio
import json
from pathlib import Path

from PIL import Image
from preview import ArtifactStore, ExecutionContext, HTTPSegmenter, PreviewEngine, PreviewSettings, SegmentationSettings
from preview.scene_layers import decode, png
from preview.scripts.generic_cases import node
from preview.scripts.verify_semantic_pixels import check


async def verify(args):
    store = ArtifactStore(args.output / "store")
    args.output.mkdir(parents=True, exist_ok=True)
    settings = PreviewSettings(
        provider="live_api",
        api_style="sdcpp",
        api_base=args.image_endpoint,
        api_key="local-segmentation-acceptance",
        draft_model=args.model,
        timeout_seconds=180,
        allow_fallback=False,
    )
    engine = PreviewEngine(
        store, settings=settings, segmenter=HTTPSegmenter(SegmentationSettings(endpoint=args.segment_endpoint))
    )
    data = args.source.read_bytes()
    original = store.put(data, "image/png", node_id=None, run_id=None)
    image = decode(data)
    proposal = await engine.propose_image_edit(
        original,
        {
            "operation": "background",
            "instruction": "Watercolor empty winter landscape, snowy meadow and pine trees, distant snow-covered mountains, no animals or objects",
            "targets": args.targets,
        },
    )
    assert proposal["status"] == "ready", proposal
    (args.output / "background-proposal.json").write_text(json.dumps(proposal, indent=2), encoding="utf-8")
    double = Image.new("RGBA", (image.width, image.height // 2))
    small = image.resize((image.width // 2, image.height // 2))
    double.paste(small, (0, 0))
    double.paste(small, (image.width // 2, 0))
    multiple = store.put(png(double), "image/png", node_id=None, run_id=None)
    ambiguous = await engine.propose_image_edit(
        multiple, {"operation": "replace", "instruction": "a mechanical robot", "targets": [args.targets[0]]}
    )
    assert ambiguous["status"] == "needs_confirmation" and len(ambiguous["candidates"]) >= 2, ambiguous
    (args.output / "ambiguous.png").write_bytes(png(double))
    (args.output / "ambiguous-proposal.json").write_text(json.dumps(ambiguous, indent=2), encoding="utf-8")
    report = {
        "background": {
            "status": proposal["status"],
            "protected_targets": args.targets,
            "provenance": proposal["candidates"][0]["provenance"],
        },
        "ambiguous": {
            "status": ambiguous["status"],
            "candidates": len(ambiguous["candidates"]),
            "model_executed": False,
        },
    }
    if args.live:
        result = await engine.execute_proposed_edit(
            node("image", "image_gen", {}, params={"seed": 73, "edit_reference": False}),
            {original["id"]: original},
            context=ExecutionContext(run_id="semantic-background", cache_key="semantic-background", seed=73),
            original_artifact_id=original["id"],
            proposal_id=proposal["id"],
        )
        assert result.metadata["source"] == "live_api" and not result.metadata["is_substitute"]
        (args.output / "source.png").write_bytes(data)
        (args.output / "result.png").write_bytes(store.read(result.artifacts[0]))
        (args.output / "mask.png").write_bytes(store.read(proposal["candidates"][0]["mask_artifact"]))
        report["background"]["pixels"] = check(args.output)
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--targets", nargs="+", required=True)
    parser.add_argument("--segment-endpoint", default="http://127.0.0.1:8101/segment")
    parser.add_argument("--image-endpoint", default="http://127.0.0.1:8000/v1/images/generations")
    parser.add_argument("--model", default="flux3-image-klein")
    parser.add_argument("--live", action="store_true")
    asyncio.run(verify(parser.parse_args()))
