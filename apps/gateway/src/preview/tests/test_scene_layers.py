import io
from dataclasses import replace

import pytest
from PIL import Image, ImageChops, ImageDraw
from preview import PreviewError
from preview.contracts import AdapterOutput
from preview.scene_layers import decode, extract_foreground, placed_layer, png, prepare_image_output
from preview.scripts.generic_cases import literal, node


def equal_outside(before, after, mask):
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    for channel in difference.split():
        assert ImageChops.multiply(channel, mask.point(lambda value: 255 if value == 0 else 0)).getbbox() is None


def test_corner_connected_matting_preserves_subject_and_feathers_edges():
    image = Image.new("RGB", (96, 80), "#fdfdfd")
    ImageDraw.Draw(image).ellipse((20, 10, 75, 75), fill="#bf4455")
    result, method = extract_foreground(image)
    assert method == "corner_floodfill_feather"
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((40, 40))[3] == 255
    assert any(0 < value < 255 for value in result.getchannel("A").getdata())
    assert extract_foreground(result)[0].tobytes() == result.tobytes()


@pytest.mark.parametrize("value", [[-0.1, 0, 0.5, 0.5], [0, 0, float("nan"), 1], [0, 0, 2, 1]])
def test_layer_placement_rejects_unbounded_rectangles(value):
    with pytest.raises(PreviewError):
        placed_layer(Image.new("RGBA", (20, 20)), (96, 64), {"rect": value})


@pytest.mark.asyncio
async def test_layer_stack_changes_only_foreground_alpha_and_accepts_new_background(engine, context):
    base = Image.new("RGBA", (192, 128), "#739da5")
    ImageDraw.Draw(base).line((0, 0, 191, 127), fill="#dfe3ac", width=6)
    subject = Image.new("RGBA", (80, 80))
    ImageDraw.Draw(subject).ellipse((8, 8, 70, 75), fill="#d7609d")
    background = engine.store.put(png(base), "image/png", node_id="background", run_id="old")
    foreground = engine.store.put(png(subject), "image/png", node_id="subject", run_id="new")
    spec = {"input": "subject", "role": "subject", "rect": [0.3, 0.1, 0.4, 0.8]}
    layout = node(
        "scene",
        "layout_compose",
        {"background": literal(background["id"]), "subject": literal(foreground["id"])},
        params={"layout": {"mode": "layer_stack", "layers": [spec]}},
    )
    result = await engine.execute_node(layout, {item["id"]: item for item in (background, foreground)}, context=context)
    picture = decode(engine.store.read(result.artifacts[0]))
    alpha = placed_layer(subject, base.size, spec).getchannel("A")
    equal_outside(base, picture, alpha)
    assert ImageChops.difference(base.convert("RGB"), picture.convert("RGB")).getbbox()
    assert result.metadata["base_sha256"] == background["sha256"]
    assert result.metadata["protected_pixel_count"] > 192 * 128 * 0.6
    new_base = Image.new("RGBA", base.size, "#214a71")
    new_background = engine.store.put(png(new_base), "image/png", node_id="background", run_id="new")
    layout["execution"]["inputs"]["background"] = literal(new_background["id"])
    result2 = await engine.execute_node(
        layout, {item["id"]: item for item in (new_background, foreground)}, context=context
    )
    picture2 = decode(engine.store.read(result2.artifacts[0]))
    opaque = alpha.point(lambda value: 255 if value == 255 else 0)
    equal_outside(picture, picture2, opaque.point(lambda value: 0 if value else 255))
    assert new_background["sha256"] != background["sha256"]


@pytest.mark.asyncio
async def test_masked_replace_preserves_every_pixel_outside_explicit_mask(engine, context):
    base = Image.new("RGBA", (96, 64), "#658293")
    candidate = Image.new("RGBA", base.size, "#ffcc66")
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).rectangle((35, 20, 65, 55), fill=255)
    items = [
        engine.store.put(png(image), "image/png", node_id=name, run_id="run")
        for name, image in (("base", base), ("new", candidate), ("mask", mask))
    ]
    layout = node(
        "edit",
        "layout_compose",
        {key: literal(item["id"]) for key, item in zip(("background", "new", "mask"), items)},
        params={
            "layout": {"mode": "masked_replace", "layers": [{"input": "new", "mask_input": "mask", "crop": False}]}
        },
    )
    result = await engine.execute_node(layout, {item["id"]: item for item in items}, context=context)
    picture = decode(engine.store.read(result.artifacts[0]))
    equal_outside(base, picture, mask)
    assert picture.getpixel((40, 30)) == candidate.getpixel((40, 30))
    del layout["execution"]["params"]["layout"]["layers"][0]["mask_input"]
    with pytest.raises(PreviewError, match="requires an explicit"):
        await engine.execute_node(layout, {item["id"]: item for item in items}, context=context)


