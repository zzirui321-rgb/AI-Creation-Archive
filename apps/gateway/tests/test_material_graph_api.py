import httpx
from aica_gateway.app import create_app
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import dumps, loads, new_id
from conftest import TOKEN, project_url
from fastapi.testclient import TestClient
from graph_parser_fixture import model_reply
from material_fixture import material_proposal, write_material_source
from test_graph_api import wait_parse


def test_artifact_only_bundle_to_graph_and_material_content_evidence(tmp_path):
    source = tmp_path / "materials"
    write_material_source(source)
    path = tmp_path / "materials.zip"
    bundle = build_bundle(source, path)
    calls = []

    def handle(request):
        calls.append(request)
        prepared = loads(loads(request.content.decode())["messages"][1]["content"])["input"]
        return httpx.Response(200, json=model_reply(material_proposal(prepared)))

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="synthetic-test-key"), httpx.MockTransport(handle))
    with TestClient(create_app(tmp_path / "platform", {"alice": TOKEN}, graph_provider=provider),
                    headers={"Authorization": "Bearer " + TOKEN}) as client:
        url = project_url(client)
        response = client.post(url + "/imports", content=path.read_bytes(), headers={"Content-Type": CONTENT_TYPE,
            "X-AiCA-Bundle-SHA256": bundle["bundle_sha256"], "Idempotency-Key": "sha256:" + bundle["bundle_sha256"]})
        assert response.status_code == 201
        import_id = response.json()["import_id"]
        prepared = client.post(url + "/semantic-parse-input", json={"import_id": import_id}).json()["input"]
        assert len(prepared["facts"]) == 4 and not prepared["source_node_ids"]
        job = client.post(url + "/semantic-parses", json={"request_id": new_id(), "import_id": import_id,
            "input_sha256": prepared["input_sha256"], "allow_external_processing": True}).json()["parse"]
        job = wait_parse(client, url, job["id"])
        assert job["state"] == "succeeded", job
        graph_url = url + "/semantic-graphs/" + job["graph_id"]
        graph = client.get(graph_url, params={"revision_id": job["revision_id"]}).json()["graph"]
        assert len(graph["nodes"]) == 4 and all(not node["run_ids"] for node in graph["nodes"])
        document = next(fact for fact in prepared["facts"] if fact["artifact_id"] == "story-json")
        evidence = client.get(graph_url + "/evidence/" + document["id"], params={"revision_id": job["revision_id"]}).json()
        assert evidence["role"] == "unknown" and evidence["content_state"] == "available"
        assert evidence["content"]["title"] == "Test lighthouse story"
        assert "synthetic-test-key" not in dumps(evidence)
        assert len(calls) == 1
