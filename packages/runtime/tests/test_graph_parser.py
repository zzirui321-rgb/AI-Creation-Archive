import copy
from pathlib import Path

import httpx
import pytest
from aica_runtime.deepseek import ProviderSettings
from aica_runtime.graph.assembly import assemble_graph
from aica_runtime.graph.context import prepare_graph
from aica_runtime.graph.contract import GraphDocument
from aica_runtime.graph.provider import GraphProvider
from aica_runtime.graph.validation import validate_document
from aica_schema.protocol import ProtocolError, dumps, empty_archive, loads
from graph_contract_design.cases import make_case
from graph_parser_fixture import claim, model_reply, proposal_for
from jsonschema import Draft202012Validator
from semantic_fixture import sample_archive


def context_for(case, nodes=None):
    return prepare_graph(case["archive_bytes"], "actual-import", lambda identifier, limit: case["history"][identifier], nodes)


def parse(context, proposal=None):
    return assemble_graph(proposal if proposal is not None else proposal_for(context.prepared),
                          context, "actual-graph", "actual-revision", "actual-branch", "mock-model")


def reencode(case):
    case["archive_bytes"] = dumps(case["archive"]).encode()


@pytest.mark.parametrize("name", ["multiturn", "parallel", "partial"])
def test_generic_pipeline_preserves_calls_dependencies_values_roles_and_source_bytes(name):
    case = make_case(name)
    before = copy.deepcopy(case)
    context = context_for(case)
    graph = parse(context)
    assert case == before
    assert len(graph["nodes"]) == 4
    retry = next(node for node in graph["nodes"] if len(node["source_node_ids"]) == 2)
    assert set(retry["run_ids"]) == {"run-raw-character-first", "run-raw-character-revised"}
    assert len(graph["edges"]) == 1 and graph["edges"][0]["state"] == "verified"
    assert graph["edges"][0]["binding"]["target_input"] == "reference_image"
    assert len(graph["variables"]) == 8
    assert graph["parse"]["input_sha256"] == context.prepared["input_sha256"]
    timeline = context.prepared["conversation_state"]["message_timeline"]
    assert [item["role"] for item in timeline] == ["user", "agent", "user", "tool"]
    assert timeline[1]["causal_parents"] == ["event-1"] and timeline[1]["source_seq"] == 2
    assert not graph["executable"] and graph["state"] == "draft"
    validate_document(graph, context.archive_bytes, context.history)
    schema = Path(__file__).parents[1] / "design/graph-v0.1/GraphDocument.schema.json"
    Draft202012Validator(loads(schema.read_text(encoding="utf-8"))).validate(graph)


def test_accepts_a_extraction_and_empty_archive_without_expected_graph():
    archive, _ = sample_archive()
    for source in [archive, empty_archive("An empty archive")]:
        context = prepare_graph(dumps(source).encode(), "import-without-fixture")
        if source["graph"]["nodes"]:
            assert any(location["decode"] == "identity" and location["value_pointer"] == "/prompt"
                       for location in context.prepared["parameter_locations"])
        graph = parse(context)
        assert {raw for node in graph["nodes"] for raw in node["source_node_ids"]} == set(context.prepared["source_node_ids"])
        GraphDocument.model_validate(graph)


def test_selected_subgraph_and_unassigned_members_are_visible():
    context = context_for(make_case(), ["raw-panel"])
    proposal = {"groups": [], "variables": [], "edges": [], "unresolved": []}
    graph = parse(context, proposal)
    assert graph["coverage"]["unassigned_source_node_ids"] == ["raw-panel"]
    assert {item["code"] for item in graph["coverage"]["issues"]} >= {"UNASSIGNED_SOURCE", "EXTERNAL_DEPENDENCY"}


@pytest.mark.parametrize("damage", ["outside_scope", "duplicate_owner", "self_review", "wrong_role", "wrong_actor",
                                    "invented_evidence", "fake_variable", "wrong_binding", "group_cycle"])
