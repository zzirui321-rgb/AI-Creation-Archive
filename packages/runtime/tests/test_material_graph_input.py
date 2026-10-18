import copy

import pytest
from aica_runtime.graph.__main__ import load_context, preview
from aica_runtime.graph.assembly import assemble_graph
from aica_runtime.graph.validation import ContractError, validate_document
from aica_schema.protocol import ProtocolError, dumps, empty_archive
from material_fixture import material_proposal, write_material_source


def setup(tmp_path):
    root = tmp_path / "source"
    archive = write_material_source(root)
    return root, archive, load_context(root / "aica.json", "material-import")


def assemble(context, proposal=None):
    return assemble_graph(proposal if proposal is not None else material_proposal(context.prepared), context,
                          "graph-material", "revision-material", "branch-material", "mock-model")


def test_empty_graph_with_registered_materials_produces_real_nonempty_context_and_input_report(tmp_path):
    root, _, context = setup(tmp_path)
    assert context.prepared["source_node_ids"] == []
    facts = context.prepared["facts"]
    assert len(facts) == 4 and sum(f.get("content_state") == "available" for f in facts) == 3
    assert all(f["role"] == "unknown" for f in facts)
    assert next(f["content"] for f in facts if f["artifact_id"] == "prompts-json")[0]["asset"] == "cover"
    report = preview(root, tmp_path / "preview")
    assert report["facts"] == 4 and report["document_contents"] == 3 and report["source_nodes"] == 0
    assert "尚未调用 DeepSeek" in (tmp_path / "preview/input-report.html").read_text(encoding="utf-8")


def test_artifact_backed_steps_keep_schema_and_disclose_missing_execution(tmp_path):
    _, archive, context = setup(tmp_path)
    graph = assemble(context)
    assert len(graph["nodes"]) == 4
    assert {key for node in graph["nodes"] for key in node["artifact_ids"]} == {a["id"] for a in archive["artifacts"]}
    assert all(not node["source_node_ids"] and not node["run_ids"] for node in graph["nodes"])
    assert graph["variables"] == [] and graph["edges"] == [] and not graph["executable"]
    assert any(i["code"] == "ARTIFACT_ONLY_SOURCE" for i in graph["coverage"]["issues"])
    validate_document(graph, context.archive_bytes, context.history)


@pytest.mark.parametrize("damage", ["user_role", "fake_node", "empty_graph", "unanchored_group"])
def test_materials_cannot_impersonate_messages_calls_or_silent_empty_output(tmp_path, damage):
    _, _, context = setup(tmp_path)
    proposal = material_proposal(context.prepared)
    if damage == "user_role":
        proposal["groups"][0]["purpose"]["attributed_to"] = "user"
    elif damage == "fake_node":
        proposal["groups"][0]["source_node_ids"] = ["story-json"]
    elif damage == "empty_graph":
        proposal["groups"] = []
    else:
        proposal["groups"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ProtocolError):
        assemble(context, proposal)


def test_material_association_requires_artifact_evidence_and_scope_warning(tmp_path):
    _, _, context = setup(tmp_path)
    graph = assemble(context)
    altered = copy.deepcopy(graph)
    altered["nodes"][0]["artifact_ids"] = ["cover-png"]
    with pytest.raises(ContractError):
        validate_document(altered, context.archive_bytes, context.history)
    graph["coverage"]["issues"] = []
    with pytest.raises(ContractError, match="provenance"):
        validate_document(graph, context.archive_bytes, context.history)


def test_distinct_material_steps_can_share_one_script_or_prompt_artifact(tmp_path):
    _, _, context = setup(tmp_path)
    proposal = material_proposal(context.prepared)
    second = copy.deepcopy(proposal["groups"][0])
    second.update(key="another-step-from-same-script", label="同一剧本支持的另一步骤")
    proposal["groups"].append(second)
    graph = assemble(context, proposal)
    assert len(graph["nodes"]) == 5 and len({node["id"] for node in graph["nodes"]}) == 5
    assert graph["nodes"][0]["artifact_ids"] == graph["nodes"][-1]["artifact_ids"]


def test_missing_material_is_visible_and_unassociated_material_is_not_hidden(tmp_path):
    root, _, _ = setup(tmp_path)
    (root / "story.json").unlink()
    context = load_context(root / "aica.json", "material-import")
    assert any(i["code"] == "MATERIAL_CONTENT_UNAVAILABLE" for i in context.prepared["coverage"]["issues"])
    proposal = material_proposal(context.prepared)
    proposal["groups"] = proposal["groups"][:1]
    graph = assemble(context, proposal)
    assert any(i["code"] == "UNASSIGNED_ARTIFACT" for i in graph["coverage"]["issues"])


def test_genuinely_empty_archive_cannot_preview_as_a_success(tmp_path):
    folder = tmp_path / "empty-source"
    folder.mkdir()
    source = folder / "empty.json"
    source.write_text(dumps(empty_archive("Empty archive")), encoding="utf-8")
    with pytest.raises(ProtocolError) as caught:
        preview(source, tmp_path / "output")
    assert caught.value.code == "analysis_empty" and not (tmp_path / "output").exists()
