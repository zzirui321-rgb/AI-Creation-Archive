from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .contracts import AdapterOutput, ExecutionRequest, JsonObject, PreviewError, canonical_json, digest

SUFFIXES = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/html": ".html",
    "application/json": ".json",
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "text/javascript": ".js",
    "text/x-python": ".py",
    "text/css": ".css",
}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ArtifactStore:
    def __init__(
        self,
        root: str | Path,
        *,
        prewarmed_root: str | Path | None = None,
        input_roots: tuple[Path, ...] = (),
        uri_prefix: str | None = None,
    ):
        self.root = Path(root).resolve()
        self.prewarmed_root = Path(prewarmed_root).resolve() if prewarmed_root else None
        self.input_roots = (self.root, *(Path(path).resolve() for path in input_roots))
        if self.prewarmed_root:
            self.input_roots += (self.prewarmed_root,)
        self.uri_prefix = uri_prefix.rstrip("/") if uri_prefix else None

    def put(self, data: bytes, mime_type: str, *, node_id: str | None, run_id: str | None) -> JsonObject:
        content_hash = hashlib.sha256(data).hexdigest()
        relative = Path("objects") / content_hash[:2] / (content_hash + SUFFIXES.get(mime_type, ".bin"))
        path = self.root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != content_hash:
            atomic_write(path, data)
        uri = f"{self.uri_prefix}/{relative.as_posix()}" if self.uri_prefix else path.as_uri()
        return {
            "id": f"artifact-{uuid4().hex}",
            "uri": uri,
            "mime_type": mime_type,
            "sha256": content_hash,
            "size": len(data),
            "availability": "available",
            "producer_node_id": node_id,
            "producer_run_id": run_id,
        }

    def local_path(self, uri: str) -> Path:
        if self.uri_prefix and uri.startswith(self.uri_prefix + "/"):
            path = self.root / unquote(uri[len(self.uri_prefix) + 1 :])
        elif uri.startswith("file:"):
            parsed = urlparse(uri)
            if parsed.netloc not in {"", "localhost"}:
                raise PreviewError("blocked_uri", "Remote file hosts are not supported")
            value = unquote(parsed.path)
            if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
                value = value[1:]
            path = Path(value)
        else:
            parsed = urlparse(uri)
            if parsed.scheme and not (os.name == "nt" and len(parsed.scheme) == 1):
                raise PreviewError("unmaterialized_uri", "Runtime must materialize remote inputs before composition")
            path = Path(uri)
            if not path.is_absolute():
                path = self.root / path
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.input_roots):
            raise PreviewError("blocked_uri", "Input is outside configured artifact roots")
        return path

    def read(self, artifact: Mapping[str, Any]) -> bytes:
        for uri in [artifact["uri"], *artifact.get("locations", [])]:
            try:
                data = self.local_path(uri).read_bytes()
            except (OSError, PreviewError):
                continue
            if artifact.get("sha256") != hashlib.sha256(data).hexdigest():
                continue
            if artifact.get("size") is not None and artifact["size"] != len(data):
                continue
            return data
        raise PreviewError("artifact_unavailable", f"No verified local copy of {artifact['id']}")

    @staticmethod
    def cache_name(request: ExecutionRequest, namespace: str) -> str:
        return digest({"key": request.stage_key, "fingerprint": request.fingerprint, "source": namespace}) + ".json"

    def load_cache(self, request: ExecutionRequest, namespace: str) -> AdapterOutput | None:
        root = self.prewarmed_root if namespace == "prewarmed_cache" else self.root / "cache" / namespace
        if root is None:
            return None
        try:
            entry = json.loads((root / self.cache_name(request, namespace)).read_text(encoding="utf-8"))
            if not isinstance(entry, dict) or not isinstance(entry.get("metadata", {}), dict):
                return None
            if entry["stage_key"] != request.stage_key or entry["fingerprint"] != request.fingerprint:
                return None
            if not isinstance(entry["artifact"].get("mime_type"), str):
                return None
            data = self.read(entry["artifact"])
            metadata = entry.get("metadata", {})
            if metadata.get("origin_source") == "local_fallback" and namespace != "local_fallback":
                return None
            return AdapterOutput(data, entry["artifact"]["mime_type"], namespace, metadata, cache_hit=True)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, PreviewError):
            return None

    def save_cache(self, request: ExecutionRequest, artifact: Mapping[str, Any], output: AdapterOutput) -> None:
        if output.source == "prewarmed_cache":
            return
        entry = {
            "stage_key": request.stage_key,
            "fingerprint": request.fingerprint,
            "artifact": dict(artifact),
            "metadata": {**output.metadata, "origin_source": output.source},
        }
        path = self.root / "cache" / output.source / self.cache_name(request, output.source)
        atomic_write(path, canonical_json(entry))

    def prewarm(
        self, request: ExecutionRequest, artifact: Mapping[str, Any], *, metadata: JsonObject | None = None
    ) -> Path:
        if self.prewarmed_root is None:
            raise PreviewError("missing_prewarmed_root", "Configure prewarmed_root before publishing")
        data = self.read(artifact)
        origin = (metadata or {}).get("origin_source", (metadata or {}).get("source"))
        if origin not in {"live_api", "imported_verified"}:
            raise PreviewError("fallback_prewarm", "Prewarming requires explicit live_api or imported_verified origin")
        target = self.prewarmed_root / "objects" / (artifact["sha256"] + SUFFIXES.get(artifact["mime_type"], ".bin"))
        atomic_write(target, data)
        reference = {**artifact, "uri": target.as_uri(), "locations": []}
        entry = {
            "stage_key": request.stage_key,
            "fingerprint": request.fingerprint,
            "artifact": reference,
            "metadata": {**(metadata or {}), "origin_source": origin},
        }
        manifest = self.prewarmed_root / self.cache_name(request, "prewarmed_cache")
        atomic_write(manifest, canonical_json(entry))
        return manifest
