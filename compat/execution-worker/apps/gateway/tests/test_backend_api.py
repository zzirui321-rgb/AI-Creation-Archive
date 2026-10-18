import copy
import hashlib
import threading
import time

import pytest
from aica_gateway.app import create_app
from aica_mcp.store import Recorder
from aica_mcp.transfer import CONTENT_TYPE
from aica_schema.protocol import dumps, new_id, require_archive, utc_now
from fastapi.testclient import TestClient


def import_project(client, bundle):
    path, report, image, document = bundle
    project = client.post("/api/projects", json={"title": "Fixture"}).json()["project"]
    url = f"/api/projects/{project['id']}"
    headers = {"Content-Type": CONTENT_TYPE, "Idempotency-Key": "sha256:" + report["bundle_sha256"],
               "X-AiCA-Bundle-SHA256": report["bundle_sha256"]}
    response = client.post(url + "/imports", headers=headers, content=path.read_bytes())
    assert response.status_code == 201, response.text
    return url, response.json(), headers


def execution_payload(client, url, **changes):
    current = client.get(url + "/archive").json()
    payload = {"request_id": new_id(), "archive_id": current["archive_id"],
               "expected_revision": current["archive"]["graph"]["revision_id"], "record": True, "execute": True,
               "stage": "draft"}
    payload.update(changes)
    return payload


