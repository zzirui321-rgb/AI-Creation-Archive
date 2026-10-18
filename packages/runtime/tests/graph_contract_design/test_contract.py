import copy
import json

import pytest
from aica_schema.protocol import ProtocolError, dumps
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from .assembly import assemble, prepare_context
from .cases import make_case, make_proposal
from .contract import GraphDocument, GraphEditRequest, LayoutUpdate, ModelProposal
from .export import MODELS, OUTPUT, artifacts
from .probe import evaluate
from .validation import ContractError, sha256, validate_document


@pytest.mark.parametrize("name", ["multiturn", "parallel", "partial"])
def test_positive_contract_and_a_source(name):
    case = make_case(name)
    before = copy.deepcopy(case)
    result = validate_document(case["graph"], case["archive_bytes"], case["history"])
    assert len(result.nodes) == 4
    assert len(result.nodes[1].source_node_ids) == 2
    assert len(result.nodes[1].run_ids) == 2
    assert case == before
    Draft202012Validator(GraphDocument.model_json_schema(), format_checker=FormatChecker()).validate(case["graph"])


@pytest.mark.parametrize("damage", [
    "attribution", "actor", "fake_evidence", "archive_hash", "event_hash", "event_id", "event_line",
    "pointer", "snapshot", "duplicate_owner", "wrong_run", "wrong_artifact", "invented_value",
    "wrong_type", "fake_ports", "fake_dependency", "missing_dependency", "missing_coverage",
    "hidden_gap", "self_approval", "unsafe_accept", "unknown_edge", "duplicate_id", "unsupported_append",
    "nonliteral_variable", "hidden_assignment", "execution_flag", "revision_cycle",
])
def test_negative_contract(damage):
    case = make_case("partial" if damage == "hidden_gap" else "multiturn")
    graph = copy.deepcopy(case["graph"])
    node, variable, edge = graph["nodes"][1], graph["variables"][0], graph["edges"][0]
    if damage == "attribution":
        node["purpose"].update(attributed_to="user", actor_id=None, evidence_ids=["e-message-2"])
    elif damage == "actor":
        node["purpose"].update(attributed_to="agent", actor_id="invented-agent", evidence_ids=["e-message-2"])
    elif damage == "fake_evidence":
        node["purpose"]["evidence_ids"] = ["does-not-exist"]
    elif damage == "archive_hash":
        graph["source"]["archive_sha256"] = "0" * 64
    elif damage == "event_hash":
        graph["evidence"][-1]["source_sha256"] = "0" * 64
    elif damage == "event_id":
        graph["evidence"][-1]["event_id"] = "invented-event"
    elif damage == "event_line":
        graph["evidence"][-1]["line"] = 99
    elif damage == "pointer":
        graph["evidence"][0]["pointer"] = "/graph/nodes/99"
    elif damage == "snapshot":
        graph["source"]["through_seq"] = 3
    elif damage == "duplicate_owner":
        graph["nodes"][2]["source_node_ids"].append("raw-character-revised")
    elif damage == "wrong_run":
        node["run_ids"].append("run-raw-panel")
    elif damage == "wrong_artifact":
        node["artifact_ids"].append("artifact-raw-panel")
    elif damage == "invented_value":
        variable["current_value"] = "invented prompt"
    elif damage == "wrong_type":
        variable["value_type"] = "integer"
    elif damage == "fake_ports":
        edge["binding"]["target_input"] = "prompt"
    elif damage == "fake_dependency":
        edge["archive_edge_id"] = "not-in-archive"
    elif damage == "missing_dependency":
        graph["edges"] = []
    elif damage == "missing_coverage":
        graph["coverage"]["selected_source_node_ids"].pop()
    elif damage == "hidden_gap":
        graph["coverage"]["issues"] = []
    elif damage == "self_approval":
        node["purpose"]["origin"] = "llm"
    elif damage == "unsafe_accept":
        graph["state"] = "accepted"
    elif damage == "unknown_edge":
        edge["source"] = "unknown-step"
    elif damage == "duplicate_id":
        graph["nodes"][2]["id"] = node["id"]
    elif damage == "unsupported_append":
        variable.update(value_type="integer", current_value=42, allowed_modes=["keep", "append"])
        variable["locator"]["value_pointer"] = "/seed"
    elif damage == "nonliteral_variable":
        variable.update(step_id="step-panel", current_value="image")
        variable["locator"].update(source_node_id="raw-panel", evidence_id="e-node-3",
                                   pointer="/execution/inputs/reference_image/output", decode="identity", value_pointer="")
    elif damage == "hidden_assignment":
        graph["coverage"]["unassigned_source_node_ids"] = ["raw-character-first"]
    elif damage == "revision_cycle":
        graph["parent_revision_id"] = graph["revision_id"]
    else:
        graph["executable"] = True
    with pytest.raises((ContractError, ProtocolError, ValidationError)):
        validate_document(graph, case["archive_bytes"], case["history"])


