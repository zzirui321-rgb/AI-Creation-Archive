import base64
import copy
import hashlib
import json
from dataclasses import replace
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from PIL import Image, ImageChops, ImageDraw
from preview import PreviewEngine, PreviewError, PreviewSettings
from preview.scene_layers import decode, png


def source_images(store):
    base = Image.new("RGBA", (96, 64), "#426877")
    draw = ImageDraw.Draw(base)
    draw.line((0, 0, 95, 63), fill="#9fcbb0", width=4)
    base.putpixel((0, 0), (20, 30, 40, 0))
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).ellipse((26, 10, 70, 58), fill=255)
    artifacts = [store.put(png(image), "image/png", node_id=None, run_id=None) for image in (base, mask)]
    return base, mask, artifacts


def protected_pixels(before, after, mask):
    protected = mask.point(lambda value: 255 if value == 0 else 0)
    for channel in ImageChops.difference(before, after).split():
        assert ImageChops.multiply(channel, protected).getbbox() is None


@pytest.mark.asyncio
async def test_single_node_edit_pins_resolution_seed_and_isolates_stages(store, node, context):
    base, mask, items = source_images(store)
    original_node = copy.deepcopy(node)
    settings = PreviewSettings(provider="local_fallback")
    engine = PreviewEngine(store, settings=settings)
    results = []
    for stage in ("draft", "final"):
        result = await engine.execute_image_edit(
            node,
            {a["id"]: a for a in items},
            context=replace(context, mode=stage, resolved_inputs={"prompt": "a mechanical owl"}),
            original_artifact_id=items[0]["id"],
            mask_artifact_id=items[1]["id"],
        )
        result_image = decode(store.read(result.artifacts[0]))
        assert result_image.size == base.size
        protected_pixels(base, result_image, mask)
        assert result_image.tobytes() != base.tobytes()
        assert result.metadata["seed"] == context.seed
        assert result.metadata["resolution_policy"] == "original_image"
        assert result.metadata["source"] == "local_fallback"
        engine.contract.validate("artifact", result.artifacts[0])
        results.append(result)
    assert [r.metadata["stage_key"] for r in results] == ["base-key:draft", "base-key:hq"]
    assert [r.metadata["effective_params"]["steps"] for r in results] == [4, 28]
    assert results[0].metadata["fingerprint"] != results[1].metadata["fingerprint"]
    assert node == original_node


@pytest.mark.asyncio
async def test_edit_region_append_then_background_edit_preserves_protected_rgba(engine, node, context):
    base, _, items = source_images(engine.store)
    artifacts = {items[0]["id"]: items[0]}
    first = await engine.execute_image_edit(
        node, artifacts, context=context, original_artifact_id=items[0]["id"], region=[0.65, 0.1, 0.25, 0.3]
    )
    appended = decode(engine.store.read(first.artifacts[0]))
    region = Image.new("L", base.size)
    ImageDraw.Draw(region).rectangle((62, 6, 85, 25), fill=255)
    protected_pixels(base, appended, region)
    artifacts[first.artifacts[0]["id"]] = first.artifacts[0]
    background = Image.new("L", base.size, 255)
    ImageDraw.Draw(background).rectangle((22, 5, 90, 60), fill=0)
    mask = engine.store.put(png(background), "image/png", node_id=None, run_id=None)
    artifacts[mask["id"]] = mask
    second = await engine.execute_image_edit(
        node,
        artifacts,
        context=replace(context, resolved_inputs={"prompt": "snowy mountains"}),
        original_artifact_id=first.artifacts[0]["id"],
        mask_artifact_id=mask["id"],
    )
    protected_pixels(appended, decode(engine.store.read(second.artifacts[0])), background)


@pytest.mark.asyncio
async def test_mask_edit_sdcpp_uses_original_reference_then_clamps_output(store, node, context):
    base, mask, items = source_images(store)
    calls = []
    candidate = png(Image.new("RGBA", base.size, "#ec8950"))

    def respond(request):
        assert request.url.path == "/v1/img2img"
        body = json.loads(request.content)
        assert decode(base64.b64decode(body["ref_image"])).tobytes() == base.tobytes()
        assert (body["width"], body["height"]) == base.size
        assert "edit_region" not in body and "edit_mode" not in body
        calls.append(body)
        return httpx.Response(200, json={"image": base64.b64encode(candidate).decode()})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            provider="live_api",
            api_base="https://local.invalid/v1/images/generations",
            api_key="test",
            api_style="sdcpp",
            draft_model="local-test",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    for _ in range(2):
        result = await engine.execute_image_edit(
            node,
            {a["id"]: a for a in items},
            context=context,
            original_artifact_id=items[0]["id"],
            mask_artifact_id=items[1]["id"],
        )
        protected_pixels(base, decode(store.read(result.artifacts[0])), mask)
        assert result.metadata["edit_transport"] == "reference_then_masked_blend"
    assert len(calls) == 1
    assert result.cache["decision"] == "CACHE_HIT"


