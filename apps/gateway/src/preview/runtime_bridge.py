"""Versioned integration with D's synchronous registered executor contract."""

import asyncio
import copy
import hashlib
import json
import os
import time
from pathlib import Path

from . import ArtifactStore, ExecutionContext, PreviewEngine, PreviewSettings
from .contracts import PreviewError, canonical_json, digest

BRIDGE_VERSION = "b-generic-3"
METADATA_MIME = "application/vnd.aica.preview+json"
TOOLS = ("image_gen", "image-provider", "layout_compose", "compose", "layout", "text_materialize", "json_materialize")


def stable_seed(node_id, inputs, artifact_ids):
    def normalize(value):
        if isinstance(value, str) and value in artifact_ids:
            return {"upstream_artifact": True}
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return int(digest({"node": node_id, "inputs": normalize(inputs)})[:8], 16)


def register_preview_tools(registry, settings=None, prewarmed_root=None):
    settings = settings or PreviewSettings.from_env()
    prewarmed_root = prewarmed_root or os.environ.get("AICA_PREWARMED_DIR")
    profile_id = digest(
        {"cache_policy": 6, **{key: value for key, value in vars(settings).items() if key != "api_key"}}
    )[:16]

    def handler(inputs, params, context):
        context.check_cancelled()
        document = context.engine.recorder.read_archive()["archive"]
        node = copy.deepcopy(next(item for item in document["graph"]["nodes"] if item["id"] == context.node_id))
        if "timeout_seconds" not in node["execution"].get("limits", {}):
            context.deadline = max(context.deadline, time.monotonic() + min(300, settings.timeout_seconds + 2))
        if len(node["execution"]["outputs"]) != 1:
            raise PreviewError("unsupported_outputs", "B adapters require one declared output port")
        node["execution"]["params"] = params
        store = ArtifactStore(Path(context.engine.project) / ".b-preview", prewarmed_root=prewarmed_root)
        artifacts = {item["id"]: item for item in document["artifacts"]}
        dependencies, hashes = {}, {}

        def resolve(value):
            descriptor = (
                isinstance(value, dict)
                and {"artifact_id", "sha256", "mime_type"} <= value.keys()
                and value.keys() <= {"artifact_id", "sha256", "mime_type", "size"}
            )
            identifier = value["artifact_id"] if descriptor else value
            if isinstance(identifier, str) and identifier in artifacts:
                original = artifacts[identifier]
                data = context.read_artifact(identifier)
                if hashlib.sha256(data).hexdigest() != original["sha256"]:
                    raise PreviewError("artifact_conflict", "Input artifact hash mismatch")
                local = store.put(
                    data,
                    original["mime_type"],
                    node_id=original["producer_node_id"],
                    run_id=original["producer_run_id"],
                )
                dependencies[identifier] = {**original, "uri": local["uri"], "locations": []}
                hashes[identifier] = original["sha256"]
                return identifier
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        resolved = resolve(inputs)
        if node["execution"]["tool"] in {"image_gen", "image-provider"}:
            names = set()
            for binding in node["execution"]["inputs"].values():
                if binding["kind"] == "variable":
                    names.add(binding["variable_id"])
                elif binding["kind"] == "template":
                    names.update(binding["variables"].values())
            resolved["variables"] = {name: document["variables"][name]["value"] for name in sorted(names)}
        upstream_metadata = {}
        for identifier in dependencies:
            owner = next((run for run in reversed(document["runs"]) if identifier in run["artifact_ids"]), None)
            if owner:
                sidecar = next(
                    (
                        item
                        for item in document["artifacts"]
                        if item["id"] in owner["artifact_ids"] and item["mime_type"] == METADATA_MIME
                    ),
                    None,
                )
                if sidecar:
                    body = json.loads(context.read_artifact(sidecar["id"]))
                    if (
                        body.get("format") == "aica-preview/1"
                        and body.get("metadata", {}).get("primary_sha256") == dependencies[identifier]["sha256"]
                    ):
                        upstream_metadata[identifier] = body["metadata"]
        seed = params.get("seed")
        if seed is None:
            seed = stable_seed(node["id"], resolved, artifacts)
        latest = {}
        for run in document["runs"]:
            if run["revision_id"] == document["graph"]["revision_id"] and run.get("preview_stage") == context.stage:
                latest[run["node_id"]] = run
        states = {key: value.get("cache", {}).get("decision", "unknown") for key, value in latest.items()}
        engine = PreviewEngine(store, settings=settings)
        request_context = ExecutionContext(
            run_id=context.run_id,
            cache_key=digest({"node": node["id"], "version": BRIDGE_VERSION}),
            resolved_inputs=resolved,
            mode=context.stage,
            seed=seed,
            decision="REBUILD" if node["kind"] == "composition" else "RECOMPUTE",
            node_states=states,
            artifact_metadata=upstream_metadata,
        )

        async def execute():
            task = asyncio.create_task(engine.execute_node(node, dependencies, context=request_context))
            try:
                while not task.done():
                    context.check_cancelled()
                    await asyncio.wait({task}, timeout=0.05)
                return await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(execute())
        else:
            raise PreviewError("worker_required", "Use D's executor worker, not its async HTTP loop")
        context.check_cancelled()
        primary = result.artifacts[0]
        context.cache_allowed = settings.provider in {"mock_svg", "local_fallback"} or not (
            result.metadata.get("is_substitute") or result.metadata.get("contains_substitute")
        )
        captured = context.capture(store.read(primary), primary["mime_type"])
        metadata = {
            **result.metadata,
            "provider_cache": result.cache,
            "primary_sha256": captured["sha256"],
            "reference_hashes": hashes,
        }
        metadata["artifact_ids"] = [captured["id"]]
        context.capture(canonical_json({"format": "aica-preview/1", "metadata": metadata}), METADATA_MIME)
        return {node["execution"]["outputs"][0]: captured["id"]}

    for tool in TOOLS:
        registry.register(
            tool,
            BRIDGE_VERSION,
            handler,
            cacheable=True,
            runtime_ref="b-generic@" + profile_id
            if tool in {"image_gen", "image-provider"}
            else "b-compose@5"
            if tool in {"layout_compose", "compose", "layout"}
            else "b-materialize@4",
            kind="model",
        )
    return registry