def test_semantic_cycle_is_not_an_execution_cycle():
    case = make_case()
    graph = copy.deepcopy(case["graph"])
    for source, target in (("step-character", "step-panel"), ("step-panel", "step-character")):
        graph["edges"].append({"kind": "semantic", "id": "semantic-" + source, "source": source, "target": target,
                               "relation": "inspired_by", "explanation": copy.deepcopy(graph["nodes"][1]["purpose"])})
    validate_document(graph, case["archive_bytes"], case["history"])


def test_nonconvex_step_grouping_cannot_create_execution_cycle():
    case = make_case()
    case["archive"]["graph"]["edges"].append({"id": "raw-panel-audio", "source": "raw-panel", "target": "raw-audio", "kind": "control"})
    case["archive_bytes"] = (dumps(case["archive"], pretty=True) + "\n").encode()
    graph = case["graph"]
    graph["source"]["archive_sha256"] = sha256(case["archive_bytes"])
    for evidence in graph["evidence"]:
        if evidence["kind"] == "archive":
            evidence["source_sha256"] = sha256(case["archive_bytes"])
    first, last = graph["nodes"][1], graph["nodes"].pop()
    for field in ("source_node_ids", "run_ids", "artifact_ids", "evidence_ids"):
        first[field] += last[field]
    graph["edges"].append({"id": "cycle-control", "kind": "control", "source": "step-panel", "target": "step-character",
                           "state": "verified", "archive_edge_id": "raw-panel-audio", "explanation": first["purpose"]})
    with pytest.raises(ProtocolError):
        validate_document(graph, case["archive_bytes"], case["history"])


def test_rule_assembly_preserves_dependency_and_hydrates_values():
    case = make_case()
    proposal = make_proposal(case)
    proposal["edges"] = []
    graph = assemble(proposal, case, "offline-fixture")
    assert graph["edges"][0]["state"] == "verified"
    assert graph["variables"][0]["current_value"] == case["graph"]["variables"][0]["current_value"]
    second = copy.deepcopy(proposal)
    second["groups"][1]["label"] = "另一标题"
    assert [node["id"] for node in assemble(second, case)["nodes"]] == [node["id"] for node in graph["nodes"]]


def test_unknown_model_dependency_stays_candidate():
    case = make_case()
    proposal = make_proposal(case)
    proposal["edges"][0].update(source_key="step-audio", target_key="step-panel")
    graph = assemble(proposal, case)
    assert [edge["state"] for edge in graph["edges"]] == ["verified", "candidate"]
    assert graph["edges"][1]["binding"] is None


def test_known_role_context_retains_event_identity():
    context = prepare_context(make_case())
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert facts["e-message-1"]["role"] == "user"
    assert facts["e-message-2"]["role"] == "agent"
    assert facts["e-message-2"]["event"]["event_id"] == "event-2"


def test_event_timestamp_cannot_support_user_attribution():
    case = make_case()
    case["graph"]["evidence"][5]["pointer"] = "/timestamp"
    with pytest.raises(ContractError, match="source role"):
        validate_document(case["graph"], case["archive_bytes"], case["history"])


def test_subgraph_has_to_expose_external_dependency():
    case = make_case()
    graph = case["graph"]
    graph["nodes"] = graph["nodes"][:2]
    graph["edges"] = []
    graph["coverage"]["selected_source_node_ids"] = ["raw-plan", "raw-character-first", "raw-character-revised"]
    with pytest.raises(ContractError, match="boundary"):
        validate_document(graph, case["archive_bytes"], case["history"])
    graph["coverage"]["issues"].append({"id": "external", "code": "EXTERNAL_DEPENDENCY", "severity": "warning",
                                         "message": "选中角色图步骤的下游分镜不在解析范围内。", "node_ids": [], "evidence_ids": []})
    validate_document(graph, case["archive_bytes"], case["history"])


