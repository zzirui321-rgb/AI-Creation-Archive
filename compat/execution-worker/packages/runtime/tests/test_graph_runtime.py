import copy
from pathlib import Path

import pytest
from aica_runtime.engine import cache_key
from aica_runtime.executors import default_registry, execution_plan
from aica_runtime.graph import dirty_nodes, ordered_nodes, resolve_inputs
from aica_schema.protocol import ProtocolError, loads, resolve_variable_operations


@pytest.fixture
def archive():
    path = Path(__file__).resolve().parents[3] / "packages/schema/tests/fixtures/branching.aica.json"
    document = loads(path.read_text(encoding="utf-8"))
    return document


def test_dirty_propagates_only_affected_downstream_and_keep_is_clean(archive):
    changed = copy.deepcopy(archive)
    changed["variables"] = resolve_variable_operations(changed["variables"], [
        {"variable_id": "character", "mode": "override", "value": "different"}])
    assert dirty_nodes(archive, changed) == ["character", "panel"]
    unchanged = copy.deepcopy(archive)
    unchanged["variables"] = resolve_variable_operations(unchanged["variables"], [
        {"variable_id": "character", "mode": "keep"}])
    assert dirty_nodes(archive, unchanged) == []
    changed = copy.deepcopy(archive)
    changed["variables"]["steps"]["value"] = 100
    assert dirty_nodes(archive, changed) == []


def test_target_planning_includes_dependencies_but_excludes_dead_end(archive):
    assert set(ordered_nodes(archive, ["panel"])) == {"story", "character", "panel"}
    with pytest.raises(ProtocolError):
        ordered_nodes(archive, ["discarded"])
    assert not execution_plan(archive, default_registry())["executable"]


def test_template_literal_variable_and_upstream_resolution(archive):
    node = archive["graph"]["nodes"][1]
    node["execution"]["inputs"].update({"template": {"kind": "template", "template": "Draw {{who}}",
                                                      "variables": {"who": "character"}},
                                        "literal": {"kind": "literal", "value": [1, True]}})
    result = resolve_inputs(archive, node, {})
    assert result == {"prompt": "cyberpunk girl", "template": "Draw cyberpunk girl", "literal": [1, True]}
    panel = archive["graph"]["nodes"][2]
    with pytest.raises(ProtocolError):
        resolve_inputs(archive, panel, {})


def test_cache_covers_configuration_versions_stage_harness_and_artifact_bytes():
    registry = default_registry()
    execution = {"kind": "mcp", "tool": "aica.echo", "version": "1", "inputs": {}, "params": {}, "outputs": ["text"]}
    tool = registry.resolve(execution)
    arguments = [execution, {"text": "value"}, {}, {"config": {"mode": "one"}}, tool,
                 {"picture": {"sha256": "a" * 64, "mime_type": "image/png"}}, "draft"]
    original = cache_key(*arguments)
    for index, value in ((0, {**execution, "params": {"seed": 2}}), (1, {"text": "different"}),
                         (2, {"parent": "different"}), (3, {"config": {"mode": "two"}}),
                         (4, {**tool, "version": "2"}), (6, "final")):
        changed = list(arguments)
        changed[index] = value
        assert cache_key(*changed) != original
    arguments[1] = {"text": "picture"}
    original = cache_key(*arguments)
    arguments[5]["picture"]["sha256"] = "b" * 64
    assert cache_key(*arguments) != original
