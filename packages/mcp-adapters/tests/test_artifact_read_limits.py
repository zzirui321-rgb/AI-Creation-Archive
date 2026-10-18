import hashlib
from pathlib import Path

import pytest
from aica_mcp import files
from aica_schema.protocol import ProtocolError


def test_small_artifact_read_does_not_allocate_the_entire_transfer_budget(tmp_path, monkeypatch):
    content = b"small artifact"
    path = tmp_path / "artifact.txt"
    path.write_bytes(content)
    artifact = {"id": "text", "uri": path.name, "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content), "availability": "available"}
    original = Path.open
    sizes = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    def tracked_open(target, mode="r", *args, **kwargs):
        stream = original(target, mode, *args, **kwargs)
        return Reader(stream) if target == path and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", tracked_open)
    assert files.read_artifact(tmp_path, artifact, max_bytes=512 * 1024 * 1024) == content
    assert max(sizes) <= 1024 * 1024


def test_artifact_growth_after_verification_is_still_rejected(tmp_path, monkeypatch):
    original = b"safe"
    (tmp_path / "artifact.txt").write_bytes(original + b"changed")
    artifact = {"id": "text", "uri": "artifact.txt", "sha256": hashlib.sha256(original).hexdigest(),
                "size": len(original), "availability": "available"}
    monkeypatch.setattr(files, "inspect_artifact", lambda *args: {
        "locations": [{"uri": artifact["uri"], "size": len(original), "state": "verified"}]})
    with pytest.raises(ProtocolError, match="changed while reading"):
        files.read_artifact(tmp_path, artifact, max_bytes=512 * 1024 * 1024)
