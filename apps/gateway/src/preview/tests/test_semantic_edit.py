import base64
import copy
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageDraw
from preview import EditIntent, HTTPSegmenter, PreviewError, SegmentationSettings
from preview.edit_review_api import create_review_app
from preview.scene_layers import decode, png


class Segmenter:
    def __init__(self, masks, *, score=0.8, corrupt=None):
        self.masks, self.score, self.corrupt, self.calls = masks, score, corrupt, 0

    async def segment(self, data, targets):
        self.calls += 1
        body = {
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "model": {
                "detector": "fixture-detector",
                "detector_revision": "test-1",
                "segmenter": "fixture-segmenter",
                "segmenter_revision": "test-2",
            },
            "candidates": [
                {
                    "label": target,
                    "target": target,
                    "score": self.score,
                    "mask_base64": base64.b64encode(png(mask)).decode(),
                }
                for target, mask in self.masks
            ],
        }
        if self.corrupt:
            self.corrupt(body)
        return body


def images(engine):
    base = Image.new("RGBA", (192, 128), "#577961")
    base.putpixel((0, 0), (14, 18, 24, 0))
    first, second = [Image.new("L", base.size) for _ in range(2)]
    ImageDraw.Draw(first).ellipse((30, 20, 80, 110), fill=255)
    ImageDraw.Draw(second).ellipse((120, 20, 165, 110), fill=255)
    original = engine.store.put(png(base), "image/png", node_id=None, run_id=None)
    return base, first, second, original


def unchanged(before, after, mask):
    protected = mask.point(lambda value: 255 if value == 0 else 0)
    for channel in ImageChops.difference(before, after).split():
        assert ImageChops.multiply(channel, protected).getbbox() is None


@pytest.mark.asyncio
async def test_one_semantic_target_reuses_mask_across_stages(engine, node, context):
    base, mask, _, original = images(engine)
    segmenter = Segmenter([("object", mask)])
    engine.segmenter = segmenter
    proposal = await engine.propose_image_edit(
        original, EditIntent(operation="attribute", instruction="a crimson surface", targets=["object"])
    )
    assert proposal["status"] == "ready"
    candidate = proposal["candidates"][0]
    assert candidate["mask_artifact"]["sha256"] == hashlib.sha256(png(mask)).hexdigest()
    for stage in ("draft", "final"):
        result = await engine.execute_proposed_edit(
            node,
            {original["id"]: original},
            context=replace(context, mode=stage),
            original_artifact_id=original["id"],
            proposal_id=proposal["id"],
        )
        unchanged(base, decode(engine.store.read(result.artifacts[0])), mask)
        assert result.metadata["edit_proposal"]["provenance"]["source"] == "semantic_segmentation"
        assert result.metadata["edit_proposal"]["selection"] == "single_target_policy"
        assert result.metadata["edit_proposal"]["mask_artifact"] == candidate["mask_artifact"]
    assert segmenter.calls == 1


@pytest.mark.asyncio
async def test_multiple_targets_require_selection_and_preserve_other_object(engine, node, context):
    base, mask, other, original = images(engine)
    engine.segmenter = Segmenter([("object", mask), ("object", other)])
    proposal = await engine.propose_image_edit(
        original, {"operation": "attribute", "instruction": "golden surface", "targets": ["object"]}
    )
    assert proposal["status"] == "needs_confirmation"
    kwargs = dict(context=context, original_artifact_id=original["id"], proposal_id=proposal["id"])
    with pytest.raises(PreviewError) as error:
        await engine.execute_proposed_edit(node, {original["id"]: original}, **kwargs)
    assert error.value.code == "target_confirmation_required"
    with pytest.raises(PreviewError) as error:
        await engine.execute_proposed_edit(node, {original["id"]: original}, candidate_id="foreign-target", **kwargs)
    assert error.value.code == "unknown_edit_target"
    result = await engine.execute_proposed_edit(
        node, {original["id"]: original}, candidate_id=proposal["candidates"][0]["id"], **kwargs
    )
    unchanged(base, decode(engine.store.read(result.artifacts[0])), mask)


