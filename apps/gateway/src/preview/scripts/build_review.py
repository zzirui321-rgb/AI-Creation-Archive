"""Build and validate B's synthetic integration archives, not E's final showcase."""

import argparse
import asyncio
import copy
import io
import json
import math
import struct
import sys
import wave
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from preview import (  # noqa: E402
    ArtifactStore,
    BaseAdapter,
    ExecutionContext,
    PlanStep,
    PreviewEngine,
    PreviewError,
    PreviewSettings,
)
from preview.artifact_store import atomic_write  # noqa: E402
from preview.contracts import EXTENSION_KEY, AdapterOutput, canonical_json  # noqa: E402
from preview.schema_contract import load_validation_protocol  # noqa: E402
from preview.transfer_bundle import write_transfer_bundle  # noqa: E402

NOW = "2026-10-16T00:00:00Z"
ACTIVE = ["story", "style", "music", "character", "panel_1", "panel_2", "layout"]
REUSED = ["story", "style", "music"]
PROVENANCE = {
    "story": "extracted",
    "style": "recorded",
    "music": "extracted",
    "character": "recorded",
    "panel_1": "recorded",
    "panel_2": "inferred",
    "layout": "recorded",
    "rejected_direction": "inferred",
}


def node(identifier, tool, inputs=None, params=None, branch="main"):
    return {
        "id": identifier,
        "kind": "composition" if tool == "layout_compose" else "model" if tool == "image_gen" else "transform",
        "label": identifier.replace("_", " ").title(),
        "branch_id": branch,
        "provenance": {
            "mode": PROVENANCE[identifier],
            "evidence": [{"source": f"Synthetic review fixture / {PROVENANCE[identifier]} evidence for {identifier}"}],
        },
        "execution": {
            "kind": "model" if tool == "image_gen" else "manual",
            "tool": tool,
            "version": "1",
            "inputs": inputs or {},
            "params": params or {},
            "outputs": ["result"],
        },
    }


def literal(value):
    return {"kind": "literal", "value": value}


def output_of(identifier):
    return {"kind": "node_output", "node_id": identifier, "output": "result"}


def definition():
    nodes = [
        node(
            "story",
            "text_materialize",
            {
                "content": literal(
                    "# Creation notes\n\nA courier follows an illuminated map through a rooftop garden.\n\n"
                    "The story, visual style and music remain reusable when the character changes."
                )
            },
            {"mime_type": "text/markdown"},
        ),
        node("style", "text_materialize", {"content": {"kind": "variable", "variable_id": "style"}}),
        node("music", "review_audio", {"notes": literal("Synthetic test tone / no external model")}),
        node(
            "character",
            "image_gen",
            {"prompt": {"kind": "variable", "variable_id": "character"}, "style": output_of("style")},
        ),
    ]
    for index in (1, 2):
        nodes.append(
            node(
                f"panel_{index}",
                "image_gen",
                {
                    "prompt": {
                        "kind": "template",
                        "template": f"Scene {index}: {{{{character}}}}, {{{{style}}}}",
                        "variables": {"character": "character", "style": "style"},
                    },
                    "reference_artifact_ids": output_of("character"),
                    "story": output_of("story"),
                    "style": output_of("style"),
                },
            )
        )
    nodes.append(
        node(
            "layout",
            "layout_compose",
            {
                "left": output_of("panel_1"),
                "right": output_of("panel_2"),
                "title": literal("Creation study"),
                "captions": literal(["Map discovery", "Rooftop arrival"]),
            },
            {"layout": {"columns": 2}},
        )
    )
    nodes.append(
        node(
            "rejected_direction",
            "image_gen",
            {"prompt": literal("Rejected geometry experiment")},
            {"width": 0, "height": 512},
            branch="trial",
        )
    )
    edges = []
    for item in nodes:
        for name, binding in item["execution"]["inputs"].items():
            if binding["kind"] == "node_output":
                edges.append(
                    {
                        "id": f"{binding['node_id']}-{item['id']}-{name}",
                        "source": binding["node_id"],
                        "target": item["id"],
                        "kind": "data",
                        "source_output": "result",
                        "target_input": name,
                    }
                )
    edges.append({"id": "story-trial", "source": "story", "target": "rejected_direction", "kind": "control"})
    return {
        "revision_id": "review-original",
        "parent_revision_id": None,
        "nodes": nodes,
        "edges": edges,
        "branches": [
            {"id": "main", "parent_id": None, "state": "active"},
            {
                "id": "trial",
                "parent_id": "main",
                "state": "dead_end",
                "reason": "Rejected by the actual stage-policy check: width must be positive",
            },
        ],
        "golden_path": ACTIVE.copy(),
    }