def test_rejects_model_errors_before_publishing(damage):
    case = make_case()
    if damage == "group_cycle":
        case["archive"]["graph"]["edges"] += [
            {"id": "a-b", "kind": "control", "source": "raw-character-first", "target": "raw-audio"},
            {"id": "b-c", "kind": "control", "source": "raw-audio", "target": "raw-character-revised"}]
        reencode(case)
    context = context_for(case, ["raw-plan"] if damage == "outside_scope" else None)
    proposal = proposal_for(context.prepared)
    group = proposal["groups"][0]
    if damage == "outside_scope":
        group["source_node_ids"].append("raw-panel")
    elif damage == "duplicate_owner":
        proposal["groups"][1]["source_node_ids"].extend(group["source_node_ids"])
    elif damage == "self_review":
        group["purpose"]["review"] = "accepted"
    elif damage == "wrong_role":
        group["purpose"]["attributed_to"] = "user"
    elif damage == "wrong_actor":
        agent = next(f for f in context.prepared["facts"] if f["role"] == "agent")
        group["purpose"] = claim("伪造主体", [agent["id"]], "agent", "wrong-agent")
    elif damage == "invented_evidence":
        group["purpose"]["evidence_ids"] = ["imaginary-evidence"]
    elif damage == "fake_variable":
        proposal["variables"][0]["locator"]["value_pointer"] = "/not-present"
    elif damage == "wrong_binding":
        proposal["variables"][0]["locator"]["pointer"] = "/execution/inputs/reference_image"
    with pytest.raises(ProtocolError, match="validation") as caught:
        parse(context, proposal)
    assert caught.value.code == "invalid_semantic_graph"


def test_missing_corrupt_and_future_history_are_reported_and_never_used_as_evidence():
    case = make_case()
    for reader in [None, lambda identifier, limit: b'{"bad":true}\n']:
        if reader is None:
            context = prepare_graph(case["archive_bytes"], "import", reader)
            assert context.prepared["coverage"]["omitted_history_artifact_ids"] == ["fixture-history"]
            assert not context.history
        else:
            with pytest.raises(ProtocolError) as caught:
                prepare_graph(case["archive_bytes"], "import", reader)
            assert caught.value.code == "storage_corrupt"
    events = [loads(line) for line in case["history"]["fixture-history"].decode().splitlines()]
    events[-1]["commit_seq"] = 100
    replace_history(case, events)
    context = context_for(case)
    assert not context.history and not context.prepared["conversation_state"]["message_timeline"]
    assert context.prepared["coverage"]["issues"][0]["code"] == "HISTORY_INVALID"


def replace_history(case, events):
    import hashlib

    raw = ("\n".join(dumps(item) for item in events) + "\n").encode()
    case["history"]["fixture-history"] = raw
    case["archive"]["artifacts"][0].update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    reencode(case)


def test_reserved_recorder_events_do_not_discard_real_dialogue_and_system_is_excluded():
    case = make_case()
    events = [loads(line) for line in case["history"]["fixture-history"].decode().splitlines()]
    events[0]["event"].update(type="session.created", payload={"session_token": "private-value-not-for-model"})
    events[1]["event"]["payload"].update(role="system", content="private system configuration")
    replace_history(case, events)
    context = context_for(case)
    assert not context.prepared["coverage"]["omitted_history_artifact_ids"]
    encoded = dumps(context.prepared)
    assert "private-value-not-for-model" not in encoded and "private system configuration" not in encoded
    assert len(context.prepared["conversation_state"]["message_timeline"]) == 2


def test_secret_parameter_and_model_output_are_not_exposed():
    case = make_case()
    case["archive"]["graph"]["nodes"][1]["execution"]["params"] = {"api_key": "plaintext-private-value", "count": 2}
    reencode(case)
    context = context_for(case)
    assert "plaintext-private-value" not in dumps(context.prepared)
    proposal = proposal_for(context.prepared)
    assert not any(location["pointer"].endswith("api_key") for location in context.prepared["parameter_locations"])
    variable = proposal["variables"][0]
    variable["locator"].update(pointer="/execution/params/api_key", decode="identity", value_pointer="")
    with pytest.raises(ProtocolError):
        parse(context, proposal)


