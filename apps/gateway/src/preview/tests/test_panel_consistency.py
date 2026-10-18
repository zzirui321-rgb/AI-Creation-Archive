from dataclasses import replace

import pytest
from PIL import Image
from preview import ExecutionContext, PlanStep, PreviewEngine, PreviewError, PreviewSettings
from preview.scene_layers import png
from preview.scripts.generic_cases import literal, node, output


def image_artifact(store, name, color):
    return store.put(png(Image.new("RGBA", (96, 64), color)), "image/png", node_id=name, run_id="seed")


def continuity(character, style, background, *, identity="character-v1", seed=4202):
    return {
        "identity_hash": identity,
        "seed": seed,
        "camera": "medium-shot",
        "composition": "character-left-environment-right",
        "character_ref": character["id"],
        "style_ref": style["id"],
        "background_ref": background["id"],
    }


@pytest.mark.asyncio
async def test_panel_context_routes_reference_roles_into_prompt_and_fingerprint(store):
    engine = PreviewEngine(store, settings=PreviewSettings(provider="local_fallback"))
    character = image_artifact(store, "character", "#b34d60")
    style = image_artifact(store, "style", "#d9bd78")
    background = image_artifact(store, "background", "#557f91")
    node_definition = node(
        "panel_2", "image_gen", {"prompt": literal("A traveler crosses the valley")}, params={"seed": 1}
    )
    artifacts = {item["id"]: item for item in (character, style, background)}
    context = ExecutionContext(
        run_id="panel-run",
        cache_key="panel-2",
        resolved_inputs={"consistency_context": continuity(character, style, background)},
    )
    request = engine.prepare_request(node_definition, artifacts, context=context)
    assert request.profile.seed == 4202
    assert request.inputs["reference_artifact_ids"] == [character["id"], style["id"], background["id"]]
    assert request.inputs["consistency_context"]["reference_roles"] == {
        "character": character["id"],
        "style": style["id"],
        "background": background["id"],
    }
    assert request.inputs["consistency_context"]["context_hash"]
    result = await engine.execute_node(node_definition, artifacts, context=context)
    assert result.metadata["seed"] == 4202
    assert result.metadata["consistency_context"]["identity_hash"] == "character-v1"
    assert "Continuity constraints" in result.metadata["resolved_prompt"]

    changed_character = image_artifact(store, "character-v2", "#51a4bd")
    changed_artifacts = {**artifacts, changed_character["id"]: changed_character}
    changed_context = replace(
        context,
        run_id="panel-run-2",
        resolved_inputs={
            "consistency_context": continuity(changed_character, style, background, identity="character-v2")
        },
    )
    changed_request = engine.prepare_request(node_definition, changed_artifacts, context=changed_context)
    assert changed_request.fingerprint != request.fingerprint
    assert changed_request.profile.seed == request.profile.seed


@pytest.mark.asyncio
async def test_comic_incremental_plan_reuses_panel_recomputes_changed_panel_and_rebuilds_layout(store):
    engine = PreviewEngine(store, settings=PreviewSettings(provider="local_fallback"))
    character = image_artifact(store, "character-v2", "#51a4bd")
    style = image_artifact(store, "style-v1", "#d9bd78")
    background = image_artifact(store, "background-v1", "#557f91")
    cached_panel = image_artifact(store, "panel-1-old", "#263d55")
    artifacts = {item["id"]: item for item in (character, style, background, cached_panel)}
    panel_2 = node(
        "panel_2",
        "image_gen",
        {"prompt": literal("Medium shot of the traveler arriving")},
        params={"seed": 901},
    )
    layout = node(
        "layout",
        "layout_compose",
        {"left": output("panel_1"), "right": output("panel_2")},
        params={"layout": {"direction": "vertical", "input_ports": ["left", "right"]}},
    )
    continuity_context = continuity(character, style, background, identity="character-v2")
    steps = [
        # D has already classified panel_1 as a cache hit; only panel_2 is dirty.
        PlanStep(
            panel_2,
            ExecutionContext(
                run_id="panel-2-run",
                cache_key="comic:panel-2",
                decision="RECOMPUTE",
                resolved_inputs={"consistency_context": continuity_context},
            ),
        ),
        PlanStep(
            layout,
            ExecutionContext(run_id="layout-run", cache_key="comic:layout", decision="REBUILD"),
            depends_on=("panel_1", "panel_2"),
        ),
    ]
    result = await engine.execute_plan(
        steps,
        artifacts,
        cached_outputs={"panel_1": {"out": cached_panel["id"]}},
        cached_cache={"panel_1": {"input_hash": "comic:panel-1:hq"}},
        mode="draft",
    )
    assert result.statistics == {
        "reused": 1,
        "recomputed": 1,
        "rebuilt": 1,
        "total": 3,
        "not_executed": 0,
        "reuse_ratio": pytest.approx(1 / 3),
    }
    assert result.cache_by_stage["draft"]["panel_1"]["decision"] == "CACHE_HIT"
    assert result.cache_by_stage["draft"]["panel_2"]["decision"] == "RECOMPUTE"
    assert result.cache_by_stage["draft"]["layout"]["decision"] == "REBUILD"
    panel_result = result.node_results["panel_2"]
    assert panel_result.metadata["consistency_context"]["identity_hash"] == "character-v2"
    assert panel_result.metadata["reference_artifact_ids"] == [character["id"], style["id"], background["id"]]
    layout_result = result.node_results["layout"]
    assert layout_result.cache["decision"] == "REBUILD"
    assert layout_result.metadata["input_artifact_ids"] == [cached_panel["id"], panel_result.artifacts[0]["id"]]


def test_consistency_context_requires_real_reference_artifacts(engine, node, context):
    context = replace(
        context,
        resolved_inputs={
            "consistency_context": {"identity_hash": "v1", "character_ref": "missing-artifact", "seed": 42}
        },
    )
    with pytest.raises(PreviewError, match="available image Artifact"):
        engine.prepare_request(node, {}, context=context)


def test_reference_only_panel_inputs_receive_derived_identity(engine, node, context, store):
    reference = image_artifact(store, "reference", "#4d7899")
    request = engine.prepare_request(
        node,
        {reference["id"]: reference},
        context=replace(context, resolved_inputs={"reference_artifact_ids": [reference["id"]]}),
    )
    assert request.inputs["consistency_context"]["identity_hash"].startswith("references-")
    assert request.inputs["consistency_context"]["context_hash"]
