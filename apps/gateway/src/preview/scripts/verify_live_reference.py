import argparse
import asyncio
import json
from pathlib import Path

import httpx
from preview import ArtifactStore, ExecutionContext, PreviewEngine, PreviewSettings


async def verify(args):
    store = ArtifactStore(args.output / "reference-store")
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        response = await client.get(args.reference_url)
        response.raise_for_status()
        reference = store.put(response.content, "image/png", node_id="source-image", run_id="source-run")
    node = {
        "id": "reference-edit",
        "kind": "model",
        "label": "Reference-guided image edit",
        "branch_id": "main",
        "provenance": {"mode": "recorded", "evidence": [{"source": "local-provider-acceptance"}]},
        "execution": {
            "kind": "model",
            "tool": "image_gen",
            "version": "b-generic-3",
            "inputs": {
                "prompt": {
                    "kind": "literal",
                    "value": "Watercolor owl in a quiet forest, preserve the reference composition",
                },
                "reference_artifact_ids": {"kind": "literal", "value": [reference["id"]]},
            },
            "params": {"seed": 410},
            "outputs": ["image"],
        },
    }
    settings = PreviewSettings(
        api_base=args.endpoint,
        api_key="sk-local",
        api_style="sdcpp",
        draft_model=args.model,
        hq_model=args.model,
        timeout_seconds=180,
        allow_fallback=False,
    )
    result = await PreviewEngine(store, settings=settings).execute_node(
        node,
        {reference["id"]: reference},
        context=ExecutionContext(run_id="reference-check", cache_key="reference-check", seed=410),
    )
    assert result.metadata["source"] == "live_api" and not result.metadata["is_substitute"]
    assert result.metadata["reference_artifact_ids"] == [reference["id"]]
    (args.output / "reference.png").write_bytes(store.read(result.artifacts[0]))
    (args.output / "reference-report.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "source": result.metadata["source"],
                "reference_sha256": reference["sha256"],
                "result_sha256": result.artifacts[0]["sha256"],
                "width": result.metadata["actual_width"],
                "height": result.metadata["actual_height"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reference-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(verify(parser.parse_args()))