@pytest.mark.asyncio
async def test_openai_edit_sends_multipart_and_inverts_mask(store, node, context):
    base, mask, items = source_images(store)

    def respond(request):
        assert request.url.path == "/v1/images/edits"
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + request.content
        )
        parts = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        assert decode(parts["image"]).tobytes() == base.tobytes()
        assert decode(parts["mask"]).getchannel("A").tobytes() == mask.point(lambda value: 255 - value).tobytes()
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png(base)).decode()}]})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://edit.invalid/v1",
            api_style="openai",
            api_key="test",
            draft_model="edit-model",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    result = await engine.execute_image_edit(
        node,
        {a["id"]: a for a in items},
        context=context,
        original_artifact_id=items[0]["id"],
        mask_artifact_id=items[1]["id"],
    )
    assert result.metadata["edit_transport"] == "native_mask"


@pytest.mark.asyncio
async def test_explicit_new_content_candidate_still_protects_original_pixels(store, node, context):
    base, mask, items = source_images(store)
    node["execution"]["params"]["edit_reference"] = False

    def respond(request):
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert "ref_image" not in body and "edit_reference" not in body
        assert body["size"] == "96x64"
        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png(Image.new("RGB", base.size, "white"))).decode()}]}
        )

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://local.invalid/v1",
            api_style="sdcpp",
            api_key="key",
            draft_model="model",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    result = await engine.execute_image_edit(
        node,
        {a["id"]: a for a in items},
        context=context,
        original_artifact_id=items[0]["id"],
        mask_artifact_id=items[1]["id"],
    )
    image = decode(store.read(result.artifacts[0]))
    protected_pixels(base, image, mask)
    assert image.getpixel((45, 30)) == (255, 255, 255, 255)
    assert result.metadata["edit_transport"] == "generation_then_masked_blend"


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_single_node_append_overlay_adds_only_extracted_object(store, node, context, live):
    base, _, items = source_images(store)
    candidate = Image.new("RGB", base.size, "white")
    ImageDraw.Draw(candidate).ellipse((28, 5, 68, 55), fill="#c43b51")

    def respond(request):
        body = json.loads(request.content)
        assert request.url.path.endswith("/generations") and "ref_image" not in body
        assert "pure white" in body["prompt"]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png(candidate)).decode()}]})

    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            provider="live_api" if live else "local_fallback",
            api_base="https://local.invalid/v1",
            api_style="sdcpp",
            api_key="key",
            draft_model="model",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(respond),
    )
    node["execution"]["params"]["edit_operation"] = "overlay"
    result = await engine.execute_image_edit(
        node,
        {items[0]["id"]: items[0]},
        context=replace(context, resolved_inputs={"prompt": "a red balloon"}),
        original_artifact_id=items[0]["id"],
        region=[0.7, 0, 0.3, 0.45],
    )
    picture = decode(store.read(result.artifacts[0]))
    region = Image.new("L", base.size)
    ImageDraw.Draw(region).rectangle((67, 0, 95, 28), fill=255)
    protected_pixels(base, picture, region)
    assert picture.tobytes() != base.tobytes()
    assert result.metadata["edit_operation"] == "overlay"
    assert result.metadata["effective_params"]["edit_reference"] is False
    assert result.metadata["protected_pixel_count"] > region.histogram()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "empty", "wrong_size", "ambiguous", "invalid_region"])
async def test_invalid_edits_fail_before_any_model_call(store, node, context, failure):
    _, _, items = source_images(store)
    calls = []
    engine = PreviewEngine(store, transport=httpx.MockTransport(lambda req: calls.append(req)))
    node["execution"]["params"]["edit_mode"] = "masked_replace"
    inputs = {"original_image": items[0]["id"], "edit_mask": items[1]["id"]}
    if failure == "missing":
        del inputs["edit_mask"]
    if failure in {"empty", "wrong_size"}:
        image = Image.new("L", (32, 32) if failure == "wrong_size" else (96, 64))
        items[1] = store.put(png(image), "image/png", node_id=None, run_id=None)
        inputs["edit_mask"] = items[1]["id"]
    if failure == "ambiguous":
        node["execution"]["params"]["edit_region"] = [0, 0, 1, 1]
    if failure == "invalid_region":
        del inputs["edit_mask"]
        node["execution"]["params"]["edit_region"] = [-1, 0, 1, 1]
    with pytest.raises(PreviewError):
        await engine.execute_node(node, {a["id"]: a for a in items}, context=replace(context, resolved_inputs=inputs))
    assert not calls