def test_large_context_marks_model_preview_and_retains_complete_source():
    archive = empty_archive("too-large")
    archive["graph"]["nodes"] = [{"id": "prompt", "kind": "prompt", "label": "huge", "branch_id": "main",
                                   "provenance": {"mode": "recorded", "evidence": [{"source": "X" * 140000}]}}]
    raw = dumps(archive).encode()
    context = prepare_graph(raw, "import")
    assert context.archive_bytes == raw
    assert context.archive["graph"]["nodes"][0]["provenance"]["evidence"][0]["source"] == "X" * 140000
    assert context.prepared["input_selection"]["mode"] == "bounded_previews_all_fact_references"
    assert any(issue["code"] == "MODEL_INPUT_PREVIEW" for issue in context.prepared["coverage"]["issues"])
    assert len(dumps(context.prepared).encode()) <= 2 * 1024 * 1024


def test_semantic_and_candidate_cycles_do_not_become_verified_execution_edges():
    context = context_for(make_case())
    proposal = proposal_for(context.prepared)
    first, second = [group["key"] for group in proposal["groups"][:2]]
    evidence = proposal["groups"][0]["evidence_ids"]
    proposal["edges"] = [
        {"kind": "semantic", "source_key": first, "target_key": second, "relation": "supports", "explanation": claim("语义候选", evidence)},
        {"kind": "semantic", "source_key": second, "target_key": first, "relation": "continues", "explanation": claim("语义候选", evidence)},
        {"kind": "control", "source_key": first, "target_key": second, "explanation": claim("控制候选", evidence)},
        {"kind": "control", "source_key": second, "target_key": first, "explanation": claim("控制候选", evidence)},
    ]
    graph = parse(context, proposal)
    assert sum(edge["kind"] == "semantic" for edge in graph["edges"]) == 2
    assert sum(edge["kind"] == "control" and edge["state"] == "candidate" for edge in graph["edges"]) == 2
    assert sum(edge["kind"] == "data" and edge["state"] == "verified" for edge in graph["edges"]) == 1


def test_saved_real_model_result_keeps_contract_and_original_usage_without_another_call():
    report = loads((Path(__file__).parent / "evidence/graph-parser-live.json").read_text(encoding="utf-8"))
    GraphDocument.model_validate(report["graph"])
    assert report["job"]["usage"]["total_tokens"] == 9906
    assert report["external_model_calls"] == 1 and report["graph_survives_restart"]
    assert report["graph"]["parse"]["prompt_version"] == "aica-graph-prompt/0.1.0"


@pytest.mark.parametrize("failure", ["truncated", "invalid_json", "mixed_edge", "connection", "http"])
def test_provider_single_call_and_failure_classification(failure):
    context, calls = context_for(make_case()), []

    def handler(request):
        calls.append(request)
        if failure == "connection":
            raise httpx.ReadTimeout("test-timeout")
        response = model_reply(proposal_for(context.prepared))
        if failure == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        elif failure == "invalid_json":
            response["choices"][0]["message"]["content"] = '{"groups":[],"groups":[]}'
        elif failure == "mixed_edge":
            response["choices"][0]["message"]["content"] = '{"groups":[],"edges":[{"kind":"data","relation":"supports"}]}'
        return httpx.Response(503 if failure == "http" else 200, json=response)

    provider = GraphProvider(ProviderSettings(model="deepseek-flash", api_key="test-key-only"), httpx.MockTransport(handler))
    with pytest.raises(ProtocolError) as caught:
        provider.propose(context.prepared)
    assert len(calls) == 1
    assert caught.value.code == ("provider_connection" if failure == "connection" else "provider_rejected" if failure == "http"
                                 else "invalid_semantic_graph")
