import asyncio
import base64
import copy
import hashlib
import importlib.util
import io
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from PIL import Image
from preview import ExecutionContext, PreviewEngine, PreviewError, PreviewSettings, resolve_stage_profile
from preview.artifact_store import ArtifactStore
from preview.contracts import AdapterOutput


@pytest.mark.parametrize(
    "mode,suffix,schema_stage,steps",
    [
        ("draft", ":draft", "draft", 4),
        ("high_quality", ":hq", "final", 28),
        ("final", ":hq", "final", 28),
    ],
)
def test_stage_profiles(engine, node, context, mode, suffix, schema_stage, steps):
    request = engine.prepare_request(node, {}, context=replace(context, mode=mode))
    assert request.stage_key == context.cache_key + suffix
    assert request.profile.schema_stage == schema_stage
    assert request.profile.params["steps"] == steps
    assert request.profile.seed == 73
    assert request.profile.width * 512 == request.profile.height * 768


@pytest.mark.parametrize("width,height", [(768, 512), (512, 768), (1024, 1024), (1600, 900)])
def test_aspect_ratio_and_seed_preserved(node, context, width, height):
    node["execution"]["params"].update(width=width, height=height, camera="wide", reference_strength=0.7)
    draft = resolve_stage_profile(node, replace(context, seed=None), PreviewSettings())
    hq = resolve_stage_profile(node, replace(context, mode="high_quality", seed=None), PreviewSettings())
    assert draft.seed == hq.seed
    assert draft.width * height == draft.height * width
    assert hq.width * height == hq.height * width
    assert hq.width >= draft.width and hq.height >= draft.height
    assert draft.params["camera"] == hq.params["camera"] == "wide"
    assert draft.params["reference_strength"] == hq.params["reference_strength"] == 0.7


@pytest.mark.asyncio
async def test_fallback_stages_files_and_cache(engine, node, context):
    before = copy.deepcopy(node)
    draft = await engine.execute_node(node, {}, context=context)
    cached = await engine.execute_node(node, {}, context=replace(context, run_id="run-cached"))
    final = await engine.execute_node(node, {}, context=replace(context, mode="high_quality", run_id="run-final"))
    assert cached.cache["decision"] == "CACHE_HIT"
    assert draft.cache["decision"] == final.cache["decision"] == "RECOMPUTE"
    assert draft.artifacts[0]["sha256"] == cached.artifacts[0]["sha256"] != final.artifacts[0]["sha256"]
    assert draft.metadata["is_substitute"]
    assert draft.metadata["seed"] == final.metadata["seed"]
    for result in (draft, final):
        artifact = result.artifacts[0]
        data = engine.store.read(artifact)
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        with Image.open(io.BytesIO(data)) as image:
            assert image.info["seed"] == "73"
            assert image.info["stage"] == result.preview_stage
            assert image.info["resolvedPrompt"] == node["execution"]["inputs"]["prompt"]["value"]
    assert node == before


@pytest.mark.asyncio
async def test_changed_prompt_and_model_invalidate_cache(engine, node, context):
    first = await engine.execute_node(node, {}, context=context)
    node["execution"]["inputs"]["prompt"]["value"] = "A quiet forest under sunlight"
    second = await engine.execute_node(node, {}, context=context)
    assert second.cache["decision"] == "RECOMPUTE"
    assert first.metadata["fingerprint"] != second.metadata["fingerprint"]
    assert first.artifacts[0]["sha256"] != second.artifacts[0]["sha256"]
    node["execution"]["model"] = "another-model"
    third = await engine.execute_node(node, {}, context=context)
    assert third.cache["decision"] == "RECOMPUTE"


@pytest.mark.asyncio
async def test_live_timeout_falls_back(store, node, context):
    async def slow(_request):
        await asyncio.sleep(0.2)
        return httpx.Response(500)

    settings = PreviewSettings(
        api_base="https://test.invalid/v1", api_key="test-key", draft_model="draft", timeout_seconds=0.02
    )
    engine = PreviewEngine(store, settings=settings, transport=httpx.MockTransport(slow))
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "local_fallback"
    assert "timeout" in result.metadata["fallback_reasons"]
    assert store.read(result.artifacts[0]).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_missing_key_no_network(store, node, context):
    def no_network(_request):
        pytest.fail("Missing API key should never perform an HTTP request")

    engine = PreviewEngine(
        store, settings=PreviewSettings(api_base="https://test.invalid/v1"), transport=httpx.MockTransport(no_network)
    )
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "local_fallback"
    assert "live_api_unconfigured" in result.metadata["fallback_reasons"]