def empty_archive(protocol):
    archive = protocol.empty_archive("Creation study / Original", project_id="review-original", stream_id="review")
    archive["metadata"].update(
        creator="AiCA / B",
        source_mode="mixed",
        external_links=[
            "https://github.com/AiCA-Team/AiCA",
            "https://www.bilibili.com/",
            "https://www.youtube.com/",
            "https://huggingface.co/",
        ],
        created_at=NOW,
        updated_at=NOW,
    )
    archive["graph"] = definition()
    archive["variables"] = {
        "style": {
            "type": "string",
            "default": "neon ink illustration",
            "value": "neon ink illustration",
            "modes": ["keep", "override", "append"],
            "bindings": [{"node_id": "style", "input": "content"}],
            "separator": ", ",
        },
        "character": {
            "type": "string",
            "default": "urban explorer",
            "value": "urban explorer",
            "modes": ["keep", "override", "append"],
            "bindings": [{"node_id": "character", "input": "prompt"}],
            "separator": ", ",
        },
    }
    for variable in archive["variables"].values():
        variable["bindings"].extend({"node_id": f"panel_{index}", "input": "prompt"} for index in (1, 2))
    archive["interactive_questions"] = [
        {
            "id": f"question-{name}",
            "variable_id": name,
            "prompt": f"Choose {name}",
            "modes": ["keep", "override", "append"],
        }
        for name in archive["variables"]
    ]
    archive["harnesses"] = [
        {
            "id": "review-harness",
            "name": "B synthetic fixture builder",
            "version": "2",
            "config": {},
            "state": {},
            "availability": {},
        }
    ]
    archive["agents"] = [
        {
            "id": "review-agent",
            "session_id": "review-session",
            "parent_agent_id": None,
            "harness_id": "review-harness",
            "client": "offline-review",
            "model": {},
        }
    ]
    archive["sessions"] = [
        {
            "id": "review-session",
            "state": "closed",
            "created_at": NOW,
            "consent": {"record": True, "include_children": False, "decided_at": NOW},
        }
    ]
    archive["extensions"][EXTENSION_KEY] = {
        "runs": {},
        "plans": {},
        "fixture": True,
        "golden_path_basis": "Recommended reproduction path from this fixture's successful local execution history",
        "external_links_note": "Platform homepages are integration references, not claims of uploaded assets",
    }
    return archive


def run_record(archive, node_id, run_id, stage, *, state="succeeded", outputs=None, cache=None, inputs=None):
    return {
        "id": run_id,
        "trace_id": f"review-{stage}",
        "node_id": node_id,
        "revision_id": archive["graph"]["revision_id"],
        "session_id": "review-session",
        "agent_id": "review-agent",
        "harness_id": "review-harness",
        "state": state,
        "started_at": NOW,
        "ended_at": NOW,
        "inputs": inputs or {},
        "outputs": outputs or {},
        "artifact_ids": list((outputs or {}).values()),
        "provenance": {"mode": "recorded", "evidence": [{"source": "Observed B fixture execution; synthetic content"}]},
        "preview_stage": stage,
        **({"cache": cache} if cache else {}),
    }