def test_historical_execution_cannot_be_silently_dropped_or_rebound():
    case = make_case()
    case["archive"]["artifacts"].append({"id": "old-definition", "uri": "history/old.json", "mime_type": "application/json",
                                         "sha256": "1" * 64, "availability": "missing", "producer_node_id": None, "producer_run_id": None})
    case["archive"]["history"].append({"kind": "revision", "artifact_id": "old-definition", "revision_id": "old-revision"})
    historical = copy.deepcopy(case["archive"]["runs"][0])
    historical.update(id="old-run", revision_id="old-revision", node_id="removed-old-node")
    case["archive"]["runs"].append(historical)
    case["archive_bytes"] = (dumps(case["archive"], pretty=True) + "\n").encode()
    case["graph"]["source"]["archive_sha256"] = sha256(case["archive_bytes"])
    for item in case["graph"]["evidence"]:
        if item["kind"] == "archive":
            item["source_sha256"] = sha256(case["archive_bytes"])
    with pytest.raises(ContractError, match="Historical execution"):
        validate_document(case["graph"], case["archive_bytes"], case["history"])
    case["graph"]["coverage"]["issues"].append({"id": "old-scope", "code": "HISTORICAL_RUN_SCOPE", "severity": "warning",
                                               "message": "旧定义已移除该节点，历史 Run 保留但不绑定到当前节点。", "node_ids": [], "evidence_ids": []})
    graph = assemble(make_proposal(case), case)
    assert all("old-run" not in node["run_ids"] for node in graph["nodes"])
    assert any(item["code"] == "HISTORICAL_RUN_SCOPE" for item in graph["coverage"]["issues"])


@pytest.mark.parametrize("damage", ["self_approval", "human_impersonation", "mixed_edge_kinds"])
def test_model_proposal_cannot_impersonate_review_or_mix_edge_shapes(damage):
    proposal = make_proposal(make_case())
    if damage == "self_approval":
        proposal["groups"][0]["purpose"]["review"] = "accepted"
    elif damage == "human_impersonation":
        proposal["groups"][0]["purpose"]["origin"] = "human"
    else:
        proposal["edges"][0]["relation"] = "continues"
    with pytest.raises(ValidationError):
        ModelProposal.model_validate(proposal)


@pytest.mark.parametrize("model", MODELS)
def test_generated_json_schema_is_valid(model):
    Draft202012Validator.check_schema(model.model_json_schema())


def test_generated_artifacts_are_current():
    for relative, expected in artifacts().items():
        assert (OUTPUT / relative).read_text(encoding="utf-8") == expected, relative


def test_checked_in_source_bytes_match_their_declared_hashes():
    for name in ("multiturn", "parallel", "partial"):
        graph = json.loads((OUTPUT / f"examples/{name}.graph.json").read_text(encoding="utf-8"))
        archive_bytes = (OUTPUT / f"examples/{name}.aica.json").read_bytes()
        history = {"fixture-history": (OUTPUT / f"examples/{name}.events.jsonl").read_bytes()}
        validate_document(graph, archive_bytes, history)


def test_c_edit_and_layout_payloads_have_separate_versions():
    edit = json.loads((OUTPUT / "examples/edit-request.json").read_text(encoding="utf-8"))
    layout = json.loads((OUTPUT / "examples/layout-request.json").read_text(encoding="utf-8"))
    assert GraphEditRequest.model_validate(edit).expected_revision_id == layout["graph_revision_id"]
    assert LayoutUpdate.model_validate(layout).expected_layout_version == 0
    assert ModelProposal.model_validate(make_proposal(make_case())).groups


def test_saved_model_probe_is_replayable_without_model_calls():
    recorded = json.loads((OUTPUT / "evidence/hybrid-v2.json").read_text(encoding="utf-8"))
    metrics, graph = evaluate(recorded["proposal"], make_case("parallel"), recorded["model"])
    assert metrics == recorded["metrics"]
    normalized = json.loads((OUTPUT / "evidence/hybrid-v2.normalized.graph.json").read_text(encoding="utf-8"))
    assert normalized == graph
    assert graph["parse"]["model"] == recorded["model"]
