import copy
import hashlib

import pytest
from aica_runtime.evidence import prepare_evidence, redact
from aica_runtime.semantic import validate_analysis
from aica_schema.protocol import ProtocolError, dumps
from semantic_fixture import attach_runs, sample_archive, sample_draft


@pytest.fixture
def prepared():
    archive, _ = sample_archive()
    return prepare_evidence(archive, "import-test", hashlib.sha256(dumps(archive).encode()).hexdigest())


def test_a_extracted_facts_produce_grounded_inferred_report_without_changing_archive(prepared):
    original = copy.deepcopy(prepared)
    report = validate_analysis(sample_draft(prepared), prepared)
    assert prepared == original
    assert report["requires_review"] and report["executable"] is False
    assert report["analysis"]["dependencies"][0]["provenance"] == "inferred"
    variable = report["analysis"]["variables"][0]
    assert variable["value"] == "黑猫宇航员，水彩风格" and variable["allowed_modes"] == ["keep", "override", "append"]
    assert report["analysis"]["golden_path"]["node_ids"] == []
    assert report["uncovered_node_ids"] == []


@pytest.mark.parametrize("damage", ["evidence", "node", "cycle", "pointer", "invented_success", "binding", "duplicate"])
def test_model_cannot_invent_references_values_or_success(prepared, damage):
    draft = sample_draft(prepared)
    if damage == "evidence":
        draft["intent"]["evidence_ids"] = ["unknown-evidence"]
    elif damage == "node":
        draft["steps"][0]["node_id"] = "invented-node"
    elif damage == "cycle":
        edge = copy.deepcopy(draft["dependencies"][0])
        edge["source"], edge["target"] = edge["target"], edge["source"]
        draft["dependencies"].append(edge)
    elif damage == "pointer":
        draft["variables"][0]["pointer"] = "/execution/inputs/arguments/invented_value"
    elif damage == "invented_success":
        draft["golden_path"]["node_ids"] = [draft["steps"][1]["node_id"]]
    elif damage == "binding":
        draft["variables"][0]["pointer"] = "/execution/input_bindings/arguments/value"
    else:
        draft["variables"].append(copy.deepcopy(draft["variables"][0]))
    with pytest.raises(ProtocolError) as error:
        validate_analysis(draft, prepared)
    assert error.value.code == "invalid_analysis"


def test_redaction_covers_nested_arguments_and_embedded_json_and_blocks_variable_exposure():
    archive, _ = sample_archive()
    tool = next(node for node in archive["graph"]["nodes"] if node["label"] == "image.generate")
    secret = "sk-synthetic-secret-01234567890"
    tool["execution"]["inputs"]["arguments"]["value"] = dumps({"prompt": "hello", "api_key": secret,
                                                                  "password": "plain-private-password"})
    prepared = prepare_evidence(archive, "import-test", "a" * 64)
    serialized = dumps(prepared)
    assert secret not in serialized and "plain-private-password" not in serialized
    draft = sample_draft(prepared)
    draft["variables"][0]["pointer"] = "/execution/inputs/arguments/api_key"
    with pytest.raises(ProtocolError, match="Redacted"):
        validate_analysis(draft, prepared)


def test_event_messages_have_file_and_line_citations_and_incomplete_coverage_is_visible():
    archive, _ = sample_archive()
    event = {"event_id": "message-1", "type": "message.recorded", "payload": {"role": "user", "content": "改成水彩"}}
    content = (dumps({"event": event}) + "\n").encode()
    archive["artifacts"].append({"id": "events", "uri": "events.ndjson", "mime_type": "application/x-ndjson",
                                 "sha256": hashlib.sha256(content).hexdigest(), "availability": "available",
                                 "producer_node_id": None, "producer_run_id": None})
    archive["history"] = [{"kind": "events", "artifact_id": "events", "through_seq": 1}]
    prepared = prepare_evidence(archive, "import-test", "a" * 64, lambda identifier, limit: content)
    message = next(fact for fact in prepared["facts"] if fact["id"].startswith("event:"))
    assert message["source"] == {"kind": "artifact", "artifact_id": "events", "line": 1, "event_id": "message-1"}
    missing = prepare_evidence(archive, "import-test", "a" * 64)
    assert missing["coverage"]["warnings"][0]["code"] == "history_not_read"


def test_large_or_unknown_scope_is_rejected_without_silent_truncation():
    archive, _ = sample_archive()
    with pytest.raises(ProtocolError):
        prepare_evidence(archive, "import-test", "a" * 64, node_ids=["missing"])
    archive["graph"]["nodes"][0]["description"] = "x" * (129 * 1024)
    with pytest.raises(ProtocolError) as error:
        prepare_evidence(archive, "import-test", "a" * 64)
    assert error.value.code == "analysis_limit"


def test_numeric_variables_cannot_offer_append(prepared):
    draft = sample_draft(prepared)
    draft["variables"][0]["pointer"] = "/execution/inputs/arguments/seed"
    variable = validate_analysis(draft, prepared)["analysis"]["variables"][0]
    assert variable["value"] == 42 and variable["type"] == "integer"
    assert variable["allowed_modes"] == ["keep", "override"]


def test_signed_urls_and_credentials_in_identifiers_are_not_sent():
    url = redact("https://user:password@example.invalid/image.png?token=private-value&sig=private-signature")
    assert "password" not in url and "private-value" not in url and "private-signature" not in url
    archive, _ = sample_archive()
    archive["graph"]["nodes"][0]["id"] = "sk-synthetic-identifier-secret-0123456789"
    with pytest.raises(ProtocolError) as error:
        prepare_evidence(archive, "import-test", "a" * 64)
    assert error.value.code == "analysis_sensitive_identifiers"


def test_latest_attempt_is_chosen_by_timestamp_instead_of_array_position():
    archive, _ = sample_archive()
    node = archive["graph"]["nodes"][1]["id"]
    attach_runs(archive, [(node, "failed", "2026-10-17T03:00:00Z"),
                          (node, "succeeded", "2026-10-17T10:00:00+08:00")])
    prepared = prepare_evidence(archive, "import-test", "a" * 64)
    assert prepared["latest_successful_node_ids"] == []


@pytest.mark.parametrize("damage", [None, "reverse", "missing_parent", "citation"])
def test_golden_path_requires_ordered_successful_dependency_closure(damage):
    archive, _ = sample_archive()
    character, panel = [node["id"] for node in archive["graph"]["nodes"] if node["kind"] == "tool"]
    attach_runs(archive, [(character, "succeeded", "2026-10-17T00:00:00Z"),
                          (panel, "succeeded", "2026-10-17T01:00:00Z")])
    prepared = prepare_evidence(archive, "import-test", "a" * 64)
    draft = sample_draft(prepared)
    draft["golden_path"] = {"node_ids": [character, panel], "text": "当前修订的成功尝试建议路径。",
                            "evidence_ids": ["run:test-run-0", "run:test-run-1"]}
    if damage == "reverse":
        draft["golden_path"]["node_ids"].reverse()
    elif damage == "missing_parent":
        draft["golden_path"]["node_ids"] = [panel]
    elif damage == "citation":
        draft["golden_path"]["evidence_ids"] = ["snapshot"]
    if damage is None:
        assert validate_analysis(draft, prepared)["analysis"]["golden_path"]["node_ids"] == [character, panel]
    else:
        with pytest.raises(ProtocolError):
            validate_analysis(draft, prepared)
