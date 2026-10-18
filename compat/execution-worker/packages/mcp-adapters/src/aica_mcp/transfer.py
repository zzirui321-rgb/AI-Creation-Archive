import base64
import hashlib
import ipaddress
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from aica_schema.protocol import (
    EMBEDDED_EXTENSION,
    PORTABLE_JSON_LIMIT,
    ProtocolError,
    dumps,
    embedded_objects,
    loads,
    require,
    require_archive,
)
from aica_schema.transfer import require_transfer

from .files import atomic_write, read_artifact

ARCHIVE_LIMIT = 16 * 1024 * 1024
MANIFEST_LIMIT = 2 * 1024 * 1024
ARTIFACT_LIMIT = 64 * 1024 * 1024
BUNDLE_LIMIT = 512 * 1024 * 1024
CONTENT_TYPE = "application/vnd.aica.bundle+zip"
JSON_CONTENT_TYPE = "application/json"


def content_hash(content):
    value = hashlib.sha256(content).hexdigest()
    return value


def read_portable_json(path):
    with Path(path).open("rb") as source:
        raw = source.read(PORTABLE_JSON_LIMIT + 1)
    require(len(raw) <= PORTABLE_JSON_LIMIT, "JSON exceeds 128 MiB", "transfer_limit")
    try:
        archive = loads(raw.decode("utf-8"))
    except UnicodeError as error:
        raise ProtocolError("AiCA JSON must use UTF-8", "invalid_portable_json") from error
    require_archive(archive)
    require(len(archive["artifacts"]) <= 4096, "JSON exceeds 4096 artifacts", "transfer_limit")
    objects = embedded_objects(archive, complete=True)
    metadata = {**archive, "extensions": {key: value for key, value in archive.get("extensions", {}).items() if key != EMBEDDED_EXTENSION}}
    require(len(dumps(metadata).encode("utf-8")) <= ARCHIVE_LIMIT, "Archive metadata exceeds 16 MiB", "transfer_limit")
    return archive, objects, raw


