import copy
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic, sleep

import httpx
import pytest
from aica_gateway.app import create_app
from aica_mcp.store import Recorder
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import dumps, loads, new_id, utc_now
from conftest import OTHER_TOKEN, TOKEN, project_url
from fastapi.testclient import TestClient
from graph_contract_design.cases import make_case
from graph_parser_fixture import model_reply, proposal_for
from jsonschema import Draft202012Validator


def wait_parse(client, url, parse_id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(url + "/semantic-parses/" + parse_id)
        assert response.status_code == 200, response.text
        job = response.json()["parse"]
        if job["state"] not in {"queued", "running"}:
            return job
        sleep(0.01)
    pytest.fail("Graph job did not complete within test deadline")


def import_source(client, tmp_path, name="multiturn", source=None):
    source = source or tmp_path / ("source-" + new_id())
    if not source.exists():
        source.mkdir()
        case = make_case(name)
        archive = case["archive"]
        (source / "events.jsonl").write_bytes(case["history"]["fixture-history"])
        for artifact in archive["artifacts"][1:]:
            target = source / artifact["uri"]
            target.parent.mkdir(exist_ok=True)
            content = b"synthetic-artifact:" + artifact["id"].encode()
            target.write_bytes(content)
            artifact.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content), availability="available")
        (source / "aica.json").write_bytes(dumps(archive).encode())
    raw = (source / "aica.json").read_bytes()
    path = tmp_path / (new_id() + ".zip")
    report = build_bundle(source, path)
    url = project_url(client)
    response = client.post(url + "/imports", content=path.read_bytes(), headers={
        "Content-Type": CONTENT_TYPE, "X-AiCA-Bundle-SHA256": report["bundle_sha256"],
        "Idempotency-Key": "sha256:" + report["bundle_sha256"]})
    assert response.status_code == 201, response.text
    import_id = response.json()["import_id"]
    response = client.post(url + "/semantic-parse-input", json={"import_id": import_id})
    assert response.status_code == 200, response.text
    prepared = response.json()["input"]
    payload = {"request_id": new_id(), "import_id": import_id, "input_sha256": prepared["input_sha256"],
               "allow_external_processing": True}
    return url, payload, raw, prepared


@pytest.fixture
def graph_environment(tmp_path):
    calls, options = [], {}

    def handler(request):
        calls.append(request)
        if options.get("started"):
            options["started"].set()
            assert options["release"].wait(5)
        if options.get("timeout"):
            raise httpx.ReadTimeout("transport test")
        prepared = loads(loads(request.content.decode())["messages"][1]["content"])["input"]
        if options.get("truncated"):
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"groups":['}}],
                                            "usage": {"completion_tokens": 4096}})
        proposal = proposal_for(prepared)
        if options.get("bad_role"):
            proposal["groups"][0]["purpose"]["attributed_to"] = "user"
        return httpx.Response(options.get("status", 200), json=model_reply(proposal))

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="private-mock-model-key"), httpx.MockTransport(handler))
    directory = tmp_path / "graph-platform"
    tokens = {"alice": TOKEN, "bob": OTHER_TOKEN}
    app = create_app(directory, tokens, graph_provider=provider)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield app, client, calls, options, directory, tokens, provider


