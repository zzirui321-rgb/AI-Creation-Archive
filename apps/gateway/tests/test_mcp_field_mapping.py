import copy
import hashlib
import json

from aica_gateway.process_view import archive_process_view
from aica_mcp.transfer import build_json
from aica_schema.protocol import dumps, empty_archive
from test_platform_api import login
from test_platform_api import platform_client as platform_client


def portable_archive(tmp_path):
    source = empty_archive("MCP field mapping regression")
    provenance = {"mode": "recorded", "evidence": []}
    source["graph"]["nodes"] = [
        {"id": identifier, "kind": "tool", "label": identifier, "branch_id": "main", "provenance": provenance,
         "execution": {"kind": "model", "tool": "image-tool", "version": None, "model": None,
                       "inputs": {"prompt": {"kind": "literal", "value": "definition prompt"}} if identifier == "image" else {},
                       "params": {}, "outputs": []}} for identifier in ("image", "unknown")]
    source["harnesses"] = [{"id": "harness", "name": "Synthetic host", "version": None, "config": {},
                            "state": {}, "availability": {"model": "recorded"}}]
    source["agents"] = [{"id": "agent", "session_id": "session", "parent_agent_id": None, "harness_id": "harness",
                         "client": "synthetic", "model": {"id": "agent-text-model", "reasoning_effort": "high"}}]
    source["sessions"] = [{"id": "session", "state": "active", "created_at": "2026-10-18T00:00:00Z",
                           "consent": {"record": True, "include_children": False, "decided_at": "2026-10-18T00:00:00Z"}}]
    source["runs"] = [
        {"id": "run-" + identifier, "trace_id": "trace", "node_id": identifier, "revision_id": source["graph"]["revision_id"],
         "session_id": "session", "agent_id": "agent", "harness_id": "harness", "state": "succeeded",
         "started_at": "2026-10-18T00:02:00Z", "ended_at": "2026-10-18T00:03:00Z", "inputs": {"prompt": "实际绘图提示词：红发女孩", "model": "qwen-image-2.0"} if identifier == "image" else {},
         "outputs": {"text": "Observed output"}, "artifact_ids": [], "provenance": provenance} for identifier in ("image", "unknown")]
    old = copy.deepcopy(source["runs"][0])
    old.update(id="older-run", started_at="2026-10-18T00:01:00Z", inputs={"prompt": "older actual prompt", "model": "older-model"})
    source["runs"].append(old)
    event = {"event_id": "request-event", "source_id": "synthetic-host", "source_seq": 1, "session_id": "session", "agent_id": "agent",
             "type": "message.recorded", "timestamp": "2026-10-18T00:00:00Z", "causal_parents": [],
             "payload": {"role": "user", "content": "用户原始要求：创作一个女孩的故事"}}
    content = (dumps({"stream_id": "synthetic-stream", "commit_seq": 1, "event": event}) + "\n").encode()
    (tmp_path / "history.jsonl").write_bytes(content)
    source["artifacts"] = [{"id": "events", "uri": "history.jsonl", "mime_type": "application/x-ndjson", "sha256": hashlib.sha256(content).hexdigest(),
                            "size": len(content), "availability": "available", "producer_node_id": None, "producer_run_id": None}]
    source["history"] = [{"kind": "events", "artifact_id": "events", "through_seq": 1}]
    (tmp_path / "aica.json").write_text(dumps(source), encoding="utf-8")
    output = tmp_path / "portable.json"
    build_json(tmp_path, output)
    return source, output.read_bytes()


