import hashlib

import httpx
import pytest
from aica_mcp.transfer import build_bundle
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.__main__ import load_context, parse, preview, source_path
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.provider import GraphProvider
from aica_schema.protocol import ProtocolError, dumps, loads
from graph_contract_design.cases import make_case
from graph_parser_fixture import model_reply, proposal_for


def write_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    case = make_case()
    (source / "events.jsonl").write_bytes(case["history"]["fixture-history"])
    for artifact in case["archive"]["artifacts"][1:]:
        path = source / artifact["uri"]
        path.parent.mkdir(exist_ok=True)
        content = b"synthetic-cli:" + artifact["id"].encode()
        path.write_bytes(content)
        artifact.update(sha256=hashlib.sha256(content).hexdigest(), size=len(content), availability="available")
    (source / "aica.json").write_text(dumps(case["archive"]), encoding="utf-8")
    return source


def provider_for(calls, failure=False, injection=False):
    def handle(request):
        calls.append(request)
        prepared = loads(loads(request.content.decode())["messages"][1]["content"])["input"]
        proposal = proposal_for(prepared)
        if injection:
            proposal["groups"][0]["label"] = '<script>alert("test")</script>'
        return httpx.Response(503 if failure else 200, json=model_reply(proposal))

    return GraphProvider(ProviderSettings(model="deepseek-flash", api_key="synthetic-test-key"), httpx.MockTransport(handle))


def test_local_preview_parse_and_html_report_keep_source_unchanged(tmp_path):
    source, output, calls = write_source(tmp_path), tmp_path / "run", []
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    result = preview(source, output)
    assert result["model_calls"] == 0 and not calls
    result = parse(output, True, provider_for(calls))
    assert result["state"] == "succeeded" and len(calls) == 1
    graph = loads((output / "graph.json").read_text(encoding="utf-8"))
    GraphDocument.model_validate(graph)
    assert len(graph["nodes"]) == 4
    html = (output / "report.html").read_text(encoding="utf-8")
    assert '<svg ' in html and '<details>' in html and 'run-raw-character-first' in html
    assert 'Content-Security-Policy' in html and '<script' not in html
    assert before == {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    with pytest.raises(ProtocolError, match="attempt"):
        parse(output, True, provider_for(calls))
    assert len(calls) == 1


def test_explicit_external_permission_is_required(tmp_path):
    output, calls = tmp_path / "run", []
    preview(write_source(tmp_path), output)
    with pytest.raises(ProtocolError) as caught:
        parse(output, False, provider_for(calls))
    assert caught.value.code == "not_authorized" and not calls and not (output / "attempt.json").exists()


@pytest.mark.parametrize("target", ["source", "review"])
def test_source_or_preview_changes_require_a_new_review(tmp_path, target):
    source, output, calls = write_source(tmp_path), tmp_path / "run", []
    preview(source, output)
    path = source / "aica.json" if target == "source" else output / "prepared-input.json"
    document = loads(path.read_text(encoding="utf-8"))
    if target == "source":
        document["metadata"]["title"] = "Changed source"
    else:
        document["facts"][0]["value"]["label"] = "Tampered review"
    path.write_text(dumps(document), encoding="utf-8")
    with pytest.raises(ProtocolError) as caught:
        parse(output, True, provider_for(calls))
    assert caught.value.code == "analysis_input_conflict" and not calls


def test_failed_attempt_is_saved_and_never_automatically_retried(tmp_path):
    output, calls = tmp_path / "run", []
    preview(write_source(tmp_path), output)
    with pytest.raises(ProtocolError):
        parse(output, True, provider_for(calls, failure=True))
    assert loads((output / "attempt.json").read_text())["state"] == "failed"
    with pytest.raises(ProtocolError):
        parse(output, True, provider_for(calls))
    assert len(calls) == 1 and not (output / "graph.json").exists()


def test_standard_bundle_project_and_dot_aica_resolve_identical_input(tmp_path):
    source = write_source(tmp_path)
    (source / ".aica").mkdir()
    path = tmp_path / "creation.zip"
    build_bundle(source, path)
    direct = load_context(source_path(source), "same-import")
    zipped = load_context(path, "same-import")
    assert direct.prepared == zipped.prepared
    assert source_path(source / ".aica") == source / "aica.json"


def test_report_escapes_untrusted_model_labels(tmp_path):
    output, calls = tmp_path / "run", []
    preview(write_source(tmp_path), output)
    parse(output, True, provider_for(calls, injection=True))
    html = (output / "report.html").read_text(encoding="utf-8")
    assert '<script' not in html and '&lt;script&gt;' in html


def test_existing_output_and_output_under_source_are_refused(tmp_path):
    source, output = write_source(tmp_path), tmp_path / "run"
    with pytest.raises(ProtocolError):
        preview(source, source / "results")
    preview(source, output)
    with pytest.raises(ProtocolError):
        preview(source, output)


def test_oversize_history_is_rejected_before_model_request(tmp_path):
    source = write_source(tmp_path)
    path = source / "aica.json"
    archive = loads(path.read_text(encoding="utf-8"))
    archive["artifacts"][0]["size"] = 2 * 1024 * 1024
    path.write_text(dumps(archive), encoding="utf-8")
    with pytest.raises(ProtocolError) as caught:
        preview(source, tmp_path / "run")
    assert caught.value.code == "analysis_limit"
