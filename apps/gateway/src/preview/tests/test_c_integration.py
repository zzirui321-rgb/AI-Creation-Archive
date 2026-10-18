"""Actual A/D/B HTTP acceptance, replacing the retired fixed-session API tests."""

import base64
import copy
import hashlib
import io
import json
import time
from uuid import uuid4

import pytest
from aica_schema.protocol import empty_archive, require_archive
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageStat
from preview import PreviewSettings
from preview.integration_server import create_app
from preview.runtime_bridge import stable_seed
from preview.scripts.generic_cases import cases, literal, node

TOKEN = "generic-runtime-test-token-1234567890"


def test_nested_reference_lists_do_not_change_seed_across_stages():
    draft = {"prompt": "scene", "references": [{"image": ["draft-a", "draft-b"]}]}
    final = {"prompt": "scene", "references": [{"image": ["final-a", "final-b"]}]}
    assert stable_seed("node", draft, {"draft-a", "draft-b"}) == stable_seed("node", final, {"final-a", "final-b"})
    assert stable_seed("node", draft, {"draft-a", "draft-b"}) != stable_seed(
        "node", {**draft, "prompt": "another"}, {"draft-a", "draft-b"}
    )


def wait_job(client, url, stage):
    current = client.get(url + "/archive").json()
    response = client.post(
        url + "/executions",
        json={
            "request_id": str(uuid4()),
            "archive_id": current["archive_id"],
            "expected_revision": current["archive"]["graph"]["revision_id"],
            "stage": stage,
            "execute": True,
            "record": True,
        },
    )
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = client.get(url + "/executions/" + response.json()["job_id"]).json()["execution"]
        if job["state"] not in {"queued", "running"}:
            assert job["state"] == "succeeded", job
            result = client.get(url + "/archive").json()
            require_archive(result["archive"])
            return result
        time.sleep(0.01)
    raise TimeoutError("Actual D worker timeout")


def upload(client, archive):
    project = client.post("/api/projects", json={"title": archive["metadata"]["title"]}).json()["project"]["id"]
    data = json.dumps(archive).encode()
    sha = hashlib.sha256(data).hexdigest()
    response = client.post(
        f"/api/projects/{project}/imports",
        content=data,
        headers={"Content-Type": "application/json", "Idempotency-Key": "sha256:" + sha, "X-AiCA-Bundle-SHA256": sha},
    )
    assert response.status_code == 201, response.text
    return f"/api/projects/{project}"