def append_plan(archive, plan, steps):
    ids = {item["id"] for item in archive["artifacts"]}
    archive["artifacts"].extend(item for item in plan.artifacts.values() if item["id"] not in ids)
    for step in steps:
        identifier, run_id = step.node["id"], step.context.run_id
        result = plan.node_results[identifier]
        run = run_record(
            archive,
            identifier,
            run_id,
            result.preview_stage,
            outputs=result.outputs,
            cache=result.cache,
            inputs=plan.resolved_inputs[identifier],
        )
        run["duration_ms"] = result.duration_ms
        archive["runs"].append(run)
        archive["extensions"][EXTENSION_KEY]["runs"][run_id] = result.metadata
    stage = next(iter(plan.cache_by_stage))
    archive["extensions"][EXTENSION_KEY]["plans"][stage] = {
        "statistics": plan.statistics,
        "cache_by_stage": plan.cache_by_stage,
    }


def resolved_values(item, variables):
    result = {}
    for name, binding in item["execution"]["inputs"].items():
        if binding["kind"] == "variable":
            result[name] = variables[binding["variable_id"]]["value"]
        elif binding["kind"] == "template":
            value = binding["template"]
            for placeholder, variable_id in binding["variables"].items():
                value = value.replace("{{" + placeholder + "}}", variables[variable_id]["value"])
            result[name] = value
    return result


class ReviewAudioAdapter(BaseAdapter):
    async def execute(self, request, store):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(
                b"".join(
                    struct.pack(
                        "<h", int(5500 * math.sin(2 * math.pi * 440 * i / 16000) * math.sin(math.pi * i / 32000))
                    )
                    for i in range(32000)
                )
            )
        return AdapterOutput(
            buffer.getvalue(),
            "audio/wav",
            "local_materialize",
            {"is_substitute": False, "description": "Locally synthesized test tone, not a model-generated soundtrack"},
        )


async def rejected_attempt(engine, archive, mode):
    item = next(item for item in archive["graph"]["nodes"] if item["id"] == "rejected_direction")
    stage = "draft" if mode == "draft" else "final"
    run_id = f"{archive['metadata']['project_id']}-rejected-{stage}"
    try:
        await engine.execute_node(item, {}, context=ExecutionContext(run_id, "rejected-geometry", mode=mode, seed=50))
    except PreviewError as error:
        run = run_record(archive, item["id"], run_id, stage, state="failed")
        run["error"] = f"Fixture validation failure: {error.code}"
        archive["runs"].append(run)
    else:
        raise AssertionError("The rejected fixture must fail its dimension check")


