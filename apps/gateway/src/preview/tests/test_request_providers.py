import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from preview import PreviewEngine, PreviewSettings
from preview.contracts import AdapterOutput, PreviewError
from preview.integration_server import create_app
from preview.providers.live_api import LiveAPIProvider
from preview.request_config import ImageConfig
from preview.scripts.generic_cases import cases
from test_c_integration import TOKEN, upload


def config(model="test-model", **updates):
    return {
        "provider": "openai",
        "api_base": "https://provider.invalid/v1",
        "api_key": "ephemeral-test-key-unique",
        "model": model,
        "allow_fallback": False,
        **updates,
    }


def replay(client, url, provider):
    current = client.get(url + "/archive").json()
    response = client.post(
        url + "/executions",
        json={
            "request_id": __import__("uuid").uuid4().hex,
            "archive_id": current["archive_id"],
            "expected_revision": current["archive"]["graph"]["revision_id"],
            "record": True,
            "execute": True,
            "stage": "draft",
            "image_config": provider,
        },
    )
    assert response.status_code == 202, response.text

    async def wait():
        for _ in range(2000):
            job = client.get(url + "/executions/" + response.json()["job_id"]).json()["execution"]
            if job["state"] not in {"queued", "running"}:
                return job
            await asyncio.sleep(0.01)
        raise TimeoutError("Runtime worker did not complete")

    job = asyncio.run(wait())
    archive = client.get(url + "/archive").json()
    return job, archive


def test_selected_settings_reach_runtime_and_secrets_are_not_persisted(tmp_path, monkeypatch, png_bytes):
    calls = []

    async def generate(self, request, store):
        calls.append((self.settings.api_base, self.settings.api_key, request.profile.model))
        return AdapterOutput(png_bytes, "image/png", "live_api", {"is_substitute": False, "layer_prepared": True})

    monkeypatch.setattr(LiveAPIProvider, "generate", generate)
    app = create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        url = upload(client, cases()["field-guide"])
        provider = config()
        job, result = replay(client, url, provider)
        assert job["state"] == "succeeded"
        assert calls == [(provider["api_base"], provider["api_key"], provider["model"])] * 2
        payload = {"archive_id": result["archive_id"], "stage": "draft", "image_config": provider}
        plan = client.post(url + "/plan", json=payload).json()
        assert set(plan["decisions"].values()) == {"CACHE_HIT"}
        replay(client, url, provider)
        assert len(calls) == 2
        replay(client, url, config(model="different-model"))
        assert len(calls) == 4 and calls[-1][2] == "different-model"
        assert provider["api_key"] not in json.dumps(client.get(url + "/archive").json())
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert provider["api_key"].encode() not in path.read_bytes(), path


def test_fallback_runs_do_not_populate_real_provider_runtime_cache(tmp_path, monkeypatch, png_bytes):
    fail, calls = True, []

    async def generate(self, request, store):
        calls.append(request.node["id"])
        if fail:
            raise PreviewError("timeout", "Injected model timeout")
        return AdapterOutput(png_bytes, "image/png", "live_api", {"is_substitute": False, "layer_prepared": True})

    monkeypatch.setattr(LiveAPIProvider, "generate", generate)
    app = create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        url = upload(client, cases()["field-guide"])
        provider = config(allow_fallback=True)
        job, _ = replay(client, url, provider)
        assert job["state"] == "succeeded" and len(calls) == 2
        fail = False
        job, result = replay(client, url, provider)
        assert job["state"] == "succeeded" and len(calls) == 4
        node_runs = {run["node_id"]: run for run in result["archive"]["runs"]}
        assert node_runs["biome_notes"]["cache"]["decision"] == "CACHE_HIT"
        assert node_runs["wildlife_plate"]["cache"]["decision"] == "RECOMPUTE"
        replay(client, url, provider)
        assert len(calls) == 4


@pytest.mark.asyncio
async def test_strict_api_mode_never_returns_a_fallback(store, node, context):
    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://test.invalid/v1", api_key="key", draft_model="model", allow_fallback=False
        ),
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    )
    with pytest.raises(PreviewError, match="No image provider"):
        await engine.execute_node(node, {}, context=context)


@pytest.mark.asyncio
async def test_sdcpp_payload_has_steps_style_and_reference_image(store, node, context, png_bytes):
    reference = store.put(png_bytes, "image/png", node_id="ref", run_id="ref-run")
    seen = []

    def respond(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == "/v1/img2img"
        assert body["steps"] == 4 and body["width"] == 768 and body["height"] == 512
        assert "watercolor" in body["prompt"] and "owl" in body["prompt"]
        assert body["ref_image"]
        import base64

        return httpx.Response(200, json={"image": base64.b64encode(png_bytes).decode()})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://local.invalid/v1/images/generations",
            api_style="sdcpp",
            api_key="key",
            draft_model="model",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    result = await engine.execute_node(
        node,
        {reference["id"]: reference},
        context=replace(
            context,
            resolved_inputs={"prompt": "owl", "style": "watercolor", "reference_artifact_ids": [reference["id"]]},
        ),
    )
    assert result.metadata["source"] == "live_api" and len(seen) == 1


@pytest.mark.asyncio
async def test_qwen_native_protocol(store, node, context, png_bytes):
    def respond(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert request.url.path.endswith("/services/aigc/multimodal-generation/generation")
            assert body["parameters"]["size"] == "768*512"
            assert body["input"]["messages"][0]["content"][0]["text"]
            return httpx.Response(
                200,
                json={"output": {"choices": [{"message": {"content": [{"image": "https://cdn.invalid/image.png"}]}}]}},
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, content=png_bytes)

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://qwen.invalid/api/v1",
            api_style="qwen",
            api_key="key",
            draft_model="qwen-image",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    assert (await engine.execute_node(node, {}, context=context)).metadata["source"] == "live_api"


@pytest.mark.asyncio
async def test_qwen_reference_image_uses_multimodal_content(store, node, context, png_bytes):
    reference = store.put(png_bytes, "image/png", node_id="ref", run_id="ref-run")

    def respond(request):
        body = json.loads(request.content)
        content = body["input"]["messages"][0]["content"]
        assert content[0]["image"].startswith("data:image/png;base64,")
        assert content[-1]["text"]
        return httpx.Response(
            200, json={"output": {"choices": [{"message": {"content": [{"image": "https://cdn.invalid/image.png"}]}}]}}
        )

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://qwen.invalid/api/v1",
            api_style="qwen",
            api_key="key",
            draft_model="qwen-image-2.0",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=png_bytes) if request.method == "GET" else respond(request)
        ),
    )
    result = await engine.execute_node(
        node,
        {reference["id"]: reference},
        context=replace(
            context, resolved_inputs={"prompt": "edit the reference", "reference_artifact_ids": [reference["id"]]}
        ),
    )
    assert result.metadata["source"] == "live_api"


def test_config_validation_and_defaults():
    settings = ImageConfig(**config()).settings()
    assert settings.timeout_seconds == 10
    assert "ephemeral-test-key" not in repr(ImageConfig(**config()))
    with pytest.raises(ValueError):
        ImageConfig(**config(api_base="https://user:key@example.invalid/v1"))
    with pytest.raises(ValueError):
        ImageConfig(**config(timeout_seconds=301))
    with pytest.raises(ValueError):
        ImageConfig(**config(api_key="")).settings()