@pytest.mark.parametrize(
    "name,changed,value,dirty,active",
    [
        ("ai-comic", "character", "pixel cat astronaut", {"character", "panel_1", "panel_2", "layout"}, 7),
        ("field-guide", "creature", "transparent jellyfish", {"wildlife_plate", "field_guide"}, 5),
        ("self-archive", "framework", "React", {"build_configuration", "review_note", "release_report"}, 4),
    ],
)
def test_arbitrary_archive_real_incremental_runtime(tmp_path, name, changed, value, dirty, active):
    app = create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        source = cases()[name]
        original_definition = copy.deepcopy(source["graph"])
        url = upload(client, source)
        for stage in ("draft", "final"):
            wait_job(client, url, stage)
            plan = client.get(url + f"/plan?stage={stage}").json()
            assert len(plan["decisions"]) == active
            assert set(plan["decisions"].values()) == {"CACHE_HIT"}, plan
        before = client.get(url + "/archive").json()
        fork = client.post(
            url + "/forks",
            json={
                "request_id": str(uuid4()),
                "archive_id": before["archive_id"],
                "expected_revision": before["archive"]["graph"]["revision_id"],
                "operations": [{"variable_id": changed, "mode": "override", "value": value}],
            },
        )
        assert fork.status_code == 201, fork.text
        assert set(fork.json()["dirty_node_ids"]) == dirty
        assert client.get(url + "/archive?archive_id=" + before["archive_id"]).json() == before
        for stage in ("draft", "final"):
            plan = client.get(url + f"/plan?stage={stage}").json()
            assert {identifier for identifier, state in plan["decisions"].items() if state != "CACHE_HIT"} == dirty, (
                plan
            )
            result = wait_job(client, url, stage)["archive"]
            revision = result["graph"]["revision_id"]
            latest = {
                run["node_id"]: run
                for run in result["runs"]
                if run["revision_id"] == revision and run.get("preview_stage") == stage
            }
            assert len(latest) == active
            assert {
                identifier for identifier, run in latest.items() if run["cache"]["decision"] != "CACHE_HIT"
            } == dirty
            for identifier, run in latest.items():
                if (
                    identifier in dirty
                    and next(node for node in result["graph"]["nodes"] if node["id"] == identifier)["kind"]
                    == "composition"
                ):
                    assert run["cache"]["decision"] == "REBUILD"
                for artifact_id in run["outputs"].values():
                    artifact = next(item for item in result["artifacts"] if item["id"] == artifact_id)
                    content = client.get(url + "/artifacts/" + artifact_id)
                    assert hashlib.sha256(content.content).hexdigest() == artifact["sha256"]
            assert "rejected_experiment" not in latest
        repeated = wait_job(client, url, "final")["archive"]
        assert all(run["cache"]["decision"] == "CACHE_HIT" for run in repeated["runs"][-active:])
        path = client.get(url + "/golden-path").json()["node_ids"]
        assert len(path) == active and "rejected_experiment" not in path
        assert source["graph"] == original_definition
        assert client.get(url + "/archive", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_unknown_executor_blocks_and_no_template_routes(tmp_path):
    with TestClient(
        create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback")),
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        source = cases()["field-guide"]
        source["graph"]["nodes"][0]["execution"]["tool"] = "unregistered-model"
        url = upload(client, source)
        assert not client.get(url + "/plan").json()["executable"]
        assert client.post("/preview/review/sessions", json={}).status_code == 404


def test_json_literal_with_artifact_id_retains_other_fields(tmp_path):
    source = empty_archive("Structured content")
    content = b"known input bytes"
    sha = hashlib.sha256(content).hexdigest()
    source["artifacts"] = [
        {
            "id": "known-artifact",
            "uri": "objects/" + sha,
            "mime_type": "text/plain",
            "size": len(content),
            "sha256": sha,
            "producer_node_id": None,
            "producer_run_id": None,
            "availability": "available",
        }
    ]
    source["extensions"]["aica.embedded_artifacts"] = {
        "version": "0.1",
        "encoding": "base64",
        "objects": {sha: base64.b64encode(content).decode()},
    }
    expected = {"artifact_id": "known-artifact", "description": "Keep the actual JSON object"}
    source["graph"]["nodes"] = [node("structured", "json_materialize", {"content": literal(expected)})]
    with TestClient(
        create_app(tmp_path, {"tester": TOKEN}, PreviewSettings(provider="local_fallback")),
        headers={"Authorization": "Bearer " + TOKEN},
    ) as client:
        url = upload(client, source)
        result = wait_job(client, url, "draft")["archive"]
        identifier = result["runs"][-1]["outputs"]["out"]
        assert client.get(url + "/artifacts/" + identifier).json() == expected


@pytest.mark.asyncio
async def test_semantic_fallbacks_differ_visually_and_promote_same_seed(engine, node, context):
    from dataclasses import replace

    pictures = []
    for prompt, subject in (
        ("watercolor forest dragon", "creature"),
        ("minimal web interface", "interface"),
        ("ocean transparent jellyfish", "ocean"),
        ("watercolor forest robot", "mechanical"),
    ):
        result = await engine.execute_node(
            node, {}, context=replace(context, resolved_inputs={"prompt": prompt}, seed=53)
        )
        image = Image.open(io.BytesIO(engine.store.read(result.artifacts[0])))
        assert image.info["seed"] == "53" and image.info["resolvedPrompt"] == prompt
        assert image.info["subject"] == subject
        final = await engine.execute_node(
            node, {}, context=replace(context, mode="final", resolved_inputs={"prompt": prompt}, seed=53)
        )
        final_image = Image.open(io.BytesIO(engine.store.read(final.artifacts[0])))
        assert final_image.width > image.width and final.metadata["seed"] == result.metadata["seed"]
        assert final_image.info["subject"] == image.info["subject"]
        assert final_image.info["inputHash"] == image.info["inputHash"]
        pictures.append(image.convert("RGB").resize((960, 640)))
    for index, (before, after) in enumerate(zip(pictures, pictures[1:])):
        if index < 2:
            assert sum(ImageStat.Stat(ImageChops.difference(before, after)).mean) > 60
        subject_box = (490, 170, 900, 450)
        assert sum(ImageStat.Stat(ImageChops.difference(before.crop(subject_box), after.crop(subject_box))).mean) > 60