async def build(output):
    output.mkdir(parents=True, exist_ok=True)
    protocol = load_validation_protocol()
    store = ArtifactStore(output / "artifacts", uri_prefix="artifacts")
    engine = PreviewEngine(store, settings=PreviewSettings(provider="mock_svg"))
    engine.register_adapter("review_audio", ReviewAudioAdapter())
    original = empty_archive(protocol)
    build_id = "review-" + uuid4().hex[:12]
    original["extensions"][EXTENSION_KEY]["generation_id"] = build_id
    nodes = {item["id"]: item for item in original["graph"]["nodes"]}
    original_plans = {}
    for mode in ("draft", "high_quality"):
        stage = "draft" if mode == "draft" else "final"
        steps = [
            PlanStep(
                nodes[name],
                ExecutionContext(
                    f"original-{name}-{stage}",
                    f"{build_id}:original-{name}",
                    resolved_values(nodes[name], original["variables"]),
                    mode=mode,
                    seed=100 + ACTIVE.index(name),
                    decision="REBUILD" if name == "layout" else "RECOMPUTE",
                ),
            )
            for name in ACTIVE
        ]
        plan = await engine.execute_plan(steps, {}, mode=mode)
        append_plan(original, plan, steps)
        original_plans[stage] = plan
        await rejected_attempt(engine, original, mode)
    fork = copy.deepcopy(original)
    fork["metadata"].update(project_id="review-fork", title="Creation study / Fork")
    snapshot = {key: original[key] for key in ("graph", "variables", "interactive_questions", "variable_operations")}
    historical = store.put(canonical_json(snapshot), "application/json", node_id=None, run_id=None)
    fork["artifacts"].append(historical)
    fork["history"] = [{"kind": "revision", "artifact_id": historical["id"], "revision_id": "review-original"}]
    fork["graph"].update(revision_id="review-fork", parent_revision_id="review-original")
    fork["variable_operations"] = [
        {"variable_id": "style", "mode": "keep"},
        {"variable_id": "character", "mode": "override", "value": "orbital botanist"},
        {"variable_id": "character", "mode": "append", "value": "carrying glowing seed pods"},
    ]
    fork["variables"] = protocol.resolve_variable_operations(original["variables"], fork["variable_operations"])
    for mode in ("draft", "high_quality"):
        stage = "draft" if mode == "draft" else "final"
        previous = original_plans[stage]
        cached_outputs = {name: previous.outputs[name] for name in REUSED}
        initial = {
            identifier: previous.artifacts[identifier]
            for values in cached_outputs.values()
            for identifier in values.values()
        }
        metadata = {}
        for name in REUSED:
            source = previous.node_results[name]
            run_id = f"fork-{name}-{stage}"
            cache = {**source.cache, "decision": "CACHE_HIT"}
            fork["runs"].append(run_record(fork, name, run_id, stage, outputs=source.outputs, cache=cache))
            fork["extensions"][EXTENSION_KEY]["runs"][run_id] = source.metadata
            metadata.update({identifier: source.metadata for identifier in source.outputs.values()})
        dirty = [name for name in ACTIVE if name not in REUSED]
        steps = [
            PlanStep(
                nodes[name],
                ExecutionContext(
                    f"fork-{name}-{stage}",
                    f"{build_id}:fork-{name}",
                    resolved_values(nodes[name], fork["variables"]),
                    mode=mode,
                    seed=100 + ACTIVE.index(name),
                    decision="REBUILD" if name == "layout" else "RECOMPUTE",
                ),
            )
            for name in dirty
        ]
        plan = await engine.execute_plan(
            steps,
            initial,
            cached_outputs=cached_outputs,
            cached_cache={name: previous.node_results[name].cache for name in REUSED},
            artifact_metadata=metadata,
            mode=mode,
        )
        append_plan(fork, plan, steps)
        await rejected_attempt(engine, fork, mode)
    validation = {}
    for name, archive in (("original", original), ("fork", fork)):
        validation[name] = protocol.validate_archive(archive)
        if not validation[name]["valid"]:
            raise ValueError(f"{name}: {validation[name]['errors']}")
    extras = [
        store.put(
            b'{"protocol":".aica","reuse":"content hashes","stages":["draft","final"]}',
            "application/json",
            node_id=None,
            run_id=None,
        ),
        store.put(
            b"function remix(input) { return { ...input, inherited: true }; }",
            "text/javascript",
            node_id=None,
            run_id=None,
        ),
    ]
    payload = {"original": original, "fork": fork, "extras": extras, "validation": validation}
    atomic_write(output / "original.aica.json", canonical_json(original))
    atomic_write(output / "fork.aica.json", canonical_json(fork))
    atomic_write(output / "archives.json", canonical_json(payload))
    atomic_write(output / "validation.json", canonical_json(validation))
    write_transfer_bundle(original, store, output / "original.aica.zip")
    write_transfer_bundle(fork, store, output / "fork.aica.zip")
    print(
        json.dumps(
            {
                "path": str(output / "archives.json"),
                "validation": validation,
                "fork_statistics": fork["extensions"][EXTENSION_KEY]["plans"],
            },
            indent=2,
        )
    )
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(build(parser.parse_args().output.resolve()))
