import base64
import copy
import hashlib

import pytest
from aica_schema.protocol import (
    EMBEDDED_EXTENSION,
    ProtocolError,
    embedded_objects,
    empty_archive,
    require_archive,
    validate_archive,
)


@pytest.fixture
def portable():
    content = b"selected image bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    archive = empty_archive()
    archive["artifacts"] = [{"id": "picture", "uri": ".aica/artifacts/" + sha256, "mime_type": "image/png",
                              "sha256": sha256, "size": len(content), "availability": "available",
                              "producer_node_id": None, "producer_run_id": None}]
    archive["extensions"][EMBEDDED_EXTENSION] = {"version": "0.1", "encoding": "base64",
                                               "objects": {sha256: base64.b64encode(content).decode()}}
    return archive, sha256, content


def test_embedded_bytes_are_validated_and_deduplicated_without_changing_artifact_ids(portable):
    archive, sha256, content = portable
    duplicate = copy.deepcopy(archive["artifacts"][0])
    duplicate["id"] = "second-instance"
    archive["artifacts"].append(duplicate)
    require_archive(archive)
    assert embedded_objects(archive, complete=True) == {sha256: content}
    del archive["extensions"][EMBEDDED_EXTENSION]
    require_archive(archive)
    with pytest.raises(ProtocolError, match="missing artifact bytes"):
        embedded_objects(archive, complete=True)


@pytest.mark.parametrize("damage", ["encoding", "version", "base64", "hash", "size", "orphan", "noncanonical"])
def test_invalid_embedded_bytes_are_rejected_by_the_shared_archive_validator(portable, damage):
    archive, sha256, content = portable
    extension = archive["extensions"][EMBEDDED_EXTENSION]
    if damage == "encoding":
        extension["encoding"] = "url"
    elif damage == "version":
        extension["version"] = "9"
    elif damage == "base64":
        extension["objects"][sha256] = "!invalid!"
    elif damage == "hash":
        extension["objects"][sha256] = base64.b64encode(b"different").decode()
    elif damage == "size":
        archive["artifacts"][0]["size"] += 1
    elif damage == "orphan":
        extension["objects"]["0" * 64] = base64.b64encode(content).decode()
    else:
        extension["objects"][sha256] += "="
    assert not validate_archive(archive)["valid"]


def test_empty_archive_can_be_imported_without_any_external_bytes():
    archive = empty_archive()
    require_archive(archive)
    assert embedded_objects(archive, complete=True) == {}
