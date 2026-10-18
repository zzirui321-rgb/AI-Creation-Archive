from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping, Sequence

import httpx

from .adapters import BaseAdapter, CompositeLayoutAdapter, ImageGenAdapter, TextArtifactAdapter
from .artifact_store import ArtifactStore
from .consistency import consistency_seed, ensure_reference_consistency_context, normalize_consistency_inputs
from .contracts import (
    ExecutionContext,
    ExecutionRequest,
    Mode,
    PlanResult,
    PlanStep,
    PreviewError,
    PreviewResult,
    PreviewSettings,
    digest,
)
from .plan import execute_plan
from .schema_contract import ProtocolContract
from .stage_policy import StageProfilePolicy


class PreviewEngine:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        settings: PreviewSettings | None = None,
        contract: ProtocolContract | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        segmenter=None,
    ):
        self.store = store
        self.settings = settings or PreviewSettings.from_env()
        self.contract = contract or ProtocolContract()
        self.policy = StageProfilePolicy(self.settings)
        from .semantic_edit import HTTPSegmenter

        self.segmenter = segmenter or HTTPSegmenter()
        self.adapters: dict[str, BaseAdapter] = {}
        image, layout, text = (
            ImageGenAdapter(self.settings, transport=transport),
            CompositeLayoutAdapter(),
            TextArtifactAdapter(),
        )
        for tool in ("image_gen", "image-provider"):
            self.register_adapter(tool, image)
        for tool in ("layout_compose", "compose", "layout"):
            self.register_adapter(tool, layout)
        for tool in ("text_materialize", "json_materialize"):
            self.register_adapter(tool, text)

    def register_adapter(self, tool: str, adapter: BaseAdapter) -> None:
        if not tool:
            raise ValueError("Tool ID must not be empty")
        self.adapters[tool] = adapter

    async def propose_image_edit(self, original_artifact, intent):
        from .semantic_edit import propose_image_edit

        return await propose_image_edit(self, original_artifact, intent)

    async def execute_proposed_edit(
        self, node, artifacts, *, context, original_artifact_id, proposal_id, candidate_id=None
    ):
        from .semantic_edit import execute_proposed_edit

        return await execute_proposed_edit(
            self,
            node,
            artifacts,
            context=context,
            original_artifact_id=original_artifact_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
        )

    async def execute_image_edit(
        self,
        node: Mapping[str, Any],
        artifacts: Mapping[str, Mapping[str, Any]],
        *,
        context: ExecutionContext,
        original_artifact_id: str,
        mask_artifact_id: str | None = None,
        region: Sequence[float] | None = None,
    ) -> PreviewResult:
        """Execute one existing image node with an explicit local edit boundary."""
        if (mask_artifact_id is None) == (region is None):
            raise PreviewError("missing_edit_boundary", "Provide exactly one mask_artifact_id or region")
        edited = copy.deepcopy(dict(node))
        if edited.get("execution", {}).get("tool") not in {"image_gen", "image-provider"}:
            raise PreviewError("unsupported_edit_tool", "Local image edits require an image generation node")
        execution = edited["execution"]
        params = execution.setdefault("params", {})
        params.update(edit_mode="masked_replace", layer_role="scene")
        params.pop("edit_region", None)
        inputs = copy.deepcopy(dict(context.resolved_inputs))
        inputs.pop("edit_mask", None)
        execution["inputs"].pop("edit_mask", None)
        inputs["original_image"] = original_artifact_id
        if mask_artifact_id is not None:
            inputs["edit_mask"] = mask_artifact_id
        else:
            params["edit_region"] = list(region)
        return await self.execute_node(edited, artifacts, context=replace(context, resolved_inputs=inputs))

    async def execute_plan(
        self,
        steps: Sequence[PlanStep],
        initial_artifacts: Mapping[str, Mapping[str, Any]],
        *,
        cached_outputs: Mapping[str, Mapping[str, str]] | None = None,
        cached_cache: Mapping[str, Mapping[str, Any]] | None = None,
        artifact_metadata: Mapping[str, dict[str, Any]] | None = None,
        mode: Mode | None = None,
        on_step_start: Callable[[PlanStep, ExecutionContext], Awaitable[None]] | None = None,
        on_step_complete: Callable[[PlanStep, PreviewResult, ExecutionContext], Awaitable[None]] | None = None,
    ) -> PlanResult:
        return await execute_plan(
            self,
            steps,
            initial_artifacts,
            cached_outputs=cached_outputs,
            cached_cache=cached_cache,
            artifact_metadata=artifact_metadata,
            mode=mode,
            on_step_start=on_step_start,
            on_step_complete=on_step_complete,
        )

    def prepare_request(
        self, node: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]], *, context: ExecutionContext
    ) -> ExecutionRequest:
        self.contract.validate("node", node)
        self.contract.validate("id", context.run_id)
        if not context.cache_key:
            raise PreviewError("missing_cache_key", "Runtime must provide a base cache key")
        if context.decision not in {"CACHE_HIT", "RECOMPUTE", "REBUILD"}:
            raise PreviewError("invalid_decision", "Unknown Runtime cache decision")
        execution = node.get("execution")
        if not execution or execution["tool"] not in self.adapters:
            raise PreviewError("unsupported_tool", "No registered adapter for this node's tool")
        if len(execution["outputs"]) != 1:
            raise PreviewError("unsupported_outputs", "Built-in adapters require one declared output port")
        for identifier, artifact in artifacts.items():
            self.contract.validate("artifact", artifact)
            if identifier != artifact["id"]:
                raise PreviewError("invalid_artifact_index", "Artifact dictionary keys must match IDs")
        inputs = copy.deepcopy(dict(context.resolved_inputs))
        for name, binding in execution["inputs"].items():
            if name in inputs:
                continue
            if binding["kind"] == "literal":
                inputs[name] = copy.deepcopy(binding["value"])
            elif binding["kind"] == "artifact":
                inputs[name] = binding["artifact_id"]
            else:
                raise PreviewError("unresolved_input", f"Runtime must resolve input {name}")
        consistency = None
        if "consistency_context" in inputs or "reference_artifact_ids" in inputs:
            inputs, consistency = ensure_reference_consistency_context(inputs, artifacts)
            inputs, consistency = normalize_consistency_inputs(inputs, artifacts)
            context = replace(context, seed=consistency_seed(consistency, context.seed))
        profile = self.policy.resolve(node, context)
        tool = execution["tool"]
        edit_mode = execution.get("params", {}).get("edit_mode")
        if edit_mode is not None:
            from .image_edit import resolve_image_edit

            if edit_mode != "masked_replace" or tool not in {"image_gen", "image-provider"}:
                raise PreviewError("unsupported_edit_mode", "Image editing requires masked_replace on an image node")
            if profile.params.get("layer_role", "scene") != "scene":
                raise PreviewError("invalid_edit_role", "A full-image edit cannot also extract a foreground layer")
            if not isinstance(profile.params.get("edit_reference", True), bool):
                raise PreviewError("invalid_edit_reference", "edit_reference must be a boolean")
            operation = profile.params.get("edit_operation", "replace")
            if operation not in {"replace", "overlay"}:
                raise PreviewError("invalid_edit_operation", "Expected replace or overlay")
            if operation == "overlay":
                profile = replace(profile, params={**profile.params, "edit_reference": False})
            edit = resolve_image_edit(inputs, profile.params, artifacts, self.store)
            width, height = edit.original.size
            profile = replace(
                profile, width=width, height=height, params={**profile.params, "width": width, "height": height}
            )

        def content_identity(value):
            if isinstance(value, str) and value in artifacts:
                artifact = artifacts[value]
                if artifact["sha256"] is None:
                    raise PreviewError("unverified_upstream", "Execution dependencies require content hashes")
                return {"artifact_sha256": artifact["sha256"], "mime_type": artifact["mime_type"]}
            if isinstance(value, dict):
                return {key: content_identity(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [content_identity(item) for item in value]
            return value

        fingerprint = digest(
            {
                "tool": tool,
                "version": execution["version"],
                "adapter": self.adapters[tool].version,
                "inputs": content_identity(inputs),
                "params": profile.params,
                "model": profile.model,
                "stage": profile.schema_stage,
                "upstream": sorted((value["mime_type"], value["sha256"] or key) for key, value in artifacts.items()),
                "endpoint": self.settings.api_base,
                "api_style": self.settings.api_style,
                "consistency": consistency,
            }
        )
        return ExecutionRequest(
            copy.deepcopy(dict(node)),
            copy.deepcopy(dict(artifacts)),
            inputs,
            context,
            profile,
            f"{context.cache_key}:{profile.cache_suffix}",
            fingerprint,
        )

    async def execute_node(
        self, node: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]], *, context: ExecutionContext
    ) -> PreviewResult:
        started = time.monotonic()
        if context.decision == "CACHE_HIT":
            raise PreviewError("runtime_cache_hit", "Runtime should reuse existing artifacts without invoking B")
        request = self.prepare_request(node, artifacts, context=context)
        output = await self.adapters[node["execution"]["tool"]].execute(request, self.store)
        artifact = await asyncio.to_thread(
            self.store.put, output.data, output.mime_type, node_id=node["id"], run_id=context.run_id
        )
        self.contract.validate("artifact", artifact)
        await asyncio.to_thread(self.store.save_cache, request, artifact, output)
        decision = "CACHE_HIT" if output.cache_hit else context.decision
        metadata = {
            **output.metadata,
            "source": output.source,
            "stage": request.profile.schema_stage,
            "seed": request.profile.seed,
            "width": request.profile.width,
            "height": request.profile.height,
            "model": request.profile.model,
            "effective_params": request.profile.params,
            **(
                {"consistency_context": request.inputs["consistency_context"]}
                if "consistency_context" in request.inputs
                else {}
            ),
            "stage_key": request.stage_key,
            "fingerprint": request.fingerprint,
            "artifact_ids": [artifact["id"]],
        }
        return PreviewResult(
            [artifact],
            {node["execution"]["outputs"][0]: artifact["id"]},
            {"decision": decision, "input_hash": request.stage_key, "artifact_ids": [artifact["id"]]},
            request.profile.schema_stage,
            metadata,
            round((time.monotonic() - started) * 1000, 3),
        )
