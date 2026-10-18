import hashlib
import io

import httpx
from aica_gateway.app import create_app
from aica_mcp.transfer import CONTENT_TYPE, build_bundle
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.assembly import assemble_graph
from aica_runtime.graph.context import prepare_graph
from aica_runtime.graph.input_budget import fit_model_input
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import dumps, empty_archive, loads, new_id
from fastapi.testclient import TestClient
from graph_parser_fixture import claim, model_reply
from PIL import Image
from test_graph_api import wait_parse
from test_platform_api import login


def test_module_page_prompts_images_and_legacy_source_recovery(tmp_path):
    calls = []

    def model(request):
        prepared = loads(loads(request.content.decode())["messages"][1]["content"])["input"]
        calls.append(prepared)
        assert len(prepared["page_catalog"]) == 2
        assert any(isinstance(fact.get("content"), dict) and fact["content"].get("selection") == "partial_head_middle_tail_not_complete" for fact in prepared["facts"])
        evidence = prepared["page_catalog"][0]["evidence_id"]
        proposal = {"groups": [{"key": "pages", "kind": "generation", "label": "逐页画面",
                    "source_node_ids": [], "purpose": claim("保留逐页内容与统一风格。", [evidence]),
                    "requirements": [], "evidence_ids": [evidence],
                    "prompts": [{"kind": "consolidated", "text": "统一柔和水彩风格，保留每页记录的剧情。", "evidence_ids": [evidence]}],
                    "pages": [{"source_page_id": page["id"], "summary": page["label"] + "内容摘要。",
                               "prompts": [{"kind": "suggested", "text": "可适当加强主体与背景的层次。"}]}
                              for page in prepared["page_catalog"]]}], "edges": [], "variables": [], "unresolved": []}
        return httpx.Response(200, json=model_reply(proposal))

    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(buffer, format="PNG")
    png = buffer.getvalue()
    originals = ["第一页原始提示词：小猫走进花园，水彩。" + "保留原始细节。" * 3000, "第二页原始提示词：小猫发现蝴蝶，水彩。"]
    pages = {"pages": [{"page_number": i + 1, "title": f"第{i + 1}页", "prompt": prompt,
                        "description": prompt, "image": f"images/p{i + 1}.png"} for i, prompt in enumerate(originals)]}
    content = {"pages.json": dumps(pages).encode(), "images/p1.png": png, "images/p2.png": png}
    archive = empty_archive("Synthetic two-page fixture")
    archive["artifacts"] = []
    for index, (name, raw) in enumerate(content.items()):
        (source / name).write_bytes(raw)
        archive["artifacts"].append({"id": "asset-" + str(index), "uri": name,
            "mime_type": "application/json" if name.endswith("json") else "image/png", "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw), "availability": "available", "producer_node_id": None, "producer_run_id": None})
    (source / "aica.json").write_text(dumps(archive), encoding="utf-8")
    bundle = tmp_path / "bundle.zip"
    report = build_bundle(source, bundle)
    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="synthetic-provider-key"), httpx.MockTransport(model))
    app = create_app(tmp_path / "platform", tokens={}, graph_provider=provider)
    with TestClient(app) as client:
        owner = login(client, "page-owner")
        client.headers.update(owner)
        project = client.post("/api/projects", json={"title": "Synthetic two-page fixture"}).json()["project"]["id"]
        url = "/api/projects/" + project
        imported = client.post(url + "/imports", content=bundle.read_bytes(), headers={"Content-Type": CONTENT_TYPE,
            "Idempotency-Key": "sha256:" + report["bundle_sha256"], "X-AiCA-Bundle-SHA256": report["bundle_sha256"]})
        assert imported.status_code == 201
        import_id = imported.json()["import_id"]
        prepared = client.post(url + "/semantic-parse-input", json={"import_id": import_id}).json()["input"]
        accepted = client.post(url + "/semantic-parses", json={"import_id": import_id, "request_id": new_id(),
                            "input_sha256": prepared["input_sha256"], "allow_external_processing": True})
        job = wait_parse(client, url, accepted.json()["parse"]["id"])
        assert job["state"] == "succeeded", job
        publication = client.post(url + "/semantic-parses/" + job["id"] + "/repository").json()["publication"]
        repo = "/api/repos/" + publication["repo_id"]
        graph = client.get(repo + "/graph").json()["document"]
        node = graph["nodes"][0]
        assert node["prompts"][0]["kind"] == "consolidated"
        assert [page["prompts"][0]["text"] for page in node["pages"]] == originals
        assert all(page["prompts"][1]["kind"] == "suggested" for page in node["pages"])
        endpoint = repo + "/modules/" + node["id"] + "/materials?commit_id=" + publication["commit_id"]
        materials = client.get(endpoint)
        assert materials.status_code == 200, materials.text
        assert len(materials.json()["artifacts"]) == 2
        for page in materials.json()["pages"]:
            assert len(page["artifact_ids"]) == 1
            image = client.get(repo + "/artifacts/" + page["artifact_ids"][0], params={"commit_id": publication["commit_id"]})
            assert image.content == png and image.headers["content-type"] == "image/png"
        graph["nodes"][0]["prompts"].append({"kind": "edited", "text": "用户修改模块风格", "evidence_ids": node["evidence_ids"], "source_pointer": None})
        graph["nodes"][0]["pages"][0]["prompts"].append({"kind": "edited", "text": "用户修改第一页", "evidence_ids": node["pages"][0]["evidence_ids"], "source_pointer": None})
        saved = client.post(repo + "/commits", json={"branch": "main", "expected_head": publication["commit_id"], "document": graph, "message": "编辑模块及页内提示词"})
        assert saved.status_code == 201, saved.text
        new_commit = saved.json()["commit"]["id"]
        reopened = client.get(repo + "/graph").json()["document"]["nodes"][0]
        assert reopened["prompts"][-1]["text"] == "用户修改模块风格"
        assert reopened["pages"][0]["prompts"][-1]["kind"] == "edited"
        assert reopened["pages"][0]["prompts"][0]["text"] == originals[0]
        edited_materials = client.get(endpoint.replace(publication["commit_id"], new_commit)).json()
        assert len(edited_materials["artifacts"]) == 2
        assert edited_materials["prompts"][-1]["kind"] == "edited"
        assert client.get(repo + "/artifacts/asset-1", params={"commit_id": new_commit}).content == png
        assert client.post(repo + "/commits", json={"branch": "main", "expected_head": publication["commit_id"], "document": graph, "message": "stale"}).status_code == 409
        old_graph = client.get(repo + "/graph", params={"commit_id": publication["commit_id"]}).json()["document"]
        assert all(p["kind"] != "edited" for p in old_graph["nodes"][0]["prompts"])
        image_calls = []
        candidate = io.BytesIO()
        Image.new("RGB", (64, 64), "red").save(candidate, format="PNG")

        def edit_model(request):
            if request.method == "GET":
                return httpx.Response(200, content=candidate.getvalue(), headers={"content-type": "image/png"})
            if request.headers.get("content-type", "").startswith("multipart/form-data"):
                assert request.url.path.endswith("/images/edits")
                assert b'name="image"' in request.content and b"reference.png" in request.content
                assert b'name="seed"' not in request.content and b'name="steps"' not in request.content
                image_calls.append({"multipart": True})
                return httpx.Response(200, json={"data": [{"url": "https://images.test/result.png"}]})
            body = loads(request.content.decode())
            image_calls.append(body)
            assert body["model"] == "qwen-image-2.0"
            assert "steps" not in body["parameters"] and "size" not in body["parameters"]
            assert 0 <= body["parameters"]["seed"] < 2**31
            assert body["input"]["messages"][0]["content"][0]["image"].startswith("data:image/png;base64,")
            return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [{"image": "https://images.test/result.png"}]}}]}})

        app.state.image_edit_transport = httpx.MockTransport(edit_model)
        image_list = client.get(repo + "/images").json()
        assert len(image_list["images"]) == 2
        assert client.get(repo + "/images/asset-1", params={"commit_id": new_commit, "thumbnail": True}).headers["content-type"] == "image/jpeg"
        edit_request = {"branch": "main", "expected_head": new_commit, "request_id": new_id(), "original_artifact_id": "asset-1",
                        "instruction": "把左上角改成红色", "region": [0, 0, .5, .5],
                        "image_config": {"provider": "qwen", "api_base": "https://dashscope.aliyuncs.com/api/v1", "api_key": "synthetic-image-key", "model": "qwen-image"}}
        edited = client.post(repo + "/modules/" + node["id"] + "/image-edits", json=edit_request)
        assert edited.status_code == 200, edited.text
        image_result = edited.json()
        assert image_result["protected_pixels"] == 64 * 64 - 32 * 32
        updated_bytes = client.get(image_result["url"]).content
        exported = client.get(repo + '/archive', params={'commit_id': image_result['commit_id']},
                              headers={'Accept': 'application/vnd.aica.bundle+zip'})
        assert exported.status_code == 200, exported.text
        import zipfile

        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            document = loads(package.read('aica.json').decode())
            assert any(item['id'] == image_result['artifact_id'] for item in document['artifacts'])
            assert package.read('objects/' + hashlib.sha256(updated_bytes).hexdigest()) == updated_bytes
            assert package.read('objects/' + hashlib.sha256(png).hexdigest()) == png
        with Image.open(io.BytesIO(updated_bytes)) as actual:
            assert actual.getpixel((10, 10))[:3] == (255, 0, 0)
            assert actual.getpixel((50, 50))[:3] == (0, 0, 255)
        assert client.post(repo + "/modules/" + node["id"] + "/image-edits", json=edit_request).json() == image_result
        assert len(image_calls) == 1
        assert client.get(repo + "/images/asset-1", params={"commit_id": new_commit}).content == png
        assert len(client.get(repo + "/graph").json()["document"]["nodes"][0]["image_edits"]) == 1
        assert client.get(image_result["url"]).status_code == 200
        forked = client.post(repo + "/forks", json={"name": "edited-image-fork", "title": "继承编辑图"}).json()["repository"]
        fork_graph = client.get("/api/repos/" + forked["id"] + "/graph").json()
        inherited = client.get("/api/repos/" + forked["id"] + "/images/" + image_result["artifact_id"],
                               params={"commit_id": fork_graph["commit"]["id"]})
        assert inherited.status_code == 200 and inherited.content == updated_bytes
        openai_request = {**edit_request, "expected_head": image_result["commit_id"], "request_id": new_id(),
                          "image_config": {"provider": "openai", "api_base": "https://api.example/v1/images/generations",
                                           "api_key": "synthetic-image-key", "model": "gpt-image-1"}}
        openai_result = client.post(repo + "/modules/" + node["id"] + "/image-edits", json=openai_request)
        assert openai_result.status_code == 200, openai_result.text
        assert len(image_calls) == 2 and image_calls[-1]["multipart"]
        assert client.get(repo + "/images").json()["latest_result_id"] == openai_result.json()["artifact_id"]
        assert client.post(repo + "/branches", json={"name": "prompt-only"}).status_code == 201
        prompt_document = client.get(repo + "/graph").json()["document"]
        saved_prompt = client.post(repo + "/commits", json={"branch": "prompt-only", "expected_head": openai_result.json()["commit_id"],
            "document": prompt_document, "message": "仅保存提示词草稿"})
        assert saved_prompt.status_code == 201
        assert client.get(repo + "/images", params={"branch": "prompt-only"}).json()["latest_result_id"] == openai_result.json()["artifact_id"]
        plan = client.get(repo + "/modules/" + node["id"] + "/image-edit-plan").json()
        assert {target["original_id"] for target in plan["targets"]} == {"asset-1", "asset-2"}
        assert next(target for target in plan["targets"] if target["original_id"] == "asset-1")["artifact_id"] == openai_result.json()["artifact_id"]
        assert client.get(repo + "/artifacts/asset-1", params={"commit_id": openai_result.json()["commit_id"]}).content != png
        assert client.post(repo + "/branches", json={"name": "propagation"}).status_code == 201
        downstream_request = {**edit_request, "branch": "propagation", "expected_head": openai_result.json()["commit_id"],
            "request_id": new_id(), "original_artifact_id": "asset-2", "region": None,
            "reference_artifact_id": openai_result.json()["artifact_id"]}
        propagated = client.post(repo + "/modules/" + node["id"] + "/image-edits", json=downstream_request)
        assert propagated.status_code == 200, propagated.text
        content = image_calls[-1]["input"]["messages"][0]["content"]
        assert len([item for item in content if "image" in item]) == 2
        assert client.post(repo + "/modules/" + node["id"] + "/image-edits", json=downstream_request).json() == propagated.json()
        assert len(image_calls) == 3
        assert client.get(repo + "/images", params={"branch": "propagation"}).json()["images"][1]["id"] == propagated.json()["artifact_id"]
        app.state.image_edit_transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"code": "invalid_api_key"}}))
        rejected = client.post(repo + "/modules/" + node["id"] + "/image-edits", json={**openai_request,
            "expected_head": openai_result.json()["commit_id"], "request_id": new_id()})
        assert rejected.status_code == 502
        assert "provider_http_401.invalid_api_key" in rejected.text and "synthetic-image-key" not in rejected.text
        other_headers = login(client, "edit-other")
        assert client.get(image_result["url"], headers=other_headers).status_code == 404
        assert client.post(repo + "/modules/" + node["id"] + "/image-edits", json=edit_request, headers=other_headers).status_code == 404
        node.pop("prompts")
        node.pop("pages")
        legacy = dumps(graph)
        with app.state.repository.connection(write=True) as db:
            db.execute("UPDATE repo_commits SET document=?,document_hash=? WHERE id=?",
                       (legacy, hashlib.sha256(legacy.encode()).hexdigest(), publication["commit_id"]))
        recovered = client.get(endpoint).json()
        assert [page["prompts"][0]["text"] for page in recovered["pages"]] == originals
        assert len(calls) == 1
        stored_image = tmp_path / "platform" / "snapshots" / import_id / hashlib.sha256(png).hexdigest()
        stored_image.unlink()
        assert all(not image["available"] for image in client.get(endpoint).json()["artifacts"])
        other = login(client, "page-other")
        assert client.get(endpoint, headers=other).status_code == 404