def test_a_bundle_to_c_graph_schema_evidence_persistence_and_reparse(graph_environment, tmp_path):
    app, client, calls, _, _, _, _ = graph_environment
    url, payload, raw, prepared = import_source(client, tmp_path)
    first = client.post(url + "/semantic-parses", json=payload)
    assert first.status_code == 202
    job = wait_parse(client, url, first.json()["parse"]["parse_id"])
    assert job["state"] == "succeeded", job
    assert job["usage"]["total_tokens"] == 180
    graph_url = url + "/semantic-graphs/" + job["graph_id"]
    response = client.get(graph_url, params={"revision_id": job["revision_id"]})
    assert response.status_code == 200, response.text
    graph = response.json()["graph"]
    GraphDocument.model_validate(graph)
    schema = Path(__file__).resolve().parents[3] / "packages/runtime/design/graph-v0.1/GraphDocument.schema.json"
    Draft202012Validator(loads(schema.read_text(encoding="utf-8"))).validate(graph)
    assert len(graph["nodes"]) == 4 and len(graph["edges"]) == 1
    assert graph["parse"]["model"] == "deepseek-flash" and graph["parse"]["mode"] == "rules_llm"
    assert graph["parse"]["input_sha256"] == prepared["input_sha256"]
    for evidence in graph["evidence"]:
        result = client.get(graph_url + "/evidence/" + evidence["id"], params={"revision_id": job["revision_id"]})
        assert result.status_code == 200 and result.json()["availability"] == "available", result.text
    user = next(item for item in prepared["facts"] if item["role"] == "user")
    assert client.get(graph_url + "/evidence/" + user["id"], params={"revision_id": job["revision_id"]}).json()["role"] == "user"
    assert client.post(url + "/semantic-parses", json=payload).status_code == 200 and len(calls) == 1
    assert client.get(graph_url, params={"revision_id": new_id()}).status_code == 404
    assert client.get(graph_url).status_code == 422
    assert client.get(url + "/semantic-parses").json()["parses"][0]["parse_id"] == job["id"]
    assert app.state.repository.archive_bytes("alice", url.split("/")[-1], payload["import_id"]) == raw
    next_job = client.post(url + "/semantic-parses", json={**payload, "request_id": new_id()}).json()["parse"]
    next_job = wait_parse(client, url, next_job["id"])
    assert next_job["state"] == "succeeded" and next_job["graph_id"] != job["graph_id"]
    assert client.get(graph_url, params={"revision_id": job["revision_id"]}).json()["graph"] == graph
    assert "private-mock-model-key" not in client.get("/api/graph-parser").text


def test_ownership_consent_hash_and_idempotency_gate_model_calls(graph_environment, tmp_path):
    _, client, calls, _, _, _, _ = graph_environment
    url, payload, _, _ = import_source(client, tmp_path)
    assert client.post(url + "/semantic-parses", json={**payload, "allow_external_processing": False}).status_code == 403
    assert client.post(url + "/semantic-parses", json={**payload, "input_sha256": "f" * 64}).status_code == 409
    bob = {"Authorization": "Bearer " + OTHER_TOKEN}
    assert client.post(url + "/semantic-parse-input", json={"import_id": payload["import_id"]}, headers=bob).status_code == 404
    assert client.post(url + "/semantic-parses", json=payload, headers=bob).status_code == 404
    assert not calls
    response = client.post(url + "/semantic-parses", json=payload)
    job = wait_parse(client, url, response.json()["parse"]["id"])
    assert client.get(url + "/semantic-parses/" + job["id"], headers=bob).status_code == 404
    assert client.get(url + "/semantic-parses", headers=bob).status_code == 404
    graph_url = url + "/semantic-graphs/" + job["graph_id"]
    assert client.get(graph_url, params={"revision_id": job["revision_id"]}, headers=bob).status_code == 404
    assert client.get(graph_url + "/evidence/not-known", params={"revision_id": job["revision_id"]}, headers=bob).status_code == 404
    assert client.post(url + "/semantic-parses", json={**payload, "node_ids": []}).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize("option,state,code", [("bad_role", "failed", "invalid_semantic_graph"),
                                              ("timeout", "unknown", "provider_connection"),
                                              ("status", "failed", "provider_rejected")])
def test_failed_or_uncertain_jobs_do_not_publish_or_retry(graph_environment, tmp_path, option, state, code):
    _, client, calls, options, _, _, _ = graph_environment
    options[option] = 503 if option == "status" else True
    url, payload, _, _ = import_source(client, tmp_path)
    job = client.post(url + "/semantic-parses", json=payload).json()["parse"]
    result = wait_parse(client, url, job["id"])
    assert result["state"] == state and result["error_code"] == code
    assert result["graph_id"] is None
    assert client.post(url + "/semantic-parses", json=payload).status_code == 200
    assert len(calls) == 1


def test_truncation_diagnostic_survives_polling_without_retry(graph_environment, tmp_path):
    _, client, calls, options, _, _, _ = graph_environment
    options["truncated"] = True
    url, payload, _, _ = import_source(client, tmp_path)
    job = client.post(url + "/semantic-parses", json=payload).json()["parse"]
    result = wait_parse(client, url, job["id"])
    assert result["state"] == "failed"
    assert result["error_detail"]["stage"] == "output_truncated"
    assert result["error_detail"]["finish_reason"] == "length"
    assert result["usage"]["completion_tokens"] == 4096
    assert "completion" not in result["error_detail"]
    assert client.post(url + "/semantic-parses", json=payload).status_code == 200
    assert len(calls) == 1