@pytest.mark.asyncio
async def test_fallback_cache_does_not_hide_recovered_live_api(store, node, context, png_bytes):
    available = False
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == "Bearer test-key"
        if not available:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes).decode()}]})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(api_base="https://test.invalid/v1", api_key="test-key", draft_model="draft"),
        transport=httpx.MockTransport(respond),
    )
    offline = await engine.execute_node(node, {}, context=context)
    available = True
    online = await engine.execute_node(node, {}, context=context)
    cached = await engine.execute_node(node, {}, context=context)
    assert calls == 2
    assert offline.metadata["source"] == "local_fallback"
    assert online.metadata["source"] == cached.metadata["source"] == "live_api"
    assert online.artifacts[0]["sha256"] == hashlib.sha256(png_bytes).hexdigest()
    assert cached.cache["decision"] == "CACHE_HIT"
    assert "test-key" not in json.dumps(online.to_dict())


@pytest.mark.asyncio
async def test_prewarm_hit_and_corrupt_file_fallback(store, node, context, png_bytes):
    engine = PreviewEngine(store, settings=PreviewSettings())
    request = engine.prepare_request(node, {}, context=context)
    artifact = store.put(png_bytes, "image/png", node_id=node["id"], run_id=context.run_id)
    manifest = store.prewarm(request, artifact, metadata={"origin_source": "imported_verified"})
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "prewarmed_cache"
    assert result.cache["decision"] == "CACHE_HIT"
    entry = json.loads(manifest.read_text())
    store.local_path(entry["artifact"]["uri"]).write_bytes(b"corrupt")
    fallback = await engine.execute_node(node, {}, context=context)
    assert fallback.metadata["source"] == "local_fallback"
    assert fallback.cache["decision"] == "RECOMPUTE"


@pytest.mark.asyncio
async def test_prewarm_stage_and_fingerprint_isolation(store, node, context, png_bytes):
    engine = PreviewEngine(store, settings=PreviewSettings())
    request = engine.prepare_request(node, {}, context=context)
    artifact = store.put(png_bytes, "image/png", node_id=node["id"], run_id=context.run_id)
    manifest = store.prewarm(request, artifact, metadata={"origin_source": "live_api"})
    final = await engine.execute_node(node, {}, context=replace(context, mode="final"))
    assert final.metadata["source"] == "local_fallback"
    manifest.write_text('{"broken": true}', encoding="utf-8")
    draft = await engine.execute_node(node, {}, context=context)
    assert draft.metadata["source"] == "local_fallback"


def test_cannot_publish_fallback_as_live_prewarm(engine, node, context, png_bytes):
    request = engine.prepare_request(node, {}, context=context)
    artifact = engine.store.put(png_bytes, "image/png", node_id=node["id"], run_id=context.run_id)
    with pytest.raises(PreviewError, match="explicit"):
        engine.store.prewarm(request, artifact, metadata={"source": "local_fallback"})


@pytest.mark.asyncio
async def test_composition_mixes_reused_and_new_inputs(engine, node, context, png_bytes):
    old = engine.store.put(png_bytes, "image/png", node_id="old-node", run_id="old-run")
    generated = await engine.execute_node(node, {}, context=context)
    new = generated.artifacts[0]
    text = engine.store.put(b"A reusable text artifact", "text/plain", node_id="text-node", run_id="text-run")
    composite = copy.deepcopy(node)
    composite.update(id="composite", kind="composition")
    composite["execution"].update(
        tool="layout_compose",
        inputs={},
        outputs=["page"],
        params={"layout": {"direction": "grid", "columns": 2, "cell_width": 240, "cell_height": 160}},
    )
    artifacts = {artifact["id"]: artifact for artifact in (old, new, text)}
    ctx = ExecutionContext(
        run_id="composite-run",
        cache_key="layout-key",
        decision="REBUILD",
        resolved_inputs={
            "artifact_ids": list(artifacts),
            "title": "Portable composition",
            "captions": {old["id"]: "Existing result", new["id"]: "Updated result"},
        },
        node_states={"old-node": "CACHE_HIT", node["id"]: "RECOMPUTE"},
    )
    result = await engine.execute_node(composite, artifacts, context=ctx)
    assert result.cache["decision"] == "REBUILD"
    assert len(result.metadata["regions"]) == 3
    assert result.metadata["regions"][0]["cache_decision"] == "CACHE_HIT"
    assert result.metadata["regions"][1]["cache_decision"] == "RECOMPUTE"
    with Image.open(io.BytesIO(engine.store.read(result.artifacts[0]))) as image:
        for region in result.metadata["regions"]:
            rect = region["rect"]
            assert rect["x"] + rect["width"] <= image.width
            assert rect["y"] + rect["height"] <= image.height
    again = await engine.execute_node(composite, artifacts, context=ctx)
    assert again.cache["decision"] == "CACHE_HIT"
    changed = await engine.execute_node(
        composite, artifacts, context=replace(ctx, resolved_inputs={**ctx.resolved_inputs, "title": "New title"})
    )
    assert changed.cache["decision"] == "REBUILD"
    assert changed.artifacts[0]["sha256"] != result.artifacts[0]["sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,mime,content",
    [
        ("text_materialize", "text/markdown", "# Notes\nReusable text"),
        ("json_materialize", "application/json", {"result": [1, 2], "ok": True}),
    ],
)
async def test_text_json_materialization(engine, node, context, tool, mime, content):
    node["execution"].update(tool=tool, inputs={}, params={"mime_type": mime}, outputs=["content"])
    result = await engine.execute_node(node, {}, context=replace(context, resolved_inputs={"content": content}))
    artifact = result.artifacts[0]
    assert artifact["mime_type"] == mime
    data = engine.store.read(artifact).decode()
    assert (json.loads(data) if mime == "application/json" else data) == content


