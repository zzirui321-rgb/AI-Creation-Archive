import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from aica_schema.protocol import ProtocolError, require

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def private_directory(path):
    require(not path.is_symlink(), f"Internal directory cannot be a symlink: {path}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir(), f"Not a directory: {path}")


def file_hash(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    value = hasher.hexdigest()
    return value


def atomic_write(path, data):
    require(not path.is_symlink(), f"Refusing to replace symlink: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def project_file(project, name):
    candidate = Path(name)
    require(not candidate.is_absolute(), "File paths must be relative to the configured project")
    require(".." not in candidate.parts, "Parent traversal is not allowed")
    path = (project / candidate).resolve()
    require(path.is_relative_to(project), "File escapes the configured project")
    require(path.is_file(), f"File does not exist: {name}")
    return path


def artifact_locations(artifact):
    locations = list(dict.fromkeys([artifact["uri"], *artifact.get("locations", [])]))
    return locations


def artifact_file(project, uri):
    candidate = Path(uri)
    require(not candidate.is_absolute() and ".." not in candidate.parts and "\\" not in uri,
            "Artifact location must be a portable project-relative path", "unsafe_location")
    path = (project / candidate).resolve()
    require(path.is_relative_to(project), "Artifact location escapes the project", "unsafe_location")
    internal = project / ".aica"
    require(not path.is_relative_to(internal) or path.is_relative_to(internal / "artifacts")
            or path.is_relative_to(internal / "imports"), "Artifact location points to private recorder state",
            "unsafe_location")
    return path


def inspect_location(project, artifact, uri, max_bytes=None):
    try:
        if urlsplit(uri).scheme:
            return {"uri": uri, "state": "unverified", "message": "External locations are not fetched"}
        path = artifact_file(project, uri)
        if not path.is_file():
            return {"uri": uri, "state": "missing"}
        size = path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            return {"uri": uri, "state": "unverified", "message": "Artifact exceeds the read limit", "size": size}
        if artifact["sha256"] is None:
            return {"uri": uri, "state": "unverified", "message": "No expected content hash", "size": size}
        content_hash = file_hash(path)
        matches = content_hash == artifact["sha256"] and ("size" not in artifact or size == artifact["size"])
        return {"uri": uri, "state": "verified" if matches else "mismatch", "sha256": content_hash, "size": size}
    except (OSError, ValueError) as error:
        return {"uri": uri, "state": "blocked", "message": str(error)}


def inspect_artifact(project, artifact, max_bytes=None):
    locations = [inspect_location(project, artifact, uri, max_bytes) for uri in artifact_locations(artifact)]
    states = {location["state"] for location in locations}
    state = next(value for value in ("verified", "unverified", "mismatch", "blocked", "missing") if value in states)
    return {"artifact_id": artifact["id"], "sha256": artifact["sha256"],
            "declared_availability": artifact["availability"], "state": state, "locations": locations}


def read_artifact(project, artifact, max_bytes=16 * 1024 * 1024):
    report = inspect_artifact(project, artifact, max_bytes)
    for location in report["locations"]:
        if location["state"] == "verified" and location["size"] <= max_bytes:
            path = artifact_file(project, location["uri"])
            with path.open("rb") as stream:
                content = stream.read(location["size"] + 1)
            require(len(content) <= max_bytes and hashlib.sha256(content).hexdigest() == artifact["sha256"]
                    and ("size" not in artifact or len(content) == artifact["size"]),
                    "Artifact changed while reading", "artifact_conflict")
            return content
    raise ProtocolError("No verified local artifact within the read limit", "artifact_unavailable", [report])


class ProjectLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def acquire(self):
        require(not self.path.is_symlink(), "Lock file cannot be a symlink")
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            self.handle = None
            raise ProtocolError("Project recorder is already running", "project_busy") from error

    def close(self):
        if self.handle is not None:
            if os.name == "nt":
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
