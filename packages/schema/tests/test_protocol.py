import copy
from pathlib import Path

import pytest
from aica_schema.protocol import (
    ProtocolError,
    archive_schema,
    empty_archive,
    loads,
    require_definition_snapshot,
    resolve_variable_operations,
    validate_archive,
)
from aica_schema.recording import tool_contracts
from jsonschema import Draft202012Validator


@pytest.fixture
def archive():
    document = loads((Path(__file__).parent / "fixtures" / "branching.aica.json").read_text())
    return document


def test_schema_and_branched_fixture_are_valid(archive):
    Draft202012Validator.check_schema(archive_schema())
    assert validate_archive(archive)["valid"]
    assert validate_archive(empty_archive())["valid"]
    for tool in tool_contracts():
        Draft202012Validator.check_schema(tool["inputSchema"])


def test_override_append_keep_are_ordered_and_do_not_change_defaults(archive):
    operations = [{"variable_id": "character", "mode": "override", "value": "astronaut cat"},
                  {"variable_id": "character", "mode": "append", "value": "floating burger"},
                  {"variable_id": "character", "mode": "keep"},
                  {"variable_id": "tags", "mode": "append", "value": ["space", "space"]}]
    original = copy.deepcopy(archive["variables"])
    result = resolve_variable_operations(original, operations)
    assert result["character"]["value"] == "astronaut cat, floating burger"
    assert result["character"]["default"] == "cyberpunk girl"
    assert result["tags"]["value"] == ["comic", "space", "space"]
    assert original == archive["variables"]


@pytest.mark.parametrize("operation", [
    {"variable_id": "steps", "mode": "append", "value": 1},
    {"variable_id": "steps", "mode": "override", "value": True},
    {"variable_id": "character", "mode": "keep", "value": "ignored"},
    {"variable_id": "tags", "mode": "append", "value": [10]},
])
def test_invalid_variable_edits_are_rejected(archive, operation):
    with pytest.raises(ProtocolError):
        resolve_variable_operations(archive["variables"], [operation])


@pytest.mark.parametrize("damage", ["cycle", "port", "variable", "branch", "duplicate", "version"])
def test_semantic_damage_is_rejected(archive, damage):
    if damage == "cycle":
        archive["graph"]["edges"].append({"id": "back", "source": "panel", "target": "story", "kind": "control"})
    elif damage == "port":
        archive["graph"]["edges"][0]["source_output"] = "missing"
    elif damage == "variable":
        archive["variables"]["character"]["bindings"][0]["input"] = "missing"
    elif damage == "branch":
        archive["graph"]["branches"][0]["parent_id"] = "trial"
    elif damage == "duplicate":
        archive["graph"]["nodes"].append(copy.deepcopy(archive["graph"]["nodes"][0]))
    else:
        archive["aica_version"] = "9.0"
    assert not validate_archive(archive)["valid"]


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_ambiguous_json_is_rejected(text):
    with pytest.raises(ProtocolError):
        loads(text)


def test_multi_variable_prompt_binding_is_explicit(archive):
    inputs = archive["graph"]["nodes"][1]["execution"]["inputs"]
    inputs["prompt"] = {"kind": "template", "template": "{{character}} in {{style}}",
                        "variables": {"character": "character", "style": "style"}}
    archive["variables"]["style"] = {"type": "string", "default": "manga", "value": "manga",
                                       "modes": ["keep", "override"],
                                       "bindings": [{"node_id": "character", "input": "prompt"}]}
    assert validate_archive(archive)["valid"]
    inputs["prompt"]["template"] = "{{character.__class__}}"
    assert not validate_archive(archive)["valid"]


def test_user_item_schema_cannot_fetch_remote_references(archive):
    archive["variables"]["tags"]["items_schema"] = {"$ref": "http://127.0.0.1/private"}
    assert not validate_archive(archive)["valid"]


@pytest.mark.parametrize("text", ['{"x":9007199254740992}', '{"x":1e999}'])
def test_nonportable_numbers_are_rejected(text):
    with pytest.raises(ProtocolError):
        loads(text)


@pytest.mark.parametrize("damage", ["locations", "parent", "revision_identity"])
def test_content_and_revision_references_are_consistent(archive, damage):
    artifact = {"id": "revision-file", "uri": "revision.json", "mime_type": "application/json",
                "sha256": "a" * 64, "availability": "unverified",
                "producer_node_id": None, "producer_run_id": None}
    archive["artifacts"].append(artifact)
    if damage == "parent":
        archive["graph"]["parent_revision_id"] = "unknown-revision"
    elif damage == "revision_identity":
        reference = {"kind": "revision", "revision_id": "old-revision", "artifact_id": artifact["id"]}
        archive["history"] = [reference, copy.deepcopy(reference)]
    else:
        artifact.update(sha256=None, locations=["moved.png"])
    assert not validate_archive(archive)["valid"]


def test_historical_definition_requires_matching_identity_and_valid_bindings(archive):
    definition = {key: archive.get(key, []) for key in (
        "graph", "variables", "interactive_questions", "variable_operations")}
    revision = definition["graph"]["revision_id"]
    require_definition_snapshot(definition, [], revision)
    with pytest.raises(ProtocolError, match="revision ID"):
        require_definition_snapshot(definition, [], "different-revision")
    definition["graph"]["edges"][0]["source_output"] = "missing-port"
    with pytest.raises(ProtocolError):
        require_definition_snapshot(definition, [], revision)