@pytest.mark.asyncio
async def test_background_protects_every_requested_object_and_missing_target_blocks(engine, node, context):
    base, first, second, original = images(engine)
    intent = {"operation": "background", "instruction": "snowy landscape", "targets": ["object", "prop"]}
    engine.segmenter = Segmenter([("object", first)])
    missing = await engine.propose_image_edit(original, intent)
    assert missing["status"] == "not_found" and "prop" in missing["reason"]
    engine.segmenter = Segmenter([("object", first), ("prop", second)])
    proposal = await engine.propose_image_edit(original, intent)
    assert proposal["status"] == "ready"
    mask = ImageChops.lighter(first, second).point(lambda value: 255 - value)
    assert (
        decode(engine.store.read(proposal["candidates"][0]["mask_artifact"])).convert("L").tobytes() == mask.tobytes()
    )
    result = await engine.execute_proposed_edit(
        node,
        {original["id"]: original},
        context=context,
        original_artifact_id=original["id"],
        proposal_id=proposal["id"],
    )
    unchanged(base, decode(engine.store.read(result.artifacts[0])), mask)


@pytest.mark.asyncio
async def test_append_placement_is_labelled_and_requires_confirmation(engine, node, context):
    base, first, _, original = images(engine)
    engine.segmenter = Segmenter([("object", first)])
    proposal = await engine.propose_image_edit(
        original, {"operation": "append", "instruction": "a red balloon", "targets": ["object"]}
    )
    assert proposal["status"] == "needs_confirmation"
    candidate = proposal["candidates"][0]
    assert candidate["score"] is None and candidate["provenance"]["source"] == "placement_proposal"
    allowed = decode(engine.store.read(candidate["mask_artifact"])).convert("L")
    assert ImageChops.multiply(allowed, first).getbbox() is None
    result = await engine.execute_proposed_edit(
        node,
        {original["id"]: original},
        context=context,
        original_artifact_id=original["id"],
        proposal_id=proposal["id"],
        candidate_id=candidate["id"],
    )
    unchanged(base, decode(engine.store.read(result.artifacts[0])), allowed)
    assert result.metadata["edit_operation"] == "overlay"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["source_changed", "mask_corrupt", "proposal_changed"])
async def test_stale_or_corrupt_proposal_cannot_execute(engine, node, context, kind):
    _, first, _, original = images(engine)
    engine.segmenter = Segmenter([("object", first)])
    proposal = await engine.propose_image_edit(
        original, {"operation": "attribute", "instruction": "blue", "targets": ["object"]}
    )
    if kind == "source_changed":
        original = engine.store.put(png(Image.new("RGB", (192, 128))), "image/png", node_id=None, run_id=None)
    if kind == "mask_corrupt":
        engine.store.local_path(proposal["candidates"][0]["mask_artifact"]["uri"]).write_bytes(b"corrupt")
    if kind == "proposal_changed":
        path = engine.store.root / "edit-proposals" / (proposal["id"] + ".json")
        proposal["intent"]["instruction"] = "unauthorized change"
        path.write_text(json.dumps(proposal))
    with pytest.raises(PreviewError):
        await engine.execute_proposed_edit(
            node,
            {original["id"]: original},
            context=context,
            original_artifact_id=original["id"],
            proposal_id=proposal["id"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["hash", "dimensions", "score", "revision", "base64", "target"])
async def test_invalid_segmentation_response_fails_closed(engine, fault):
    _, first, _, original = images(engine)

    def corrupt(body):
        if fault == "hash":
            body["source_sha256"] = "0" * 64
        if fault == "dimensions":
            body["candidates"][0]["mask_base64"] = base64.b64encode(png(Image.new("L", (4, 4)))).decode()
        if fault == "score":
            body["candidates"][0]["score"] = float("nan")
        if fault == "revision":
            del body["model"]["segmenter_revision"]
        if fault == "base64":
            body["candidates"][0]["mask_base64"] = "not-base64"
        if fault == "target":
            body["candidates"][0]["target"] = "another object"

    engine.segmenter = Segmenter([("object", first)], corrupt=corrupt)
    proposal = await engine.propose_image_edit(
        original, {"operation": "attribute", "instruction": "blue", "targets": ["object"]}
    )
    assert proposal["status"] == "unavailable" and not proposal["candidates"]


@pytest.mark.asyncio
async def test_low_scores_are_not_automatic_and_unconfigured_service_has_no_fake_masks(engine):
    _, first, _, original = images(engine)
    intent = {"operation": "attribute", "instruction": "blue", "targets": ["object"]}
    engine.segmenter = Segmenter([("object", first)], score=0.31)
    assert (await engine.propose_image_edit(original, intent))["status"] == "needs_confirmation"
    engine.segmenter = HTTPSegmenter(SegmentationSettings())
    unavailable = await engine.propose_image_edit(original, intent)
    assert unavailable["status"] == "unavailable" and unavailable["reason"] == "segmentation_unconfigured"
    assert not unavailable["candidates"]


@pytest.mark.asyncio
async def test_http_segmenter_limits_and_timeouts(engine):
    _, _, _, original = images(engine)
    data = engine.store.read(original)
    settings = SegmentationSettings(endpoint="https://segment.invalid/segment", max_bytes=4)
    segmenter = HTTPSegmenter(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"too": "large"}))
    )
    with pytest.raises(PreviewError) as error:
        await segmenter.segment(data, ["object"])
    assert error.value.code == "segmentation_oversized"

    def timeout(request):
        raise httpx.ReadTimeout("injected timeout", request=request)

    segmenter = HTTPSegmenter(settings, transport=httpx.MockTransport(timeout))
    with pytest.raises(PreviewError) as error:
        await segmenter.segment(data, ["object"])
    assert error.value.code == "segmentation_unavailable"