def test_large_input_preserves_fact_references_and_bounds_text():
    archive = empty_archive("Large synthetic input")
    archive["artifacts"] = [{"id": f"asset-{i}", "uri": "images/" + "a" * 180 + f"{i}.png",
        "mime_type": "image/png", "sha256": "0" * 64, "size": 1, "availability": "available",
        "producer_node_id": None, "producer_run_id": None} for i in range(450)]
    context = prepare_graph(dumps(archive).encode(), "large-import")
    assert len(context.prepared["facts"]) == 450
    assert len(context.evidence) == 450
    prepared = {"facts": [{"id": "large-text", "value": {"prompt": "长提示词" * 200000}}], "coverage": {"issues": []}}
    fit_model_input(prepared)
    assert len(dumps(prepared).encode()) < 128 * 1024
    assert prepared["facts"][0]["id"] == "large-text"
    assert prepared["coverage"]["issues"][0]["code"] == "MODEL_INPUT_PREVIEW"


def test_75_pages_restore_without_repeating_page_text_in_model_output():
    material = dumps({"pages": [{"page_number": i + 1, "description": f"页面{i + 1}内容",
                                "prompt": f"页面{i + 1}原始提示词", "image": "image.png"} for i in range(75)]}).encode()
    archive = empty_archive("Synthetic 75-page input")
    archive["artifacts"] = [{"id": "pages", "uri": "pages.json", "mime_type": "application/json",
        "size": len(material), "sha256": hashlib.sha256(material).hexdigest(), "availability": "available",
        "producer_node_id": None, "producer_run_id": None},
        {"id": "image", "uri": "image.png", "mime_type": "image/png", "size": 1, "sha256": "0" * 64,
         "availability": "available", "producer_node_id": None, "producer_run_id": None}]
    context = prepare_graph(dumps(archive).encode(), "large-import", lambda identifier, limit: material)
    assert context.prepared["output_strategy"] == "modules_only_pages_restored_from_source"
    calls = []

    def respond(request):
        body = loads(request.content.decode())
        calls.append(body)
        assert body["max_tokens"] == 8192
        evidence = "e0"
        return httpx.Response(200, json=model_reply({"groups": [{"key": "pages", "kind": "generation", "label": "分页画面",
            "source_node_ids": [], "purpose": "按源内容组织分页画面。",
            "evidence_ids": [evidence], "prompt": "保留各页原始画面内容"}], "edges": []}))

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="synthetic-provider-key"), httpx.MockTransport(respond))
    snapshot = provider.snapshot(context.prepared)
    assert provider.configuration().max_tokens == 4096
    response = snapshot.propose(context.prepared)
    graph = assemble_graph(response["proposal"], context, "graph-large", "revision-large", "branch-large", response["model"])
    pages = graph["nodes"][0]["pages"]
    assert len(pages) == 75 and len(calls) == 1
    assert [page["prompts"][0]["text"] for page in pages] == [f"页面{i + 1}原始提示词" for i in range(75)]
    assert all(page["artifact_ids"] == ["image"] and page["summary"]["origin"] == "rule" for page in pages)


