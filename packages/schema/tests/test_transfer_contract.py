import pytest
from aica_schema.protocol import ProtocolError
from aica_schema.transfer import require_transfer, transfer_schema
from jsonschema import Draft202012Validator


def test_transfer_contract_sections_are_self_contained_and_validate_empty_bundle():
    for section in ("manifest", "receipt"):
        Draft202012Validator.check_schema(transfer_schema(section))
    manifest = {"format": "aica-transfer/0.1", "archive": {
        "path": "aica.json", "sha256": "a" * 64, "size": 20}, "artifacts": []}
    require_transfer(manifest)
    manifest["archive"]["path"] = "../aica.json"
    with pytest.raises(ProtocolError):
        require_transfer(manifest)


def test_upload_receipt_requires_bound_hashes_and_rejects_unexpected_fields():
    receipt = {"status": "success", "format": "aica-transfer/0.1", "import_id": "import",
               "project_id": "project", "bundle_sha256": "a" * 64, "archive_sha256": "b" * 64}
    require_transfer(receipt, "receipt")
    receipt["session_token"] = "must-not-be-returned"
    with pytest.raises(ProtocolError):
        require_transfer(receipt, "receipt")