@pytest.mark.asyncio
async def test_a_schema_compatibility_full_archive(engine, node, context, a_protocol):
    result = await engine.execute_node(node, {}, context=context)
    now = "2026-10-16T00:00:00Z"
    run = {
        "id": context.run_id,
        "trace_id": "trace",
        "node_id": node["id"],
        "revision_id": "revision",
        "session_id": "session",
        "agent_id": "agent",
        "harness_id": "harness",
        "state": "succeeded",
        "started_at": now,
        "ended_at": now,
        "inputs": {},
        "outputs": result.outputs,
        "artifact_ids": [item["id"] for item in result.artifacts],
        "provenance": node["provenance"],
        "cache": result.cache,
        "preview_stage": result.preview_stage,
        "duration_ms": result.duration_ms,
    }
    archive = {
        "aica_version": "0.1",
        "metadata": {
            "project_id": "project",
            "title": "Test",
            "source_mode": "mixed",
            "created_at": now,
            "updated_at": now,
            "external_links": [],
        },
        "variables": {},
        "interactive_questions": [],
        "graph": {
            "revision_id": "revision",
            "nodes": [node],
            "edges": [],
            "branches": [{"id": "main", "parent_id": None, "state": "active"}],
            "golden_path": [node["id"]],
        },
        "artifacts": result.artifacts,
        "runs": [run],
        "harnesses": [
            {"id": "harness", "name": "test", "version": None, "config": {}, "state": {}, "availability": {}}
        ],
        "agents": [
            {
                "id": "agent",
                "session_id": "session",
                "parent_agent_id": None,
                "harness_id": "harness",
                "client": "test",
                "model": {},
            }
        ],
        "sessions": [
            {
                "id": "session",
                "state": "active",
                "created_at": now,
                "consent": {"record": True, "include_children": False, "decided_at": now},
            }
        ],
        "snapshot": {"stream_id": "stream", "snapshot_id": "snapshot", "commit_seq": 0, "complete": True, "gaps": []},
        "extensions": result.extension_patch(context.run_id),
    }
    Draft202012Validator(engine.contract.schema).validate(archive)
    assert a_protocol.validate_archive(archive)["valid"]
    assert "mime" not in result.artifacts[0] and "metadata" not in result.artifacts[0]