def test_module_propagation_uses_dependencies_and_source_producers():
    from aica_gateway.repo_media import effective_images, propagation_targets

    def node(identifier):
        return {"id": identifier, "label": identifier, "artifact_ids": [], "source_node_ids": ["raw-" + identifier]}

    document = {"nodes": [node(key) for key in ["character", "pages", "unrelated", "alternative"]], "edges": [
        {"kind": "control", "source": "character", "target": "pages"},
        {"kind": "control", "source": "character", "target": "pages"},
        {"kind": "semantic", "source": "character", "target": "pages", "relation": "refines"},
        {"kind": "semantic", "source": "character", "target": "alternative", "relation": "alternative_to"},
    ]}
    document["nodes"][0]["image_edits"] = [{"original_artifact_id": "character-image", "result_artifact_id": "edited"},
        {"original_artifact_id": "edited", "result_artifact_id": "edited-again"}]
    artifacts = [{"id": key + "-image", "mime_type": "image/png", "producer_node_id": "render-" + key}
                 for key in ["character", "pages", "unrelated", "alternative"]]
    edges = [{"source": "raw-" + key, "target": "render-" + key, "kind": "control"}
             for key in ["character", "pages", "unrelated", "alternative"]]
    plan = propagation_targets(document, "character", artifacts, edges)
    assert [target["original_id"] for target in plan] == ["character-image", "pages-image"]
    assert plan[0]["artifact_id"] == "edited-again"
    assert effective_images(document)["character-image"] == "edited-again"
    assert [target["node_id"] for target in propagation_targets(document, "pages", artifacts, edges)] == ["pages"]


def test_source_image_aliases_share_effective_result_without_merging_pages():
    from aica_gateway.repo_media import effective_images, image_aliases

    artifacts = [{"id": "raw", "sha256": "same", "producer_node_id": None},
                 {"id": "produced", "sha256": "same", "producer_node_id": "render"},
                 {"id": "page-1", "sha256": "two", "producer_node_id": "page-one"},
                 {"id": "page-2", "sha256": "two", "producer_node_id": "page-two"}]
    assert image_aliases(artifacts) == {"raw": "produced"}
    for original in ["raw", "produced"]:
        document = {"nodes": [{"image_edits": [{"original_artifact_id": original, "result_artifact_id": "edited"}]}]}
        assert effective_images(document, artifacts) == {"raw": "edited", "produced": "edited"}
