import asyncio
import copy
from dataclasses import replace

import pytest
from preview import ExecutionContext, PlanExecutionError, PlanStep, PreviewError


def derived(node, identifier, tool="text_materialize", inputs=None, params=None):
    result = copy.deepcopy(node)
    result.update(id=identifier, kind="composition" if tool == "layout_compose" else "transform")
    result["execution"].update(tool=tool, inputs=inputs or {}, params=params or {}, outputs=["result"])
    return result


@pytest.mark.asyncio
async def test_plan_injects_outputs_and_preserves_cached_inputs(engine, node, png_bytes):
    cached = engine.store.put(png_bytes, "image/png", node_id="cached", run_id="cached-run")
    first = derived(node, "first", inputs={"content": {"kind": "literal", "value": "New text result"}})
    layout = derived(
        node,
        "layout",
        "layout_compose",
        inputs={
            "old": {"kind": "node_output", "node_id": "cached", "output": "image"},
            "new": {"kind": "node_output", "node_id": "first", "output": "result"},
        },
        params={"layout": {"columns": 2, "cell_width": 160, "cell_height": 120}},
    )
    steps = [
        PlanStep(first, ExecutionContext("first-run", "first")),
        PlanStep(layout, ExecutionContext("layout-run", "layout", decision="REBUILD"), depends_on=("first",)),
    ]
    initial = {cached["id"]: cached}
    before = copy.deepcopy(initial)
    result = await engine.execute_plan(steps, initial, cached_outputs={"cached": {"image": cached["id"]}})
    assert result.statistics == {
        "reused": 1,
        "recomputed": 1,
        "rebuilt": 1,
        "total": 3,
        "not_executed": 0,
        "reuse_ratio": 1 / 3,
    }
    assert result.resolved_inputs["layout"]["new"] == result.outputs["first"]["result"]
    regions = result.node_results["layout"].metadata["regions"]
    assert [region["node_id"] for region in regions] == ["cached", "first"]
    assert [region["cache_decision"] for region in regions] == ["CACHE_HIT", "RECOMPUTE"]
    assert initial == before
    assert set(result.cache_by_stage) == {"draft"}
    assert engine.store.read(result.artifacts[result.outputs["layout"]["result"]]).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_plan_preflights_order_before_any_execution(engine, node, monkeypatch):
    producer = derived(node, "producer", inputs={"content": {"kind": "literal", "value": "content"}})
    consumer = derived(
        node, "consumer", inputs={"content": {"kind": "node_output", "node_id": "producer", "output": "result"}}
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("Invalid plans must fail before dispatch")

    monkeypatch.setattr(engine, "execute_node", forbidden)
    with pytest.raises(PreviewError) as error:
        await engine.execute_plan(
            [
                PlanStep(consumer, ExecutionContext("consumer-run", "consumer")),
                PlanStep(producer, ExecutionContext("producer-run", "producer")),
            ],
            {},
        )
    assert error.value.code == "invalid_plan_order"


@pytest.mark.asyncio
async def test_partial_plan_retained_and_downstream_not_run(engine, node):
    good = derived(node, "good", inputs={"content": {"kind": "literal", "value": "ok"}})
    bad = derived(node, "bad")
    after = derived(node, "after", inputs={"content": {"kind": "literal", "value": "never"}})
    steps = [PlanStep(item, ExecutionContext(f"run-{item['id']}", item["id"])) for item in (good, bad, after)]
    with pytest.raises(PlanExecutionError) as error:
        await engine.execute_plan(steps, {})
    assert error.value.failed_node_id == "bad"
    assert list(error.value.partial_result.node_results) == ["good"]
    assert error.value.partial_result.statistics["not_executed"] == 2


@pytest.mark.asyncio
async def test_plan_rejects_mixed_stages_and_duplicate_nodes(engine, node):
    first = PlanStep(node, ExecutionContext("run-first", "first", seed=7))
    other = derived(node, "other", inputs={"content": {"kind": "literal", "value": "content"}})
    second = PlanStep(other, ExecutionContext("run-second", "second", mode="final"))
    with pytest.raises(PreviewError, match="one stage"):
        await engine.execute_plan([first, second], {})
    with pytest.raises(PreviewError, match="unique"):
        await engine.execute_plan([first, first], {})


@pytest.mark.asyncio
async def test_empty_and_all_cached_plan(engine, png_bytes):
    empty = await engine.execute_plan([], {}, mode="final")
    assert empty.statistics["total"] == 0
    assert set(empty.cache_by_stage) == {"final"}
    old = engine.store.put(png_bytes, "image/png", node_id="old", run_id="old-run")
    cached = await engine.execute_plan(
        [], {old["id"]: old}, cached_outputs={"old": {"result": old["id"]}}, mode="final"
    )
    assert cached.statistics["reuse_ratio"] == 1
    assert not cached.node_results


@pytest.mark.asyncio
async def test_final_plan_reuses_seed_and_stage_keys(engine, node):
    context = ExecutionContext("draft-run", "stable-key", seed=23)
    draft = await engine.execute_plan([PlanStep(node, context)], {})
    final = await engine.execute_plan([PlanStep(node, replace(context, run_id="final-run", mode="high_quality"))], {})
    assert draft.node_results[node["id"]].metadata["seed"] == final.node_results[node["id"]].metadata["seed"]
    assert draft.cache_by_stage["draft"][node["id"]]["input_hash"].endswith(":draft")
    assert final.cache_by_stage["final"][node["id"]]["input_hash"].endswith(":hq")


@pytest.mark.asyncio
async def test_same_content_new_instances_keep_cache_and_rebind_regions(engine, node, png_bytes):
    old = engine.store.put(png_bytes, "image/png", node_id="old", run_id="old-run")
    new = engine.store.put(png_bytes, "image/png", node_id="new", run_id="new-run")
    layout = derived(
        node, "layout", "layout_compose", inputs={}, params={"layout": {"cell_width": 160, "cell_height": 120}}
    )
    first = await engine.execute_node(
        layout,
        {old["id"]: old},
        context=ExecutionContext(
            "layout-first", "layout-content", {"artifact_ids": [old["id"]]}, seed=3, decision="REBUILD"
        ),
    )
    second = await engine.execute_node(
        layout,
        {new["id"]: new},
        context=ExecutionContext(
            "layout-second", "layout-content", {"artifact_ids": [new["id"]]}, seed=3, decision="REBUILD"
        ),
    )
    assert first.artifacts[0]["sha256"] == second.artifacts[0]["sha256"]
    assert second.cache["decision"] == "CACHE_HIT"
    assert second.metadata["regions"][0]["artifact_id"] == new["id"]
    assert second.metadata["regions"][0]["node_id"] == "new"


@pytest.mark.asyncio
async def test_plan_lifecycle_hooks_wrap_execution_and_publish_outputs(engine, node):
    events = []
    text = derived(node, "text", inputs={"content": {"kind": "literal", "value": "recordable result"}})

    async def start(step, context):
        events.append(("started", context.run_id))
        assert context.resolved_inputs["content"] == "recordable result"

    async def complete(step, result, context):
        assert engine.store.read(result.artifacts[0]) == b"recordable result"
        events.append(("artifact-recorded", result.artifacts[0]["producer_run_id"]))
        events.append(("finished", context.run_id))

    await engine.execute_plan(
        [PlanStep(text, ExecutionContext("recordable-run", "recordable"))],
        {},
        on_step_start=start,
        on_step_complete=complete,
    )
    assert events == [
        ("started", "recordable-run"),
        ("artifact-recorded", "recordable-run"),
        ("finished", "recordable-run"),
    ]


@pytest.mark.asyncio
async def test_failed_completion_hook_retains_output_without_replaying(engine, node):
    text = derived(node, "text", inputs={"content": {"kind": "literal", "value": "durable output"}})

    async def unavailable_recorder(step, result, context):
        raise OSError("recorder unavailable")

    with pytest.raises(PlanExecutionError) as error:
        await engine.execute_plan(
            [PlanStep(text, ExecutionContext("durable-run", "durable"))], {}, on_step_complete=unavailable_recorder
        )
    assert error.value.code == "plan_observer_failed"
    assert list(error.value.partial_result.node_results) == ["text"]
    assert error.value.partial_result.statistics["not_executed"] == 0


@pytest.mark.asyncio
async def test_start_hook_failure_prevents_generation(engine, node, monkeypatch):
    async def denied(step, context):
        raise PreviewError("not_authorized", "Runtime execution authorization is missing")

    async def forbidden(*args, **kwargs):
        pytest.fail("Execution must not run after a rejected start hook")

    monkeypatch.setattr(engine, "execute_node", forbidden)
    with pytest.raises(PlanExecutionError) as error:
        await engine.execute_plan([PlanStep(node, ExecutionContext("denied-run", "denied"))], {}, on_step_start=denied)
    assert error.value.code == "not_authorized"
    assert error.value.partial_result.statistics["not_executed"] == 1


@pytest.mark.asyncio
async def test_plan_cancellation_propagates_without_dispatching_later_steps(engine, node, monkeypatch):
    calls = []

    async def cancelled(item, artifacts, *, context):
        calls.append(item["id"])
        raise asyncio.CancelledError()

    monkeypatch.setattr(engine, "execute_node", cancelled)
    later = derived(node, "later", inputs={"content": {"kind": "literal", "value": "never"}})
    with pytest.raises(asyncio.CancelledError):
        await engine.execute_plan(
            [
                PlanStep(node, ExecutionContext("cancel-run", "cancel")),
                PlanStep(later, ExecutionContext("later-run", "later")),
            ],
            {},
        )
    assert calls == [node["id"]]