def wait_job(client, url, job_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(url + "/executions/" + job_id).json()["execution"]
        if job["state"] not in {"queued", "running"}:
            return job
        time.sleep(0.02)
    pytest.fail("Execution did not finish")


def execute(client, url, **changes):
    payload = execution_payload(client, url, **changes)
    response = client.post(url + "/executions", json=payload)
    assert response.status_code == 202, response.text
    job = wait_job(client, url, response.json()["job_id"])
    return job, payload


def test_upload_preserves_raw_archive_and_image_and_is_idempotent(client, app, bundle_factory, workflow):
    bundle = bundle_factory(workflow)
    url, receipt, headers = import_project(client, bundle)
    path, report, image, document = bundle
    assert receipt["archive_sha256"] == report["archive_sha256"]
    assert client.get(url + "/archive").json()["archive"] == document
    response = client.get(url + "/artifacts/picture")
    assert response.content == image and response.headers["content-type"] == "image/png"
    assert response.headers["x-aica-sha256"] == hashlib.sha256(image).hexdigest()
    assert client.post(url + "/imports", headers=headers, content=path.read_bytes()).json() == receipt
    assert len(client.get(url + "/archives").json()["archives"]) == 1
    with app.state.store.transaction() as database:
        raw = database.execute("SELECT body FROM archives WHERE id=?", (receipt["import_id"],)).fetchone()["body"]
    assert hashlib.sha256(raw.encode()).hexdigest() == receipt["archive_sha256"]
    assert not list((app.state.store.directory / "uploads").iterdir())


def test_authentication_and_project_isolation(client, bundle_factory, workflow):
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    response = client.get(url + "/graph", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401 and response.json()["status"] == "error"
    bob = {"Authorization": "Bearer bob-development-test-token-1234567890"}
    for path in ("/archive", "/graph", "/archives", "/artifacts/picture", "/plan", "/golden-path"):
        assert client.get(url + path, headers=bob).status_code == 404
    assert client.get("/api/projects", headers=bob).json()["projects"] == []
    anonymous = client.get("/api/projects", headers={"Authorization": ""})
    assert anonymous.status_code == 401


def test_invalid_upload_is_not_published(client, app, bundle_factory, workflow):
    bundle = bundle_factory(workflow)
    url, receipt, headers = import_project(client, bundle)
    corrupt = b"invalid zip"
    sha256 = hashlib.sha256(corrupt).hexdigest()
    headers.update({"X-AiCA-Bundle-SHA256": sha256, "Idempotency-Key": "sha256:" + sha256})
    assert client.post(url + "/imports", headers=headers, content=corrupt).status_code == 422
    assert client.get(url + "/archive").json()["archive_id"] == receipt["import_id"]
    headers["Content-Length"] = str(513 * 1024 * 1024)
    assert client.post(url + "/imports", headers=headers, content=b"").status_code == 413
    assert not list((app.state.store.directory / "uploads").iterdir())


def test_fork_variable_modes_revision_guard_and_history(client, bundle_factory, workflow):
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    original = client.get(url + "/archive").json()
    payload = {"request_id": new_id(), "archive_id": original["archive_id"],
               "expected_revision": workflow["graph"]["revision_id"],
               "operations": [{"variable_id": "text", "mode": "override", "value": "new"},
                              {"variable_id": "text", "mode": "append", "value": "value"},
                              {"variable_id": "text", "mode": "keep"}]}
    response = client.post(url + "/forks", json=payload)
    assert response.status_code == 201, response.text
    fork = response.json()
    assert fork["dirty_node_ids"] == ["root", "child"]
    current = client.get(url + "/archive").json()["archive"]
    require_archive(current)
    assert current["variables"]["text"]["value"] == "new value"
    assert client.post(url + "/forks", json=payload).json() == fork
    payload["request_id"] = new_id()
    assert client.post(url + "/forks", json=payload).status_code == 409
    historical = client.get(url + "/revisions/" + workflow["graph"]["revision_id"]).json()["definition"]
    assert historical["variables"]["text"]["value"] == "hello"
    assert client.get(url + "/archive", params={"archive_id": original["archive_id"]}).json() == original


def test_runtime_cache_dirty_propagation_and_golden_path(client, bundle_factory, workflow):
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    job, payload = execute(client, url)
    assert job["state"] == "succeeded", job
    first = client.get(url + "/archive").json()["archive"]
    assert {run["cache"]["decision"] for run in first["runs"]} == {"RECOMPUTE"}
    assert set(client.get(url + "/golden-path").json()["node_ids"]) == {"root", "child", "other"}
    repeated = client.post(url + "/executions", json=payload)
    assert repeated.json()["job_id"] == job["id"]
    second_job, payload = execute(client, url)
    assert second_job["state"] == "succeeded", second_job
    listed = client.get(url + "/executions?limit=1").json()["executions"]
    assert len(listed) == 1 and listed[0]["id"] == second_job["id"]
    assert client.get(url + "/executions", headers={"Authorization": "Bearer bob-development-test-token-1234567890"}).status_code == 404
    second = client.get(url + "/archive").json()["archive"]
    current_runs = [run for run in second["runs"] if run["trace_id"] == second["runs"][-1]["trace_id"]]
    assert len(current_runs) == 3 and all(run["cache"]["decision"] == "CACHE_HIT" for run in current_runs)
    before = client.get(url + "/archive").json()
    fork = client.post(url + "/forks", json={"request_id": new_id(), "archive_id": before["archive_id"],
                       "expected_revision": before["archive"]["graph"]["revision_id"],
                       "operations": [{"variable_id": "text", "mode": "override", "value": "changed"}]}).json()
    assert fork["dirty_node_ids"] == ["root", "child"]
    third_job, payload = execute(client, url)
    assert third_job["state"] == "succeeded", third_job
    third = client.get(url + "/archive").json()["archive"]
    runs = {run["node_id"]: run for run in third["runs"] if run["trace_id"] == third["runs"][-1]["trace_id"]}
    assert runs["other"]["cache"]["decision"] == "CACHE_HIT"
    assert runs["root"]["cache"]["decision"] == "RECOMPUTE"
    assert runs["child"]["cache"]["decision"] == "RECOMPUTE"
    root_output = runs["root"]["outputs"]["text"]
    assert client.get(url + "/artifacts/" + root_output).content == b"changed"
    require_archive(third)


@pytest.mark.parametrize("field", ["record", "execute"])
def test_execution_needs_separate_explicit_consent(client, bundle_factory, workflow, field):
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    payload = execution_payload(client, url, **{field: False})
    assert client.post(url + "/executions", json=payload).status_code == 403


@pytest.mark.parametrize("kind", ["manual", "command", "script", "unknown"])
def test_preflight_refuses_unregistered_or_unsafe_execution(client, bundle_factory, workflow, kind):
    node = workflow["graph"]["nodes"][0]["execution"]
    node["kind"] = "mcp" if kind == "unknown" else kind
    node["tool"] = "unregistered"
    if kind in {"command", "script"}:
        node.update({"argv": ["must-not-run"], "runtime_ref": "untrusted@1"})
    if kind == "script":
        node["script_artifact_id"] = "picture"
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    assert not client.get(url + "/plan").json()["executable"]
    assert client.post(url + "/executions", json=execution_payload(client, url)).status_code == 422


def test_cancellation_and_project_mutations_are_serialized(client, registry, bundle_factory, workflow):
    started, release = threading.Event(), threading.Event()

    def waiting(inputs, params, context):
        started.set()
        assert release.wait(5)
        context.check_cancelled()
        return {"text": "done"}

    registry.register("wait", "1", waiting)
    workflow["graph"]["nodes"][0]["execution"]["tool"] = "wait"
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    payload = execution_payload(client, url)
    job_id = client.post(url + "/executions", json=payload).json()["job_id"]
    try:
        assert started.wait(5)
        other = copy.deepcopy(payload)
        other["request_id"] = new_id()
        assert client.post(url + "/executions", json=other).status_code == 409
        assert client.post(url + f"/executions/{job_id}/cancel").status_code == 200
    finally:
        release.set()
    job = wait_job(client, url, job_id)
    assert job["state"] == "cancelled", job
    archive = client.get(url + "/archive").json()["archive"]
    assert archive["runs"][-1]["state"] == "cancelled"
    assert not {"root", "child"} & set(client.get(url + "/golden-path").json()["node_ids"])


def test_failed_tool_stops_dependents_and_keeps_failure_record(client, registry, bundle_factory, workflow):
    def failing(inputs, params, context):
        raise RuntimeError("private-provider-secret")

    registry.register("fail", "1", failing)
    workflow["graph"]["nodes"][0]["execution"]["tool"] = "fail"
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    job, payload = execute(client, url)
    assert job["state"] == "failed", job
    archive = client.get(url + "/archive").json()["archive"]
    assert not any(run["node_id"] == "child" for run in archive["runs"])
    assert any(run["state"] == "failed" for run in archive["runs"])
    assert "private-provider-secret" not in dumps(archive)


def test_restart_preserves_data_and_marks_unstarted_job_unknown(app, bundle_factory, workflow):
    store = app.state.store
    auth = {"Authorization": "Bearer alice-development-test-token-12345678"}
    with TestClient(app, headers=auth) as first:
        url, receipt, headers = import_project(first, bundle_factory(workflow))
        payload = execution_payload(first, url)
        project_id = url.rsplit("/", 1)[-1]
        result, created = store.create_job("alice", project_id, payload)
        assert created
    with TestClient(create_app(store.directory, {"alice": "alice-development-test-token-12345678"}),
                    headers=auth) as restarted:
        job = restarted.get(url + "/executions/" + result["job_id"]).json()["execution"]
        assert job["state"] == "unknown" and job["result"]["code"] == "server_restarted"
        assert restarted.get(url + "/artifacts/picture").status_code == 200


def test_restart_marks_persisted_running_attempt_unknown_without_replay(app, bundle_factory, workflow):
    store = app.state.store
    auth = {"Authorization": "Bearer alice-development-test-token-12345678"}
    with TestClient(app, headers=auth) as first:
        url, receipt, headers = import_project(first, bundle_factory(workflow))
        payload = execution_payload(first, url)
        project_id = url.rsplit("/", 1)[-1]
        result, created = store.create_job("alice", project_id, payload)
        archive = first.get(url + "/archive").json()["archive"]
        path = store.prepare_workspace(project_id, result["job_id"], archive)
        recorder = Recorder(path)
        try:
            credentials = recorder.start_session(new_id(), True)
            harness_id, agent_id, run_id = new_id(), new_id(), new_id()
            values = [
                ("harness.registered", {"id": harness_id, "name": "recovery fixture", "version": "1",
                                        "config": {}, "state": {}, "availability": {}}),
                ("agent.registered", {"id": agent_id, "session_id": credentials["session_id"],
                                      "harness_id": harness_id, "parent_agent_id": None, "client": "test", "model": {}}),
                ("tool.started", {"id": run_id, "trace_id": new_id(), "node_id": "root",
                                  "revision_id": archive["graph"]["revision_id"], "session_id": credentials["session_id"],
                                  "agent_id": agent_id, "harness_id": harness_id, "state": "running",
                                  "started_at": utc_now(), "inputs": {}, "outputs": {}, "artifact_ids": [],
                                  "provenance": {"mode": "recorded", "evidence": []}}),
            ]
            for sequence, (kind, value) in enumerate(values, 1):
                event = {"event_id": new_id(), "source_id": "recovery-test", "source_seq": sequence,
                         "session_id": credentials["session_id"], "agent_id": agent_id if kind == "tool.started" else None,
                         "type": kind,
                         "timestamp": utc_now(), "causal_parents": [], "payload": value}
                recorder.append(event, credentials["session_token"])
            recorder.flush()
            store.checkpoint(project_id, result["job_id"], path, recorder.read_archive()["archive"])
            store.set_job_state(result["job_id"], "running")
        finally:
            recorder.close()
    with TestClient(create_app(store.directory, {"alice": "alice-development-test-token-12345678"}),
                    headers=auth) as restarted:
        job = restarted.get(url + "/executions/" + result["job_id"]).json()["execution"]
        assert job["state"] == "unknown"
        document = restarted.get(url + "/archive").json()["archive"]
        assert len(document["runs"]) == 1 and document["runs"][0]["id"] == run_id
        assert document["runs"][0]["state"] == "unknown"
        require_archive(document)


def test_missing_cached_bytes_rebuild_from_registered_executor(client, app, bundle_factory, workflow):
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    first, payload = execute(client, url)
    assert first["state"] == "succeeded"
    archive = client.get(url + "/archive").json()["archive"]
    output = next(run for run in archive["runs"] if run["node_id"] == "root")["outputs"]["text"]
    record = next(item for item in archive["artifacts"] if item["id"] == output)
    project_id = url.rsplit("/", 1)[-1]
    app.state.store.blob_path(project_id, record["sha256"]).unlink()
    workflow["metadata"]["title"] = "Fresh source with the same execution inputs"
    path, report, image, source = bundle_factory(workflow)
    response = client.post(url + "/imports", content=path.read_bytes(), headers={"Content-Type": CONTENT_TYPE,
                            "Idempotency-Key": "sha256:" + report["bundle_sha256"],
                            "X-AiCA-Bundle-SHA256": report["bundle_sha256"]})
    assert response.status_code == 201, response.text
    rebuilt, payload = execute(client, url, target_node_ids=["root"])
    assert rebuilt["state"] == "succeeded", rebuilt
    archive = client.get(url + "/archive").json()["archive"]
    assert archive["runs"][-1]["cache"]["decision"] == "REBUILD"


def test_targeted_execution_and_preview_stage_are_passed_to_registered_adapter(client, registry, bundle_factory, workflow):
    stages = []

    def preview(inputs, params, context):
        stages.append(context.stage)
        return {"text": context.stage}

    registry.register("preview-test", "1", preview, kind="model")
    definition = workflow["graph"]["nodes"][0]["execution"]
    definition.update({"kind": "model", "tool": "preview-test"})
    url, receipt, headers = import_project(client, bundle_factory(workflow))
    for stage in ("draft", "final", "final"):
        job, payload = execute(client, url, target_node_ids=["child"], stage=stage)
        assert job["state"] == "succeeded", job
    assert stages == ["draft", "final", "final"]
    document = client.get(url + "/archive").json()["archive"]
    assert {run["node_id"] for run in document["runs"]} == {"root", "child"}


def test_gateway_rejects_another_writer_for_the_same_data_directory(client, app):
    from aica_schema.protocol import ProtocolError

    second = create_app(app.state.store.directory, {"alice": "alice-development-test-token-12345678"})
    with pytest.raises(ProtocolError, match="already running"), TestClient(second):
        pass
