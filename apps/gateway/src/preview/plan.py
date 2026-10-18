from __future__ import annotations

import copy
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Sequence

from .contracts import (
    ExecutionContext,
    JsonObject,
    Mode,
    PlanExecutionError,
    PlanResult,
    PlanStep,
    PreviewError,
    PreviewResult,
)

if TYPE_CHECKING:
    from .engine import PreviewEngine


def stage_name(mode: Mode) -> str:
    if mode not in {"draft", "final", "high_quality"}:
        raise PreviewError("invalid_stage", "Expected draft, high_quality or final")
    return "draft" if mode == "draft" else "final"


def artifact_references(value: Any, artifacts: Mapping[str, Any]) -> list[str]:
    if isinstance(value, str):
        return [value] if value in artifacts else []
    if isinstance(value, Mapping):
        return list(
            dict.fromkeys(identifier for item in value.values() for identifier in artifact_references(item, artifacts))
        )
    if isinstance(value, (tuple, list)):
        return list(dict.fromkeys(identifier for item in value for identifier in artifact_references(item, artifacts)))
    return []


async def execute_plan(
    engine: PreviewEngine,
    steps: Sequence[PlanStep],
    initial_artifacts: Mapping[str, Mapping[str, Any]],
    *,
    cached_outputs: Mapping[str, Mapping[str, str]] | None = None,
    cached_cache: Mapping[str, Mapping[str, Any]] | None = None,
    artifact_metadata: Mapping[str, JsonObject] | None = None,
    mode: Mode | None = None,
    on_step_start: Callable[[PlanStep, ExecutionContext], Awaitable[None]] | None = None,
    on_step_complete: Callable[[PlanStep, PreviewResult, ExecutionContext], Awaitable[None]] | None = None,
) -> PlanResult:
    started = time.monotonic()
    steps = list(steps)
    stage = stage_name(mode or (steps[0].context.mode if steps else "draft"))
    known = copy.deepcopy(dict(initial_artifacts))
    outputs = copy.deepcopy(dict(cached_outputs or {}))
    metadata = copy.deepcopy(dict(artifact_metadata or {}))
    for identifier, artifact in known.items():
        engine.contract.validate("artifact", artifact)
        if identifier != artifact["id"]:
            raise PreviewError("invalid_artifact_index", "Artifact dictionary keys must match IDs")
    ports: dict[str, set[str]] = {}
    cache: dict[str, JsonObject] = {}
    for node_id, values in outputs.items():
        engine.contract.validate("id", node_id)
        if (
            not isinstance(values, Mapping)
            or not values
            or any(not isinstance(value, str) or value not in known for value in values.values())
        ):
            raise PreviewError("invalid_cached_outputs", "Cached output ports must reference initial artifacts")
        ports[node_id] = set(values)
        cache[node_id] = {"decision": "CACHE_HIT", "artifact_ids": list(dict.fromkeys(values.values()))}
        prior_hash = (cached_cache or {}).get(node_id, {}).get("input_hash")
        if prior_hash is not None:
            if not isinstance(prior_hash, str):
                raise PreviewError("invalid_cached_outputs", "Cached input_hash must be a string")
            cache[node_id]["input_hash"] = prior_hash
    run_ids: set[str] = set()
    for step in steps:
        node, context = step.node, step.context
        engine.contract.validate("node", node)
        engine.contract.validate("id", context.run_id)
        node_id = node["id"]
        if node_id in ports or context.run_id in run_ids:
            raise PreviewError("duplicate_plan_step", "Plan node IDs and Run IDs must be unique")
        if context.decision not in {"RECOMPUTE", "REBUILD"} or stage_name(context.mode) != stage:
            raise PreviewError("invalid_plan", "A plan contains dirty steps from exactly one stage")
        execution = node.get("execution", {})
        if execution.get("tool") not in engine.adapters or len(execution.get("outputs", [])) != 1:
            raise PreviewError("unsupported_tool", "Plan uses an unsupported tool or output shape")
        if not context.cache_key or any(dependency not in ports for dependency in step.depends_on):
            raise PreviewError("invalid_plan_order", "Control dependencies must be cached or precede this step")
        for name, binding in execution["inputs"].items():
            if binding["kind"] == "node_output":
                if binding["output"] not in ports.get(binding["node_id"], set()):
                    raise PreviewError("invalid_plan_order", "Data dependencies must precede their consumers")
            elif binding["kind"] in {"variable", "template"} and name not in context.resolved_inputs:
                raise PreviewError("unresolved_input", f"Runtime must resolve variable/template input {name}")
            elif binding["kind"] == "artifact" and binding["artifact_id"] not in known:
                raise PreviewError("missing_input", "Explicit artifact binding is absent from initial artifacts")
        ports[node_id] = set(execution["outputs"])
        run_ids.add(context.run_id)
    result = PlanResult(known, outputs, {}, {}, {stage: cache}, {}, 0)

    def summarize() -> PlanResult:
        decisions = [entry["decision"] for entry in cache.values()]
        total = len(outputs)
        planned_total = len(cached_outputs or {}) + len(steps)
        reused = decisions.count("CACHE_HIT")
        result.statistics = {
            "reused": reused,
            "recomputed": decisions.count("RECOMPUTE"),
            "rebuilt": decisions.count("REBUILD"),
            "total": planned_total,
            "not_executed": planned_total - total,
            "reuse_ratio": reused / planned_total if planned_total else 0,
        }
        result.duration_ms = round((time.monotonic() - started) * 1000, 3)
        return result

    states = {node_id: "CACHE_HIT" for node_id in outputs}
    prompts: dict[str, str] = {}
    for step in steps:
        node_id = step.node["id"]
        inputs = copy.deepcopy(dict(step.context.resolved_inputs))
        for name, binding in step.node["execution"]["inputs"].items():
            if binding["kind"] == "node_output":
                inputs[name] = outputs[binding["node_id"]][binding["output"]]
            elif binding["kind"] == "artifact":
                inputs[name] = binding["artifact_id"]
            elif binding["kind"] == "literal":
                inputs.setdefault(name, copy.deepcopy(binding["value"]))
        dependencies = {identifier: known[identifier] for identifier in artifact_references(inputs, known)}
        context = replace(
            step.context,
            resolved_inputs=inputs,
            node_states={**step.context.node_states, **states},
            node_prompts={**step.context.node_prompts, **prompts},
            artifact_metadata={**step.context.artifact_metadata, **metadata},
        )
        try:
            if on_step_start is not None:
                await on_step_start(step, context)
            item = await engine.execute_node(step.node, dependencies, context=context)
        except Exception as error:
            raise PlanExecutionError(
                node_id, summarize(), error.code if isinstance(error, PreviewError) else "plan_execution_failed"
            ) from error
        result.node_results[node_id] = item
        result.resolved_inputs[node_id] = inputs
        outputs[node_id] = item.outputs
        cache[node_id] = item.cache
        states[node_id] = item.cache["decision"]
        prompts[node_id] = str(inputs.get("prompt", ""))
        for artifact in item.artifacts:
            known[artifact["id"]] = artifact
            metadata[artifact["id"]] = item.metadata
        if on_step_complete is not None:
            try:
                await on_step_complete(step, item, context)
            except Exception as error:
                raise PlanExecutionError(node_id, summarize(), "plan_observer_failed") from error
    return summarize()