@pytest.mark.asyncio
async def test_wrong_provider_dimensions_cannot_return_silently_resized_edit(store, node, context):
    _, _, items = source_images(store)
    engine = PreviewEngine(
        store,
        settings=PreviewSettings(
            api_base="https://local.invalid/v1",
            api_style="sdcpp",
            api_key="key",
            draft_model="model",
            allow_fallback=False,
        ),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"image": base64.b64encode(png(Image.new("RGB", (48, 32)))).decode()})
        ),
    )
    with pytest.raises(PreviewError):
        await engine.execute_image_edit(
            node,
            {a["id"]: a for a in items},
            context=context,
            original_artifact_id=items[0]["id"],
            mask_artifact_id=items[1]["id"],
        )


@pytest.mark.asyncio
async def test_transparent_white_mask_is_protected_and_soft_mask_blends(engine, node, context):
    base, _, items = source_images(engine.store)
    mask = Image.new("RGBA", base.size, (255, 255, 255, 0))
    ImageDraw.Draw(mask).rectangle((20, 15, 60, 50), fill=(255, 255, 255, 128))
    artifact = engine.store.put(png(mask), "image/png", node_id=None, run_id=None)
    result = await engine.execute_image_edit(
        node,
        {a["id"]: a for a in [items[0], artifact]},
        context=context,
        original_artifact_id=items[0]["id"],
        mask_artifact_id=artifact["id"],
    )
    protected_pixels(base, decode(engine.store.read(result.artifacts[0])), mask.getchannel("A"))
    assert result.metadata["editable_pixel_count"] == 41 * 36


def test_one_image_node_real_runtime_fork_plan_replay_without_layer_nodes(tmp_path, store):
    from uuid import uuid4

    from aica_schema.protocol import empty_archive, require_archive
    from fastapi.testclient import TestClient
    from preview.integration_server import create_app
    from preview.scripts.generic_cases import finish, node, variable
    from preview.transfer_bundle import export_transfer_bundle
    from test_c_integration import TOKEN, wait_job

    base, mask, items = source_images(store)
    archive = empty_archive("Single image node with explicit local edit / synthetic acceptance")
    archive["artifacts"] = items
    archive["graph"]["nodes"] = [
        node(
            "complete_image",
            "image_gen",
            {
                "prompt": variable("requested_change"),
                **{
                    key: {"kind": "artifact", "artifact_id": item["id"]}
                    for key, item in zip(("original_image", "edit_mask"), items)
                },
            },
            params={"seed": 73, "edit_mode": "masked_replace"},
        )
    ]
    finish(archive, {"requested_change": "a watercolor bird"})
    bundle = export_transfer_bundle(archive, store)
    sha = hashlib.sha256(bundle).hexdigest()
    with TestClient(
        create_app(tmp_path / "platform", {"tester": TOKEN}, PreviewSettings(provider="local_fallback")),
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        project = client.post("/api/projects", json={"title": "Single-node editing"}).json()["project"]["id"]
        prefix = "/api/projects/" + project
        response = client.post(
            prefix + "/imports",
            content=bundle,
            headers={
                "Content-Type": "application/vnd.aica.bundle+zip",
                "Idempotency-Key": "sha256:" + sha,
                "X-AiCA-Bundle-SHA256": sha,
            },
        )
        assert response.status_code == 201, response.text
        before = wait_job(client, prefix, "draft")
        fork = client.post(
            prefix + "/forks",
            json={
                "request_id": uuid4().hex,
                "archive_id": before["archive_id"],
                "expected_revision": before["archive"]["graph"]["revision_id"],
                "operations": [{"variable_id": "requested_change", "mode": "override", "value": "mechanical robot"}],
            },
        )
        assert fork.status_code == 201, fork.text
        assert client.get(prefix + "/plan?stage=draft").json()["decisions"] == {"complete_image": "RECOMPUTE"}
        for stage in ("draft", "final"):
            after = wait_job(client, prefix, stage)
            require_archive(after["archive"])
            assert len(after["archive"]["graph"]["nodes"]) == 1
            run = next(item for item in reversed(after["archive"]["runs"]) if item["preview_stage"] == stage)
            identifier = run["outputs"]["out"]
            data = client.get(prefix + "/artifacts/" + identifier).content
            image = decode(data)
            assert image.size == base.size
            protected_pixels(base, image, mask)
            assert image.tobytes() != base.tobytes()
            assert client.get(prefix + f"/plan?stage={stage}").json()["decisions"] == {"complete_image": "CACHE_HIT"}
