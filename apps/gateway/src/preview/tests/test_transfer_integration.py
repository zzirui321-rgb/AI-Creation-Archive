import io
import json
import sys
import types
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from preview import PreviewError
from preview.transfer_bundle import export_transfer_bundle


def test_b_bundle_passes_latest_a_transfer_verifier(engine, node, context, a_protocol, monkeypatch):
    from preview.scripts.build_review import empty_archive

    root = Path(__file__).resolve().parents[5]

    def source(path):
        return (root / path).read_bytes()

    schema = json.loads(source("packages/schema/src/aica_schema/transfer.schema.json"))
    transfer_contract = types.ModuleType("aica_schema.transfer")

    def require_transfer(value, section="manifest"):
        Draft202012Validator({**schema, "$ref": f"#/$defs/{section}"}).validate(value)

    transfer_contract.require_transfer = require_transfer
    monkeypatch.setitem(sys.modules, "aica_schema.protocol", a_protocol)
    monkeypatch.setitem(sys.modules, "aica_schema.transfer", transfer_contract)
    verifier = types.ModuleType("aica_mcp.transfer")
    exec(compile(source("packages/mcp-adapters/src/aica_mcp/transfer.py"), "A-transfer.py", "exec"), verifier.__dict__)
    archive = empty_archive(a_protocol)
    archive["graph"]["nodes"] = []
    archive["graph"]["edges"] = []
    archive["graph"]["golden_path"] = []
    archive["variables"] = {}
    archive["interactive_questions"] = []
    archive["artifacts"] = [engine.store.put(b"portable content", "text/plain", node_id=None, run_id=None)]
    archive["artifacts"].append({**archive["artifacts"][0], "id": "second-instance"})
    bundle = export_transfer_bundle(archive, engine.store)
    report = verifier.verify_bundle_stream(io.BytesIO(bundle))
    assert report["artifact_count"] == 2 and report["object_count"] == 1
    with zipfile.ZipFile(io.BytesIO(bundle)) as file:
        assert set(file.namelist()) == {"aica.json", "manifest.json", "objects/" + archive["artifacts"][0]["sha256"]}
    engine.store.local_path(archive["artifacts"][0]["uri"]).write_bytes(b"corrupt")
    with pytest.raises(PreviewError, match="verified local copy"):
        export_transfer_bundle(archive, engine.store)