@pytest.mark.asyncio
async def test_image_node_masked_edit_preserves_original_pixels(engine, context):
    base = Image.new("RGBA", (96, 64), "#738d94")
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).ellipse((25, 10, 70, 60), fill=255)
    original, edit_mask = [
        engine.store.put(png(image), "image/png", node_id="input", run_id="run") for image in (base, mask)
    ]
    item = node("edit", "image_gen", {}, params={"edit_mode": "masked_replace"})
    request = engine.prepare_request(
        item,
        {a["id"]: a for a in (original, edit_mask)},
        context=replace(context, resolved_inputs={"original_image": original["id"], "edit_mask": edit_mask["id"]}),
    )
    generated = Image.new("RGBA", base.size, "#ff8844")
    output = prepare_image_output(AdapterOutput(png(generated), "image/png", "live_api"), request, engine.store)
    equal_outside(base, decode(output.data), mask)


@pytest.mark.asyncio
async def test_fallback_subject_and_append_do_not_change_background_pixels(engine, context):
    def request(subject, detail=""):
        return replace(
            context,
            resolved_inputs={"prompt": subject, "style": "watercolor", "environment": "forest", "detail": detail},
        )

    item = node("image", "image_gen", {})
    results = [
        await engine.execute_node(item, {}, context=request(subject, detail))
        for subject, detail in (
            ("silver-haired traveler", ""),
            ("astronaut cat", ""),
            ("astronaut cat", "floating hamburger"),
        )
    ]
    pictures = [decode(engine.store.read(result.artifacts[0])) for result in results]
    mask = Image.new("L", pictures[0].size)
    box = tuple(
        round(value * scale)
        for value, scale in zip((480, 150, 920, 470), (pictures[0].width / 960, pictures[0].height / 640) * 2)
    )
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    equal_outside(pictures[0], pictures[1], mask)
    detail_mask = Image.new("L", pictures[0].size)
    ImageDraw.Draw(detail_mask).rectangle(
        (
            int(0.07 * pictures[0].width),
            int(0.15 * pictures[0].height),
            int(0.39 * pictures[0].width),
            int(0.56 * pictures[0].height),
        ),
        fill=255,
    )
    equal_outside(pictures[1], pictures[2], detail_mask)
    assert pictures[0].tobytes() != pictures[1].tobytes() != pictures[2].tobytes()


@pytest.mark.asyncio
async def test_offline_subjects_have_alpha_and_empty_details_are_not_generated(engine, context):
    item = node("subject", "image_gen", {"prompt": literal("robot")}, params={"layer_role": "subject"})
    result = await engine.execute_node(item, {}, context=context)
    picture = decode(engine.store.read(result.artifacts[0]))
    assert picture.getchannel("A").getextrema() == (0, 255)
    item["execution"]["params"]["layer_role"] = "detail"
    item["execution"]["inputs"]["prompt"] = literal("")
    result = await engine.execute_node(item, {}, context=context)
    assert decode(engine.store.read(result.artifacts[0])).getchannel("A").getbbox() is None


@pytest.mark.parametrize(
    "variable,mode,value,dirty",
    [
        ("subject", "override", "mechanical robot", {"subject_layer", "scene"}),
        ("detail", "append", "floating hamburger", {"detail_layer", "scene"}),
        ("environment", "override", "night sky", {"environment_plate", "scene"}),
    ],
)
def test_real_runtime_routes_independent_layer_changes(tmp_path, variable, mode, value, dirty):
    import uuid

    from fastapi.testclient import TestClient
    from preview import PreviewSettings
    from preview.integration_server import create_app
    from preview.scripts.layered_cases import scene_case
    from test_c_integration import TOKEN, upload, wait_job

    with TestClient(
        create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback")),
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        prefix = upload(client, scene_case())
        before = wait_job(client, prefix, "draft")
        fork = client.post(
            prefix + "/forks",
            json={
                "request_id": uuid.uuid4().hex,
                "archive_id": before["archive_id"],
                "expected_revision": before["archive"]["graph"]["revision_id"],
                "operations": [{"variable_id": variable, "mode": mode, "value": value}],
            },
        )
        assert fork.status_code == 201, fork.text
        after = wait_job(client, prefix, "draft")
        runs = {run["node_id"]: run for run in after["archive"]["runs"]}
        assert {id for id, run in runs.items() if run["cache"]["decision"] != "CACHE_HIT"} == dirty
        assert runs["scene"]["cache"]["decision"] == "REBUILD"


def test_layered_comic_reuses_shared_layers_and_rebuilds_panels(tmp_path):
    from fastapi.testclient import TestClient
    from preview import PreviewSettings
    from preview.integration_server import create_app
    from preview.scripts.layered_cases import comic_case
    from test_c_integration import TOKEN, upload, wait_job

    with TestClient(
        create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback")),
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        prefix = upload(client, comic_case())
        result = wait_job(client, prefix, "draft")
        output = next(run for run in result["archive"]["runs"] if run["node_id"] == "comic")["outputs"]["out"]
        with Image.open(io.BytesIO(client.get(prefix + "/artifacts/" + output).content)) as picture:
            assert picture.height > picture.width
