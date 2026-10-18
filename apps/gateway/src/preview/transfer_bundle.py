from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Mapping

from .artifact_store import ArtifactStore, atomic_write
from .contracts import PreviewError, canonical_json
from .schema_contract import load_validation_protocol


def export_transfer_bundle(archive: Mapping, store: ArtifactStore) -> bytes:
    load_validation_protocol().require_archive(dict(archive))
    archive_bytes = canonical_json(archive)
    if len(archive_bytes) > 16 * 1024 * 1024 or len(archive["artifacts"]) > 4096:
        raise PreviewError("transfer_limit", "Archive exceeds A transfer limits")
    manifest = {
        "format": "aica-transfer/0.1",
        "archive": {
            "path": "aica.json",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "size": len(archive_bytes),
        },
        "artifacts": [],
    }
    buffer = io.BytesIO()
    total, stored = len(archive_bytes), set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("aica.json", archive_bytes)
        for artifact in archive["artifacts"]:
            data = store.read(artifact)
            if len(data) > 64 * 1024 * 1024:
                raise PreviewError("transfer_limit", "Artifact exceeds A transfer limits")
            path = "objects/" + artifact["sha256"]
            manifest["artifacts"].append(
                {"artifact_id": artifact["id"], "path": path, "sha256": artifact["sha256"], "size": len(data)}
            )
            if path not in stored:
                total += len(data)
                if total > 510 * 1024 * 1024:
                    raise PreviewError("transfer_limit", "Bundle exceeds A transfer limits")
                bundle.writestr(path, data)
                stored.add(path)
        manifest_bytes = canonical_json(manifest)
        if len(manifest_bytes) > 2 * 1024 * 1024:
            raise PreviewError("transfer_limit", "Manifest exceeds A transfer limits")
        bundle.writestr("manifest.json", manifest_bytes)
    return buffer.getvalue()


def write_transfer_bundle(archive, store, path):
    atomic_write(path, export_transfer_bundle(archive, store))
