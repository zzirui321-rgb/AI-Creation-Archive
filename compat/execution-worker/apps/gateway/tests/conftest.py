import base64
import copy
import hashlib

import pytest
from aica_gateway.app import create_app
from aica_mcp.transfer import build_bundle
from aica_runtime.executors import default_registry
from aica_schema.protocol import dumps, empty_archive
from fastapi.testclient import TestClient

ALICE_TOKEN = "alice-development-test-token-12345678"
BOB_TOKEN = "bob-development-test-token-1234567890"


@pytest.fixture
def workflow():
    archive = empty_archive("Backend fixture")
    archive["variables"] = {"text": {"type": "string", "default": "hello", "value": "hello", "separator": " ",
                                      "modes": ["keep", "override", "append"],
                                      "bindings": [{"node_id": "root", "input": "text"}]}}
    for node_id, tool, inputs in (
        ("root", "aica.text-artifact", {"text": {"kind": "variable", "variable_id": "text"}}),
        ("child", "aica.echo", {"text": {"kind": "node_output", "node_id": "root", "output": "text"}}),
        ("other", "aica.echo", {"text": {"kind": "literal", "value": "independent"}}),
    ):
        archive["graph"]["nodes"].append({"id": node_id, "kind": "tool", "label": node_id, "branch_id": "main",
                                            "provenance": {"mode": "recorded", "evidence": []},
                                            "execution": {"kind": "mcp", "tool": tool, "version": "1",
                                                          "inputs": inputs, "params": {}, "outputs": ["text"]}})
    archive["graph"]["edges"] = [{"id": "dependency", "source": "root", "target": "child", "kind": "data",
                                    "source_output": "text", "target_input": "text"}]
    return archive


@pytest.fixture
def bundle_factory(tmp_path):
    count = 0
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=")

    def build(archive):
        nonlocal count
        count += 1
        project = tmp_path / f"source-{count}"
        project.mkdir()
        (project / "images").mkdir()
        (project / "images" / "pixel.png").write_bytes(image)
        document = copy.deepcopy(archive)
        document["artifacts"].append({"id": "picture", "uri": "images/pixel.png", "mime_type": "image/png",
                                       "sha256": hashlib.sha256(image).hexdigest(), "size": len(image),
                                       "availability": "available", "producer_node_id": None, "producer_run_id": None})
        (project / "aica.json").write_text(dumps(document, pretty=True), encoding="utf-8")
        target = tmp_path / f"bundle-{count}.zip"
        report = build_bundle(project, target)
        return target, report, image, document

    return build


@pytest.fixture
def registry():
    result = default_registry()
    return result


@pytest.fixture
def app(tmp_path, registry):
    result = create_app(tmp_path / "server", {"alice": ALICE_TOKEN, "bob": BOB_TOKEN}, registry)
    return result


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer " + ALICE_TOKEN}) as instance:
        yield instance
