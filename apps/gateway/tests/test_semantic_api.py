import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from aica_gateway.app import create_app
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_runtime.deepseek import DeepSeekProvider, ProviderSettings
from aica_schema.protocol import dumps, new_id
from conftest import OTHER_TOKEN, TOKEN, project_url
from fastapi.testclient import TestClient
from semantic_fixture import sample_archive, sample_draft


def setup_project(client, tmp_path):
    archive, raw = sample_archive()
    source = tmp_path / "semantic-source"
    source.mkdir()
    (source / "trace.jsonl").write_bytes(raw)
    (source / "aica.json").write_text(dumps(archive), encoding="utf-8")
    path = tmp_path / "semantic.zip"
    bundle = build_bundle(source, path)
    url = project_url(client)
    headers = {"Content-Type": CONTENT_TYPE, "X-AiCA-Bundle-SHA256": bundle["bundle_sha256"],
               "Idempotency-Key": "sha256:" + bundle["bundle_sha256"]}
    response = client.post(url + "/imports", headers=headers, content=path.read_bytes())
    assert response.status_code == 201
    import_id = response.json()["import_id"]
    prepared = client.get(url + "/analysis-input", params={"import_id": import_id}).json()["input"]
    payload = {"request_id": new_id(), "import_id": import_id, "input_sha256": prepared["input_sha256"],
               "allow_external_processing": True}
    return url, payload, archive


@pytest.fixture
def model_calls():
    return []


@pytest.fixture
def model_provider(model_calls):
    def handler(request):
        model_calls.append(request)
        prepared = json.loads(json.loads(request.content)["messages"][1]["content"])["facts"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": dumps(sample_draft(prepared))}}]})

    return DeepSeekProvider(ProviderSettings(model="deepseek-flash", api_key="test-provider-key-not-real"),
                            httpx.MockTransport(handler))


@pytest.fixture
def semantic_app(tmp_path, model_provider):
    return create_app(tmp_path / "semantic-platform", {"alice": TOKEN, "bob": OTHER_TOKEN},
                      semantic_provider=model_provider)


@pytest.fixture
def semantic_client(semantic_app):
    with TestClient(semantic_app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        yield client


def test_analysis_is_grounded_persisted_and_idempotent_without_modifying_archive(semantic_client, tmp_path, model_calls):
    client = semantic_client
    url, payload, archive = setup_project(client, tmp_path)
    first = client.post(url + "/analyses", json=payload)
    assert first.status_code == 201, first.text
    analysis = first.json()["analysis"]
    assert analysis["state"] == "succeeded"
    result = analysis["result"]
    assert result["executable"] is False and result["requires_review"] is True
    assert result["analysis"]["dependencies"] and result["analysis"]["variables"]
    assert client.post(url + "/analyses", json=payload).status_code == 200
    assert len(model_calls) == 1
    assert client.get(url + "/analyses/" + analysis["id"]).json()["analysis"] == analysis
    assert len(client.get(url + "/analyses").json()["analyses"]) == 1
    assert client.get(url + "/archive").json()["archive"] == archive
    assert len(client.get(url + "/archives").json()["archives"]) == 1
    assert client.get("/api/semantic-provider").json()["model"] == "deepseek-flash"
    assert "test-provider-key-not-real" not in client.get("/api/semantic-provider").text


def test_consent_hash_and_ownership_are_checked_before_model_call(semantic_client, tmp_path, model_calls):
    client = semantic_client
    url, payload, _ = setup_project(client, tmp_path)
    assert client.post(url + "/analyses", json={**payload, "allow_external_processing": False}).status_code == 403
    assert client.post(url + "/analyses", json={**payload, "input_sha256": "f" * 64}).status_code == 409
    bob = {"Authorization": "Bearer " + OTHER_TOKEN}
    assert client.get(url + "/analysis-input", params={"import_id": payload["import_id"]}, headers=bob).status_code == 404
    assert client.post(url + "/analyses", json=payload, headers=bob).status_code == 404
    assert client.get(url + "/analyses", headers=bob).status_code == 404
    assert model_calls == []


def test_concurrent_retries_share_one_attempt(semantic_client, model_provider, model_calls, tmp_path):
    client = semantic_client
    url, payload, _ = setup_project(client, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = model_provider.transport.handler

    def blocking(request):
        entered.set()
        assert release.wait(10)
        return original(request)

    model_provider.transport = httpx.MockTransport(blocking)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, url + "/analyses", json=payload)
        try:
            assert entered.wait(10)
            duplicate = client.post(url + "/analyses", json=payload)
            assert duplicate.status_code == 202 and duplicate.json()["analysis"]["state"] == "running"
            assert client.post(url + "/analyses", json={**payload, "request_id": new_id()}).status_code == 409
        finally:
            release.set()
        assert pending.result().status_code == 201
    assert len(model_calls) == 1


def test_failed_provider_attempt_is_visible_and_never_automatically_retried(semantic_client, semantic_app, model_provider,
                                                                          model_calls, tmp_path):
    client = semantic_client
    url, payload, _ = setup_project(client, tmp_path)

    def rejected(request):
        model_calls.append(request)
        return httpx.Response(401, text="PRIVATE_PROVIDER_DIAGNOSTIC")

    model_provider.transport = httpx.MockTransport(rejected)
    response = client.post(url + "/analyses", json=payload)
    assert response.status_code == 502 and "PRIVATE_PROVIDER_DIAGNOSTIC" not in response.text
    retry = client.post(url + "/analyses", json=payload)
    assert retry.status_code == 200 and retry.json()["analysis"]["state"] == "failed"
    assert retry.json()["analysis"]["error_code"] == "provider_rejected" and len(model_calls) == 1
    assert len(client.get(url + "/archives").json()["archives"]) == 1
    analysis_id = retry.json()["analysis"]["id"]
    with semantic_app.state.repository.connection(write=True) as database:
        database.execute("UPDATE semantic_analyses SET state='running' WHERE id=?", (analysis_id,))


def test_restart_marks_unfinished_analysis_unknown_without_calling_model(tmp_path, model_provider, model_calls):
    directory = tmp_path / "restart-platform"
    tokens = {"alice": TOKEN}
    app = create_app(directory, tokens, semantic_provider=model_provider)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        url, payload, _ = setup_project(client, tmp_path)
        result = client.post(url + "/analyses", json=payload).json()["analysis"]
        with app.state.repository.connection(write=True) as database:
            database.execute("UPDATE semantic_analyses SET state='running',result=NULL WHERE id=?", (result["id"],))
    assert len(model_calls) == 1
    with TestClient(create_app(directory, tokens, semantic_provider=model_provider),
                    headers={"Authorization": "Bearer " + TOKEN}) as restarted:
        response = restarted.post(url + "/analyses", json=payload)
        assert response.status_code == 200 and response.json()["analysis"]["state"] == "unknown"
        assert response.json()["analysis"]["error_code"] == "service_restarted"
    assert len(model_calls) == 1


def test_request_id_cannot_be_reused_for_different_facts(semantic_client, tmp_path, model_calls):
    url, payload, _ = setup_project(semantic_client, tmp_path)
    assert semantic_client.post(url + "/analyses", json=payload).status_code == 201
    altered = copy.deepcopy(payload)
    altered["node_ids"] = []
    assert semantic_client.post(url + "/analyses", json=altered).status_code == 409
    assert len(model_calls) == 1