@pytest.mark.asyncio
async def test_generated_incremental_archives_pass_a_semantics(tmp_path, a_protocol):
    script = Path(__file__).resolve().parents[1] / "scripts/build_review.py"
    specification = importlib.util.spec_from_file_location("b_review", script)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    await module.build(tmp_path)
    archives = json.loads((tmp_path / "archives.json").read_text(encoding="utf-8"))
    for name in ("original", "fork"):
        result = a_protocol.validate_archive(archives[name])
        assert result["valid"], result["errors"]
    expected = {
        "story": "CACHE_HIT",
        "style": "CACHE_HIT",
        "music": "CACHE_HIT",
        "character": "RECOMPUTE",
        "panel_1": "RECOMPUTE",
        "panel_2": "RECOMPUTE",
        "layout": "REBUILD",
    }
    fork = archives["fork"]
    for stage in ("draft", "final"):
        current = [run for run in fork["runs"] if run["revision_id"] == "review-fork" and run["preview_stage"] == stage]
        assert {run["node_id"]: run["cache"]["decision"] for run in current if run["state"] == "succeeded"} == expected
        assert next(run for run in current if run["node_id"] == "rejected_direction")["state"] == "failed"
        assert fork["extensions"]["aica.preview"]["plans"][stage]["statistics"]["total"] == 7
    assert [item["mode"] for item in fork["variable_operations"]] == ["keep", "override", "append"]
    assert fork["variables"]["style"]["value"] == archives["original"]["variables"]["style"]["value"]
    assert fork["variables"]["character"]["default"] == "urban explorer"
    assert fork["variables"]["character"]["value"] == "orbital botanist, carrying glowing seed pods"
    assert {node["provenance"]["mode"] for node in fork["graph"]["nodes"]} == {"recorded", "extracted", "inferred"}
    assert all(node["provenance"]["evidence"] for node in fork["graph"]["nodes"])
    assert len(fork["metadata"]["external_links"]) == 4
    assert len(fork["graph"]["golden_path"]) == 7
    assert next(branch for branch in fork["graph"]["branches"] if branch["state"] == "dead_end")["reason"]
    store = ArtifactStore(tmp_path / "artifacts", uri_prefix="artifacts")
    for artifact in fork["artifacts"]:
        assert hashlib.sha256(store.read(artifact)).hexdigest() == artifact["sha256"]
        assert hashlib.sha256((tmp_path / artifact["uri"]).read_bytes()).hexdigest() == artifact["sha256"]
    assert json.loads((tmp_path / "original.aica.json").read_text()) == archives["original"]
    assert json.loads((tmp_path / "fork.aica.json").read_text()) == fork
    for node_id in ("character", "panel_1", "panel_2"):
        records = [run for run in fork["runs"] if run["revision_id"] == "review-fork" and run["node_id"] == node_id]
        seeds = [fork["extensions"]["aica.preview"]["runs"][run["id"]]["seed"] for run in records]
        assert len(set(seeds)) == 1


@pytest.mark.asyncio
async def test_cached_node_never_executes_adapter(engine, node, context, monkeypatch):
    async def forbidden(*_args):
        pytest.fail("A Runtime CACHE_HIT must consume no generation resources")

    monkeypatch.setattr(engine.adapters["image_gen"], "execute", forbidden)
    with pytest.raises(PreviewError) as error:
        await engine.execute_node(node, {}, context=replace(context, decision="CACHE_HIT"))
    assert error.value.code == "runtime_cache_hit"


@pytest.mark.parametrize(
    "change,code",
    [("unknown_tool", "unsupported_tool"), ("unresolved", "unresolved_input"), ("extra_field", "invalid_protocol")],
)
def test_rejects_invalid_contracts(engine, node, context, change, code):
    if change == "unknown_tool":
        node["execution"]["tool"] = "execute_arbitrary_code"
    elif change == "unresolved":
        node["execution"]["inputs"]["prompt"] = {"kind": "variable", "variable_id": "subject"}
    else:
        node["preview"] = {"mode": "draft"}
    with pytest.raises(PreviewError) as error:
        engine.prepare_request(node, {}, context=context)
    assert error.value.code == code


def test_store_rejects_traversal_and_checks_alternative_location(tmp_path, png_bytes):
    store = ArtifactStore(tmp_path / "store")
    with pytest.raises(PreviewError):
        store.local_path("../outside.png")
    artifact = store.put(png_bytes, "image/png", node_id="node", run_id="run")
    alternative = {**artifact, "uri": "not-found.png", "locations": [artifact["uri"]]}
    assert store.read(alternative) == png_bytes
    store.local_path(artifact["uri"]).write_bytes(b"wrong")
    with pytest.raises(PreviewError):
        store.read(artifact)


@pytest.mark.asyncio
async def test_cancellation_is_not_fallback(store, node, context):
    async def cancelled(_request):
        raise asyncio.CancelledError()

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(api_base="https://test.invalid/v1", api_key="key", draft_model="draft"),
        transport=httpx.MockTransport(cancelled),
    )
    with pytest.raises(asyncio.CancelledError):
        await engine.execute_node(node, {}, context=context)


@pytest.mark.asyncio
async def test_custom_adapter_registration(engine, node, context):
    from preview import BaseAdapter

    class CustomAdapter(BaseAdapter):
        async def execute(self, request, store):
            return AdapterOutput(b"custom output", "text/plain", "custom_adapter")

    node["execution"]["tool"] = "custom_tool"
    engine.register_adapter("custom_tool", CustomAdapter())
    result = await engine.execute_node(node, {}, context=context)
    assert engine.store.read(result.artifacts[0]) == b"custom output"