def build_json(project, output, expected_sha256=None):
    project = Path(project).resolve()
    output = Path(output).absolute()
    require(output.resolve() != project / "aica.json" and not output.resolve().is_relative_to(project / ".aica"),
            "Write portable JSON outside live aica.json and private recorder state", "unsafe_location")
    with (project / "aica.json").open("rb") as source:
        raw = source.read(PORTABLE_JSON_LIMIT + 1)
    require(len(raw) <= PORTABLE_JSON_LIMIT, "Archive exceeds JSON transfer limit", "transfer_limit")
    require(expected_sha256 is None or content_hash(raw) == expected_sha256,
            "Snapshot changed after export; retry exporting JSON", "snapshot_conflict")
    archive = loads(raw.decode("utf-8"))
    require_archive(archive)
    existing = embedded_objects(archive)
    archive.setdefault("extensions", {}).pop(EMBEDDED_EXTENSION, None)
    require(len(dumps(archive).encode("utf-8")) <= ARCHIVE_LIMIT, "Archive metadata exceeds 16 MiB", "transfer_limit")
    require(len(archive["artifacts"]) <= 4096, "JSON exceeds 4096 artifacts", "transfer_limit")
    objects = {}
    size = 0
    for artifact in archive["artifacts"]:
        sha256 = artifact["sha256"]
        if sha256 not in objects:
            content = existing[sha256] if sha256 in existing else read_artifact(project, artifact, ARTIFACT_LIMIT)
            size += len(content)
            require(size <= PORTABLE_JSON_LIMIT * 3 // 4, "Artifacts exceed JSON transfer limit", "transfer_limit")
            objects[sha256] = base64.b64encode(content).decode("ascii")
    archive["extensions"][EMBEDDED_EXTENSION] = {"version": "0.1", "encoding": "base64", "objects": objects}
    require_archive(archive)
    embedded_objects(archive, complete=True)
    content = dumps(archive, pretty=True).encode("utf-8")
    require(len(content) <= PORTABLE_JSON_LIMIT, "JSON exceeds 128 MiB", "transfer_limit")
    atomic_write(output, content)
    return {"status": "success", "portable": True, "path": str(output), "sha256": content_hash(content),
            "size": len(content), "artifact_count": len(archive["artifacts"]), "object_count": len(objects)}


def stream_hash(stream):
    hasher = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        require(size <= BUNDLE_LIMIT, "Bundle exceeds 512 MiB", "transfer_limit")
        hasher.update(block)
    value = hasher.hexdigest()
    return value, size


def build_bundle(project, output, expected_sha256=None, archive_bytes=None):
    project = Path(project).resolve()
    output = Path(output).absolute()
    require(not output.resolve().is_relative_to(project / ".aica"),
            "Write transfer bundles outside private recorder state", "unsafe_location")
    if archive_bytes is None:
        with (project / "aica.json").open("rb") as stream:
            archive_bytes = stream.read(ARCHIVE_LIMIT + 1)
    require(len(archive_bytes) <= ARCHIVE_LIMIT, "Archive exceeds 16 MiB", "transfer_limit")
    archive_hash = content_hash(archive_bytes)
    require(expected_sha256 is None or archive_hash == expected_sha256,
            "Snapshot changed after export; retry building the bundle", "snapshot_conflict")
    archive = loads(archive_bytes.decode("utf-8"))
    require_archive(archive)
    require(len(archive["artifacts"]) <= 4096, "Bundle exceeds 4096 artifacts", "transfer_limit")
    manifest = {"format": "aica-transfer/0.1", "archive": {
        "path": "aica.json", "sha256": archive_hash, "size": len(archive_bytes)}, "artifacts": []}
    descriptor, temporary = tempfile.mkstemp(prefix=".aica-transfer-", dir=output.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("aica.json", archive_bytes)
                stored = set()
                total = len(archive_bytes)
                for artifact in archive["artifacts"]:
                    content = read_artifact(project, artifact, ARTIFACT_LIMIT)
                    name = "objects/" + content_hash(content)
                    manifest["artifacts"].append({"artifact_id": artifact["id"], "path": name,
                                                  "sha256": content_hash(content), "size": len(content)})
                    if name not in stored:
                        total += len(content)
                        require(total <= BUNDLE_LIMIT, "Bundle exceeds 512 MiB", "transfer_limit")
                        bundle.writestr(name, content)
                        stored.add(name)
                require_transfer(manifest)
                manifest_bytes = dumps(manifest, pretty=True).encode("utf-8")
                require(len(manifest_bytes) <= MANIFEST_LIMIT, "Manifest exceeds 2 MiB", "transfer_limit")
                bundle.writestr("manifest.json", manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        result = verify_bundle(temporary)
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    result["path"] = str(output)
    return result


def verify_bundle_stream(stream):
    stream.seek(0)
    bundle_hash, bundle_size = stream_hash(stream)
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as bundle:
            entries = bundle.infolist()
            names = {item.filename for item in entries}
            require(len(entries) <= 4098 and len(names) == len(entries),
                    "Duplicate ZIP members or too many members", "invalid_transfer")
            require({"manifest.json", "aica.json"} <= names, "Missing bundle metadata", "invalid_transfer")
            total = 0
            for entry in entries:
                require(entry.compress_type == zipfile.ZIP_STORED and not entry.flag_bits & 1
                        and stat.S_IFMT(entry.external_attr >> 16) in {0, stat.S_IFREG},
                        "Bundle members must be unencrypted, stored regular files", "invalid_transfer")
                limit = MANIFEST_LIMIT if entry.filename == "manifest.json" else ARTIFACT_LIMIT
                if entry.filename == "aica.json":
                    limit = ARCHIVE_LIMIT
                require(entry.file_size <= limit, "Bundle member exceeds size limit", "transfer_limit")
                total += entry.file_size
            require(total <= BUNDLE_LIMIT, "Bundle contents exceed 512 MiB", "transfer_limit")
            manifest = loads(bundle.read("manifest.json").decode("utf-8"))
            require_transfer(manifest)
            archive_bytes = bundle.read("aica.json")
            require(content_hash(archive_bytes) == manifest["archive"]["sha256"]
                    and len(archive_bytes) == manifest["archive"]["size"],
                    "Archive does not match manifest", "transfer_mismatch")
            archive = loads(archive_bytes.decode("utf-8"))
            require_archive(archive)
            records = {item["artifact_id"]: item for item in manifest["artifacts"]}
            require(len(records) == len(manifest["artifacts"])
                    and set(records) == {item["id"] for item in archive["artifacts"]},
                    "Manifest must map every archive artifact exactly once", "invalid_transfer")
            expected = {"manifest.json", "aica.json"}
            verified = set()
            for artifact in archive["artifacts"]:
                record = records[artifact["id"]]
                require(record["sha256"] == artifact["sha256"]
                        and record["path"] == "objects/" + record["sha256"]
                        and ("size" not in artifact or record["size"] == artifact["size"]),
                        "Artifact identity differs from archive", "transfer_mismatch")
                name = record["path"]
                require(name in names and bundle.getinfo(name).file_size == record["size"],
                        "Artifact bytes are missing or have a different size", "transfer_mismatch")
                expected.add(name)
                if name not in verified:
                    with bundle.open(name) as content:
                        actual_hash, size = stream_hash(content)
                    require(actual_hash == record["sha256"] and size == record["size"],
                            "Artifact bytes do not match hash", "transfer_mismatch")
                    verified.add(name)
            require(names == expected, "Unexpected ZIP members", "invalid_transfer")
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError) as error:
        raise ProtocolError("Invalid transfer ZIP", "invalid_transfer") from error
    return {"status": "success", "bundle_sha256": bundle_hash, "size": bundle_size,
            "archive_sha256": manifest["archive"]["sha256"], "project_id": archive["metadata"]["project_id"],
            "artifact_count": len(records), "object_count": len(verified), "manifest": manifest}


def verify_bundle(path):
    with Path(path).open("rb") as stream:
        result = verify_bundle_stream(stream)
    return result


def require_upload_url(url, allow_loopback_http=False):
    destination = urlsplit(url)
    loopback = destination.hostname == "localhost"
    if destination.hostname and not loopback:
        try:
            loopback = ipaddress.ip_address(destination.hostname).is_loopback
        except ValueError:
            loopback = False
    require((destination.scheme == "https" or allow_loopback_http and destination.scheme == "http" and loopback)
            and destination.hostname and destination.username is None
            and destination.password is None and not destination.fragment and not destination.query,
            "Use HTTPS without credentials, query or fragment; explicit HTTP is limited to loopback tests",
            "unsafe_destination")
    require("\\" not in url and not any(ord(char) <= 32 or ord(char) == 127 for char in url),
            "Invalid upload URL", "unsafe_destination")
    destination.port


def upload_bundle(path, url, token, confirm_upload=False, transport=None, allow_loopback_http=False):
    require(confirm_upload is True, "Uploading requires explicit confirmation", "upload_not_authorized")
    require_upload_url(url, allow_loopback_http)
    require(isinstance(token, str) and token and all(33 <= ord(char) <= 126 for char in token),
            "A valid platform upload token is required", "upload_not_authorized")
    # A private copy keeps validation, retry identity and transmitted bytes consistent.
    with Path(path).open("rb") as source, tempfile.TemporaryFile() as snapshot:
        size = 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            size += len(block)
            require(size <= BUNDLE_LIMIT, "Bundle exceeds 512 MiB", "transfer_limit")
            snapshot.write(block)
        report = verify_bundle_stream(snapshot)
        snapshot.seek(0)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": CONTENT_TYPE,
                   "Content-Length": str(report["size"]), "Accept": "application/json",
                   "Idempotency-Key": "sha256:" + report["bundle_sha256"],
                   "X-AiCA-Bundle-SHA256": report["bundle_sha256"]}
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False,
                              follow_redirects=False, transport=transport) as client:
                with client.stream("POST", url, headers=headers, content=snapshot) as response:
                    require(response.status_code in {200, 201},
                            f"Upload not confirmed (HTTP {response.status_code}); retry the same bundle",
                            "upload_rejected")
                    body = bytearray()
                    for block in response.iter_bytes():
                        body.extend(block)
                        require(len(body) <= 64 * 1024, "Upload receipt exceeds 64 KiB", "invalid_receipt")
        except httpx.HTTPError as error:
            raise ProtocolError("Upload connection failed; remote outcome may be unknown. Retry the same bundle.",
                                "upload_unconfirmed") from error
    try:
        receipt = loads(body.decode("utf-8"))
        require_transfer(receipt, "receipt")
    except (ProtocolError, UnicodeError) as error:
        raise ProtocolError("Platform returned an invalid upload receipt", "invalid_receipt") from error
    require(receipt["bundle_sha256"] == report["bundle_sha256"]
            and receipt["archive_sha256"] == report["archive_sha256"],
            "Upload receipt does not identify this bundle", "invalid_receipt")
    return {"status": "success", "receipt": receipt, "source_project_id": report["project_id"]}
