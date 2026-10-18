import copy
import re
from pathlib import Path

from aica_schema.protocol import EMBEDDED_EXTENSION, ProtocolError, digest, dumps, loads, require, require_archive

from .files import ProjectLock, atomic_write, file_hash, private_directory, project_file
from .transfer import ARCHIVE_LIMIT, content_hash, read_portable_json

WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)}


def import_target(project, name):
    candidate = Path(name)
    require(candidate.parts and not candidate.is_absolute() and ".." not in candidate.parts
            and all(part.lower() not in {".aica", ".git"} and part.split(".")[0].lower() not in WINDOWS_DEVICES
                    and re.fullmatch(r'[^<>:"\\|?*\x00-\x1f]+', part)
                    and not part.endswith((".", " ")) for part in candidate.parts),
            "Import target must be a portable new project subdirectory", "unsafe_location")
    target = project / candidate
    require(target != project, "Import cannot replace the current project", "unsafe_location")
    for path in [target, *target.parents]:
        if path == project:
            break
        require(not path.is_symlink(), "Import target cannot contain symlinks", "unsafe_location")
    parent = target.parent.resolve(strict=True)
    require(parent.is_relative_to(project) and parent.is_dir(), "Import target parent must exist inside the project",
            "unsafe_location")
    result = parent / target.name
    return result


def checked_import_layout(target):
    for path in (target, target / ".aica", target / ".aica/imports", target / ".aica/artifacts"):
        require(not path.is_symlink(), "Import state cannot contain symlinks", "unsafe_location")
        require(not path.exists() or path.is_dir(), "Import state path is not a directory", "import_conflict")
    require(not (target / "aica.json").is_symlink(), "Imported archive cannot be a symlink", "unsafe_location")
    require(not (target / ".aica/imports/local-import.json").is_symlink(),
            "Import receipt cannot be a symlink", "unsafe_location")


def remove_empty_import(target):
    for path in (target / ".aica/imports", target / ".aica", target):
        try:
            path.rmdir()
        except OSError:
            pass


def restore_project(target, content, objects, expected):
    checked_import_layout(target)
    marker = target / ".aica/imports/local-import.json"
    if target.exists():
        require(marker.is_file(), "Target already exists without a matching import receipt; choose a new directory",
                "target_exists")
        with marker.open("rb") as stream:
            raw = stream.read(65537)
        require(len(raw) <= 65536, "Import receipt exceeds the read limit", "import_conflict")
        state = loads(raw.decode("utf-8"))
        require(isinstance(state, dict) and state.get("format") == expected["format"]
                and state.get("request_id") == expected["request_id"]
                and state.get("fingerprint") == expected["fingerprint"]
                and state.get("archive_sha256") == expected["archive_sha256"]
                and state.get("result") == expected["result"] and state.get("state") in {"preparing", "complete"},
                "Target import has different content or request_id", "id_conflict")
    else:
        target.mkdir(mode=0o700)
        state = {**expected, "state": "preparing"}
        try:
            private_directory(target / ".aica")
            private_directory(target / ".aica/imports")
            atomic_write(marker, dumps(state).encode("utf-8"))
        except OSError:
            remove_empty_import(target)
            raise
    already_imported = state["state"] == "complete"
    archive = target / "aica.json"
    require(not already_imported or archive.is_file(), "Completed import archive is missing", "import_conflict")
    if archive.exists():
        require(archive.is_file(), "Target archive is not a file", "import_conflict")
        if not already_imported:
            require(file_hash(archive) == expected["archive_sha256"],
                    "Target archive changed during import; it will not be replaced", "import_conflict")
        else:
            with archive.open("rb") as stream:
                raw = stream.read(ARCHIVE_LIMIT + 1)
            require(len(raw) <= ARCHIVE_LIMIT, "Current archive exceeds the import verification read limit",
                    "transfer_limit")
            current = loads(raw.decode("utf-8"))
            require_archive(current)
            require(current["metadata"]["project_id"] == expected["result"]["project_id"],
                    "Target now belongs to another project", "import_conflict")
    private_directory(target / ".aica/artifacts")
    for sha256, data in objects.items():
        path = target / ".aica/artifacts" / sha256
        require(not path.is_symlink(), "Restored artifacts cannot be symlinks", "unsafe_location")
        if path.exists():
            require(path.is_file() and file_hash(path) == sha256,
                    "Restored artifact has different content; it will not be replaced", "artifact_conflict")
        else:
            atomic_write(path, data)
    if not archive.exists():
        atomic_write(archive, content)
    if not already_imported:
        state["state"] = "complete"
        atomic_write(marker, dumps(state).encode("utf-8"))
    return {**state["result"], "already_imported": already_imported,
            "recording_database_present": (target / ".aica/recording.sqlite3").is_file()}


def import_archive(project, path, target, request_id, confirm_import):
    require(confirm_import is True, "Local import requires explicit user approval", "not_authorized")
    project = Path(project).resolve(strict=True)
    source = project_file(project, path)
    require(source.relative_to(project).parts[0].lower() not in {".aica", ".git"},
            "Select an exported JSON outside private recorder and Git state", "unsafe_location")
    destination = import_target(project, target)
    require(not source.is_relative_to(destination), "Import source must be outside its target", "unsafe_location")
    document, objects, raw = read_portable_json(source)
    source_sha256 = content_hash(raw)
    del raw
    restored = copy.deepcopy(document)
    restored.setdefault("extensions", {}).pop(EMBEDDED_EXTENSION, None)
    for artifact in restored["artifacts"]:
        location = ".aica/artifacts/" + artifact["sha256"]
        if location != artifact["uri"] and location not in artifact.get("locations", []):
            artifact.setdefault("locations", []).append(location)
    require_archive(restored)
    content = dumps(restored, pretty=True).encode("utf-8")
    require(len(content) <= ARCHIVE_LIMIT, "Restored archive metadata exceeds 16 MiB", "transfer_limit")
    relative = destination.relative_to(project).as_posix()
    result = {"status": "success", "project": relative, "archive_path": relative + "/aica.json",
              "request_id": request_id, "source_sha256": source_sha256,
              "project_id": restored["metadata"]["project_id"], "source_snapshot_id": document["snapshot"]["snapshot_id"],
              "node_count": len(restored["graph"]["nodes"]), "artifact_count": len(restored["artifacts"]),
              "object_count": len(objects), "restored_bytes": sum(len(data) for data in objects.values()),
              "recording_started_by_import": False, "executed": False, "requires_new_recording_consent": True,
              "next_step": "Open this project directory as the agent workspace and reconnect aica-mcp; request fresh consent before recording."}
    expected = {"format": "aica-local-import/0.1", "request_id": request_id,
                "fingerprint": digest([source.relative_to(project).as_posix(), relative, source_sha256]),
                "archive_sha256": content_hash(content), "result": result}
    directory = project / ".aica/outboxes/archive-imports"
    private_directory(project / ".aica")
    private_directory(project / ".aica/outboxes")
    private_directory(directory)
    lock = ProjectLock(directory / (digest(relative) + ".lock"))
    lock.acquire()
    try:
        result = restore_project(destination, content, objects, expected)
    except OSError as error:
        raise ProtocolError("Local import interrupted; retry with the same source, target and request_id",
                            "import_interrupted", [{"target": relative, "reason": str(error)}]) from error
    finally:
        lock.close()
    return result