@pytest.mark.asyncio
async def test_repeated_proposal_reuses_generation_but_model_version_change_invalidates(engine, node, context):
    _, mask, _, original = images(engine)
    engine.segmenter = Segmenter([("object", mask)])
    intent = {"operation": "attribute", "instruction": "blue", "targets": ["object"]}
    proposals = [await engine.propose_image_edit(original, intent) for _ in range(2)]
    assert proposals[0]["id"] != proposals[1]["id"]
    results = [
        await engine.execute_proposed_edit(
            node,
            {original["id"]: original},
            context=context,
            original_artifact_id=original["id"],
            proposal_id=proposal["id"],
        )
        for proposal in proposals
    ]
    assert results[0].cache["decision"] == "RECOMPUTE"
    assert results[1].cache["decision"] == "CACHE_HIT"
    assert results[1].metadata["edit_proposal"]["id"] == proposals[1]["id"]
    engine.segmenter.corrupt = lambda body: body["model"].update(segmenter_revision="new-version")
    changed = await engine.propose_image_edit(original, intent)
    result = await engine.execute_proposed_edit(
        node,
        {original["id"]: original},
        context=context,
        original_artifact_id=original["id"],
        proposal_id=changed["id"],
    )
    assert result.cache["decision"] == "RECOMPUTE"


def test_local_review_api_and_error_paths(tmp_path):
    image = png(Image.new("RGB", (192, 128), "#688b77"))
    mask = Image.new("L", (192, 128))
    ImageDraw.Draw(mask).ellipse((30, 20, 80, 110), fill=255)
    with TestClient(create_review_app(tmp_path, segmenter=Segmenter([("object", mask)]))) as client:
        source = client.post("/edit-api/images", json={"image_base64": base64.b64encode(image).decode()}).json()[
            "artifact"
        ]
        assert source["uri"].startswith("/edit-api/") and "file:" not in json.dumps(source)
        proposal = client.post(
            "/edit-api/proposals",
            json={
                "source_id": source["id"],
                "intent": {"operation": "attribute", "instruction": "red", "targets": ["object"]},
            },
        ).json()
        assert proposal["status"] == "ready"
        assert client.get(proposal["candidates"][0]["mask_artifact"]["uri"]).status_code == 200
        body = {
            "source_id": source["id"],
            "proposal_id": proposal["id"],
            "image_config": {"provider": "local_fallback"},
        }
        result = client.post("/edit-api/execute", json=body)
        assert result.status_code == 200, result.text
        after = decode(client.get(result.json()["artifact"]["uri"]).content)
        unchanged(decode(image), after, mask)
        second = client.post(
            "/edit-api/proposals",
            json={
                "source_id": source["id"],
                "intent": {"operation": "attribute", "instruction": "red", "targets": ["object"]},
            },
        ).json()
        repeated = client.post("/edit-api/execute", json={**body, "proposal_id": second["id"]})
        assert repeated.status_code == 200
        assert repeated.json()["metadata"]["provider_cache"]["decision"] == "CACHE_HIT"
        bad = copy.deepcopy(body)
        bad["candidate_id"] = "foreign-target"
        assert client.post("/edit-api/execute", json=bad).status_code == 409
        assert client.post("/edit-api/images", json={"image_base64": "bad"}).status_code == 422
        assert client.get("/edit-api/health", headers={"Origin": "https://external.invalid"}).status_code == 403