def test_import_maps_run_agent_and_history_and_refreshes_legacy_cache(platform_client, tmp_path):
    client = platform_client
    client.headers.update(login(client, "mapping-owner"))
    _, content = portable_archive(tmp_path)
    response = client.post("/api/creation-parser/imports", content=content)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    url = f"/api/creation-parser/jobs/{identifier}/result"
    result = client.get(url).json()
    view = result["process_graph"]
    image, unknown = view["nodes"]
    assert image["prompt"] == "实际绘图提示词：红发女孩"
    assert image["model"] == "qwen-image-2.0"
    assert image["agent_model"] == unknown["agent_model"] == "agent-text-model"
    assert unknown["model"] is None
    assert unknown["field_status"]["model"]["state"] == "not_recorded"
    assert image["field_status"]["prompt"]["source_pointer"] == "/runs/0/inputs/prompt"
    assert [run["id"] for run in image["runs"]] == ["older-run", "run-image"]
    assert image["harness"]["name"] == "Synthetic host"
    assert view["source_context"]["user_messages"][0]["text"].startswith("用户原始要求")
    assert "private-synthetic-key" not in json.dumps(view)
    repo, commit = result["repository"]["id"], result["repository"]["commit_id"]
    graph = client.get(f"/api/repos/{repo}/graph").json()
    assert graph["process_graph"] == view
    assert client.get(f"/api/repos/{repo}/archive", params={"commit_id": commit}).content == content
    legacy = copy.deepcopy(result)
    legacy["process_graph"] = {"nodes": [{"id": "image", "prompt": "", "model": None}]}
    with client.app.state.repository.connection(write=True) as db:
        db.execute("UPDATE frontend_jobs SET result=? WHERE id=?", (json.dumps(legacy), identifier))
    assert client.get(url).json()["process_graph"] == view
    assert client.post("/api/creation-parser/imports", content=content).json()["id"] == identifier
    assert len(client.get("/api/repos?scope=mine").json()["repositories"]) == 1
    assert len(client.get(f"/api/repos/{repo}/commits").json()["commits"]) == 1
    saved = {"format": "aica-ui-module-record", "steps": [{"id": "image", "prompt": "", "model": "", "inputs": {}}],
             "edges": [], "ui": {"basePrompts": {"image": ""}, "baseModels": {"image": ""}}}
    response = client.post(f"/api/repos/{repo}/commits", json={"expected_head": commit, "document": saved, "message": "Old UI save"})
    assert response.status_code == 201, response.text
    restored = client.get(f"/api/repos/{repo}/graph").json()["process_graph"]
    assert restored["nodes"][0]["prompt"] == image["prompt"]
    assert restored["source_context"]["user_messages"] == view["source_context"]["user_messages"]
    saved["steps"][0].update(prompt="My edited instruction", model="my-selected-model")
    response = client.post(f"/api/repos/{repo}/commits", json={"expected_head": response.json()["commit"]["id"], "document": saved, "message": "Edit prompt"})
    assert response.status_code == 201, response.text
    restored = client.get(f"/api/repos/{repo}/graph").json()["process_graph"]["nodes"][0]
    assert restored["prompt"] == "My edited instruction"
    assert restored["model"] == "my-selected-model"
    saved["steps"][0].update(prompt="", fieldStatus=restored["field_status"])
    response = client.post(f"/api/repos/{repo}/commits", json={"expected_head": response.json()["commit"]["id"], "document": saved, "message": "Clear prompt"})
    assert response.status_code == 201, response.text
    assert client.get(f"/api/repos/{repo}/graph").json()["process_graph"]["nodes"][0]["prompt"] == ""
    client.headers.update(login(client, "mapping-other"))
    assert client.get(url).status_code == 404
    assert client.get(f"/api/repos/{repo}/graph").status_code == 404


def test_mcp_upload_receipt_unchanged_and_existing_repo_gets_mapping(platform_client, tmp_path):
    from aica_schema.transfer import require_transfer

    client = platform_client
    client.headers.update(login(client, "mapping-upload"))
    _, content = portable_archive(tmp_path)
    project = client.post("/api/projects", json={"title": "MCP direct upload"}).json()["project"]["id"]
    digest = hashlib.sha256(content).hexdigest()
    response = client.post(f"/api/projects/{project}/imports", content=content,
                           headers={"Content-Type": "application/json", "Idempotency-Key": "sha256:" + digest,
                                    "X-AiCA-Bundle-SHA256": digest})
    assert response.status_code == 201, response.text
    require_transfer(response.json(), "receipt")
    repo = client.get(f"/api/projects/{project}/repository").json()["repository"]["id"]
    assert client.get(f"/api/repos/{repo}/graph").json()["process_graph"]["nodes"][0]["model"] == "qwen-image-2.0"


def test_missing_history_is_explicit_and_does_not_erase_recorded_fields(tmp_path):
    source, _ = portable_archive(tmp_path)
    result = archive_process_view(source)
    assert result["nodes"][0]["model"] == "qwen-image-2.0"
    assert result["source_context"]["user_messages"] == []
    assert result["source_context"]["history_issues"][0]["state"] == "not_expanded"
    (tmp_path / "history.jsonl").write_text("corrupt", encoding="utf-8")
    result = archive_process_view(source, lambda _: tmp_path / "history.jsonl")
    assert "哈希" in result["warnings"][0]
    assert not result["source_context"]["user_messages"]


def test_prior_revision_runs_remain_visible_and_variables_resolve():
    source = empty_archive("Historical run")
    source["variables"] = {"subject": {"value": "cat"}}
    source["graph"]["nodes"] = [{"id": "image", "execution": {"inputs": {"prompt": {"kind": "template", "template": "{{subject}} on moon"}},
                                                                                "model": {"id": "definition-model"}}}]
    source["runs"] = [{"id": "run", "node_id": "image", "revision_id": "prior-revision", "started_at": "2026-10-18T00:00:00Z",
                       "inputs": {"prompt": "recorded prompt"}}]
    result = archive_process_view(source)
    assert result["nodes"][0]["prompt"] == "recorded prompt"
    assert result["evidence"][0]["run_revision_id"] == "prior-revision"
    source["runs"] = []
    result = archive_process_view(source)
    assert result["nodes"][0]["prompt"] == "cat on moon"
    assert result["nodes"][0]["model"] == "definition-model"
