import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest
from preview import PreviewEngine, PreviewSettings


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["openai", "siliconflow", "fal"])
async def test_live_provider_protocols(store, node, context, png_bytes, style):
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["seed"] == context.seed
            if style == "fal":
                assert body["image_size"] == {"width": 768, "height": 512}
                assert body["num_inference_steps"] == 4
                return httpx.Response(
                    200,
                    json={
                        "status_url": "https://test.invalid/status/1",
                        "response_url": "https://test.invalid/result/1",
                    },
                )
            if style == "siliconflow":
                assert body["image_size"] == "768x512"
                assert body["num_inference_steps"] == 4
                return httpx.Response(200, json={"images": [{"url": "https://cdn.invalid/image.png"}]})
            assert body["size"] == "768x512"
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png_bytes).decode()}]})
        if request.url.path.startswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path.startswith("/result"):
            return httpx.Response(200, json={"images": [{"url": "https://cdn.invalid/image.png"}]})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=png_bytes)

    settings = PreviewSettings(
        api_base="https://test.invalid/v1",
        api_key="key",
        api_style=style,
        draft_model="black-forest-labs/FLUX.1-schnell",
    )
    engine = PreviewEngine(store, settings=settings, transport=httpx.MockTransport(respond))
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "live_api"
    assert store.read(result.artifacts[0]) == png_bytes
    assert calls[0].headers["authorization"].startswith("Key " if style == "fal" else "Bearer ")


@pytest.mark.asyncio
async def test_fal_queue_polling_shares_whole_deadline(store, node, context):
    async def respond(request):
        if request.method == "POST":
            return httpx.Response(
                200, json={"status_url": "https://queue.fal.run/status", "response_url": "https://queue.fal.run/result"}
            )
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"status": "IN_QUEUE"})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://queue.fal.run/model",
            api_key="key",
            api_style="fal",
            draft_model="model",
            timeout_seconds=0.03,
        ),
        transport=httpx.MockTransport(respond),
    )
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "local_fallback"
    assert result.metadata["fallback_reasons"] == ["timeout"]


@pytest.mark.asyncio
async def test_reference_images_sent_to_fal(store, node, context, png_bytes):
    reference = store.put(png_bytes, "image/png", node_id="reference", run_id="reference-run")

    def respond(request):
        body = json.loads(request.content)
        assert body["image_urls"][0].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"images": [{"b64_json": base64.b64encode(png_bytes).decode()}]})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(api_base="https://fal.run/model", api_key="key", api_style="fal", draft_model="model"),
        transport=httpx.MockTransport(respond),
    )
    result = await engine.execute_node(
        node,
        {reference["id"]: reference},
        context=replace(
            context, resolved_inputs={"prompt": "Updated subject", "reference_artifact_ids": [reference["id"]]}
        ),
    )
    assert result.metadata["source"] == "live_api"
    assert result.metadata["reference_artifact_ids"] == [reference["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, [], {"data": []}, {"data": [None]}, {"images": [{"b64_json": "invalid!"}]}])
async def test_malformed_live_responses_fall_back(store, node, context, body):
    engine = PreviewEngine(
        store,
        settings=PreviewSettings(api_base="https://test.invalid/v1", api_key="key", draft_model="model"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "local_fallback"
    assert result.metadata["fallback_reasons"] == ["provider_error"]


@pytest.mark.asyncio
async def test_prewarms_take_priority_over_live(store, node, context, png_bytes):
    def forbidden(_):
        pytest.fail("A verified prewarm must skip the live API")

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(api_base="https://test.invalid/v1", api_key="key", draft_model="model"),
        transport=httpx.MockTransport(forbidden),
    )
    request = engine.prepare_request(node, {}, context=context)
    artifact = store.put(png_bytes, "image/png", node_id=node["id"], run_id=context.run_id)
    manifest = store.prewarm(request, artifact, metadata={"origin_source": "live_api"})
    result = await engine.execute_node(node, {}, context=context)
    assert result.metadata["source"] == "prewarmed_cache"
    entry = json.loads(manifest.read_text())
    entry["metadata"] = []
    manifest.write_text(json.dumps(entry), encoding="utf-8")
    assert store.load_cache(request, "prewarmed_cache") is None


@pytest.mark.parametrize("alias", [False, True])
def test_environment_settings_and_dotenv(tmp_path, alias):
    prefix = "IMAGE_API_URL" if alias else "AICA_IMAGE_API_BASE"
    env_file = tmp_path / ".env"
    env_file.write_text(f"{prefix}=https://example.invalid/v1\nIMAGE_API_KEY=private\nPREVIEW_PROVIDER=mock_svg\n")
    settings = PreviewSettings.from_env(env_file, environ={"PREVIEW_PROVIDER": "live_api"})
    assert settings.api_base == "https://example.invalid/v1"
    assert settings.api_key == "private"
    assert settings.provider == "live_api"
    assert "private" not in repr(settings)
