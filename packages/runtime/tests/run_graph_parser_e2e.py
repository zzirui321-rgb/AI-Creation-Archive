import argparse
import hashlib
import json
import secrets
from pathlib import Path
from time import monotonic, sleep

import httpx
from aica_gateway.app import create_app
from aica_mcp.files import private_directory
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import dumps, new_id, require, utc_now
from fastapi.testclient import TestClient
from graph_contract_design.cases import make_case


class CountingTransport(httpx.BaseTransport):
    def __init__(self):
        self.delegate = httpx.HTTPTransport(retries=0)
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        require(self.calls == 1, "Probe allows exactly one external model request")
        return self.delegate.handle_request(request)

    def close(self):
        self.delegate.close()


def run(root):
    root = root.resolve()
    checkout = Path(__file__).resolve().parents[3]
    require(not root.is_relative_to(checkout) and not root.exists(), "Use a new output directory outside checkout")
    private_directory(root)
    source = root / "synthetic-source"
    source.mkdir()
    case = make_case("parallel")
    archive = case["archive"]
    (source / "events.jsonl").write_bytes(case["history"]["fixture-history"])
    for artifact in archive["artifacts"][1:]:
        path = source / artifact["uri"]
        path.parent.mkdir(exist_ok=True)
        content = b"synthetic-test-artifact:" + artifact["id"].encode()
        path.write_bytes(content)
        artifact.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content), availability="available")
    raw = dumps(archive).encode()
    (source / "aica.json").write_bytes(raw)
    bundle_path = root / "transfer.zip"
    bundle = build_bundle(source, bundle_path)
    transport = CountingTransport()
    provider = GraphProvider(transport=transport)
    require(provider.describe()["configured"], "Existing DeepSeek configuration is required")
    tokens = {"graph-live-probe": secrets.token_urlsafe(32)}
    headers = {"Authorization": "Bearer " + tokens["graph-live-probe"]}
    directory = root / "platform"
    report = {"tested_at": utc_now(), "mode": "real_model_on_synthetic_data", "api_transport": "in_process_testclient",
              "automatic_retries": 0, "creation_tools_executed": False, "public_deployment": False}
    with TestClient(create_app(directory, tokens, graph_provider=provider), headers=headers) as client:
        project = client.post("/api/projects", json={"title": "Synthetic Graph parser integration"})
        url = "/api/projects/" + project.json()["project"]["id"]
        response = client.post(url + "/imports", content=bundle_path.read_bytes(), headers={
            "Content-Type": CONTENT_TYPE, "X-AiCA-Bundle-SHA256": bundle["bundle_sha256"],
            "Idempotency-Key": "sha256:" + bundle["bundle_sha256"]})
        require(response.status_code == 201, "Synthetic bundle import failed")
        import_id = response.json()["import_id"]
        prepared = client.post(url + "/semantic-parse-input", json={"import_id": import_id}).json()["input"]
        payload = {"request_id": new_id(), "import_id": import_id, "input_sha256": prepared["input_sha256"],
                   "allow_external_processing": True}
        submitted = client.post(url + "/semantic-parses", json=payload)
        require(submitted.status_code == 202, "Graph submission failed")
        job = submitted.json()["parse"]
        deadline = monotonic() + 150
        while job["state"] in {"queued", "running"} and monotonic() < deadline:
            sleep(0.1)
            job = client.get(url + "/semantic-parses/" + job["id"]).json()["parse"]
        report.update(job=job, input_sha256=prepared["input_sha256"], fact_count=len(prepared["facts"]))
        if job["state"] == "succeeded":
            graph_url = url + "/semantic-graphs/" + job["graph_id"]
            graph = client.get(graph_url, params={"revision_id": job["revision_id"]}).json()["graph"]
            GraphDocument.model_validate(graph)
            report.update(graph=graph, step_count=len(graph["nodes"]), variable_count=len(graph["variables"]),
                          verified_dependencies=sum(edge["kind"] != "semantic" and edge["state"] == "verified" for edge in graph["edges"]))
        report["raw_archive_unchanged"] = client.app.state.repository.archive_bytes(
            "graph-live-probe", url.split("/")[-1], import_id) == raw
    with TestClient(create_app(directory, tokens, graph_provider=provider), headers=headers) as client:
        prior = client.post(url + "/semantic-parses", json=payload)
        report["retry_after_restart_same_job"] = prior.status_code == 200 and prior.json()["parse"]["id"] == job["id"]
        if job["state"] == "succeeded":
            report["graph_survives_restart"] = client.get(graph_url, params={"revision_id": job["revision_id"]}).json()["graph"] == graph
    report["external_model_calls"] = transport.calls
    (root / "result.json").write_text(dumps(report, pretty=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in {"graph", "job"}}))
    print(json.dumps({"state": job["state"], "usage": job["usage"], "error_code": job["error_code"]}))
    require(job["state"] == "succeeded", "Live parse did not succeed; inspect saved status, no automatic retry")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One real Graph parser request using synthetic A data only")
    parser.add_argument("--confirm-external", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    run(parser.parse_args().root)