def test_concurrent_retries_dispatch_once_and_busy_project_is_rejected(graph_environment, tmp_path):
    _, client, calls, options, _, _, _ = graph_environment
    url, payload, _, _ = import_source(client, tmp_path)
    options.update(started=threading.Event(), release=threading.Event())
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            responses = list(workers.map(lambda _: client.post(url + "/semantic-parses", json=copy.deepcopy(payload)), range(4)))
        assert all(response.status_code == 202 for response in responses), [r.text for r in responses]
        assert len({r.json()["parse"]["id"] for r in responses}) == 1
        assert options["started"].wait(3)
        assert client.post(url + "/semantic-parses", json={**payload, "request_id": new_id()}).status_code == 409
    finally:
        options["release"].set()
    assert wait_parse(client, url, responses[0].json()["parse"]["id"])["state"] == "succeeded"
    assert len(calls) == 1


def test_restart_preserves_success_and_marks_interrupted_unknown_without_resending(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        prepared = loads(loads(request.content.decode())["messages"][1]["content"])["input"]
        return httpx.Response(200, json=model_reply(proposal_for(prepared)))

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="test-key"), httpx.MockTransport(handler))
    directory, tokens = tmp_path / "restart", {"alice": TOKEN}
    app = create_app(directory, tokens, graph_provider=provider)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        url, payload, _, _ = import_source(client, tmp_path)
        job = client.post(url + "/semantic-parses", json=payload).json()["parse"]
        job = wait_parse(client, url, job["id"])
        graph_url = url + "/semantic-graphs/" + job["graph_id"]
        graph = client.get(graph_url, params={"revision_id": job["revision_id"]}).json()
        interrupted_payload = {**payload, "request_id": new_id()}
        second = client.post(url + "/semantic-parses", json=interrupted_payload).json()["parse"]
        wait_parse(client, url, second["id"])
    with app.state.repository.connection(write=True) as database:
        database.execute("UPDATE semantic_graph_parses SET state='running',graph=NULL WHERE id=?", (second["id"],))
    with TestClient(create_app(directory, tokens, graph_provider=provider), headers={"Authorization": "Bearer " + TOKEN}) as client:
        assert client.get(graph_url, params={"revision_id": job["revision_id"]}).json() == graph
        retry = client.post(url + "/semantic-parses", json=interrupted_payload)
        assert retry.status_code == 200 and retry.json()["parse"]["state"] == "unknown"
        assert retry.json()["parse"]["error_code"] == "service_restarted"
    assert len(calls) == 2


def test_real_a_recorder_export_is_consumed_without_private_database(graph_environment, tmp_path):
    _, client, _, _, _, _, _ = graph_environment
    source = tmp_path / "a-recorder-source"
    source.mkdir()
    recorder = Recorder(source)
    try:
        credentials = recorder.start_session(new_id(), True)
        event = {"event_id": new_id(), "source_id": "actual-recorder-test", "source_seq": 1,
                 "session_id": credentials["session_id"], "agent_id": None, "type": "message.recorded",
                 "timestamp": utc_now(), "causal_parents": [], "payload": {"role": "user", "content": "制作月球海报。"}}
        recorder.append(event, credentials["session_token"])
        recorder.flush()
    finally:
        recorder.close()
    url, payload, _, prepared = import_source(client, tmp_path, source=source)
    assert len(prepared["conversation_state"]["message_timeline"]) == 1
    assert prepared["coverage"]["omitted_history_artifact_ids"] == []
    assert credentials["session_token"] not in dumps(prepared)
    result = client.post(url + "/semantic-parses", json=payload)
    job = wait_parse(client, url, result.json()["parse"]["id"])
    assert job["state"] == "succeeded"
    graph = client.get(url + "/semantic-graphs/" + job["graph_id"], params={"revision_id": job["revision_id"]}).json()["graph"]
    assert len(graph["nodes"]) == 1 and graph["nodes"][0]["source_node_ids"] == []
