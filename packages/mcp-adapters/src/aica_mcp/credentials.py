import os
import time
from pathlib import Path

from aica_schema.protocol import digest, dumps, loads, require
from aica_schema.recording import require_project_connection

from .files import atomic_write, private_directory


class Credentials:
    def __init__(self, project, stream_id):
        self.project = Path(project).resolve()
        self.stream_id = stream_id

    def identity(self, config):
        value = digest([str(self.project), self.stream_id, config["server_url"], config["project_id"]])
        return value

    def path(self, config):
        directory = Path(os.environ.get("AICA_CREDENTIALS_DIR", str(Path.home() / ".aica" / "credentials"))).expanduser()
        require(directory.is_absolute(), "Credential directory must be absolute")
        require(not directory.resolve().is_relative_to(self.project), "Credentials must be stored outside the workspace")
        path = directory / (self.identity(config) + ".json")
        require(not directory.is_symlink() and not directory.parent.is_symlink() and not path.is_symlink(),
                "Credential store cannot be a symlink")
        return path

    def read(self, config):
        path = self.path(config)
        if not path.exists():
            return None
        require(os.name == "nt" or path.stat().st_mode & 0o077 == 0, "Credential file must be private", "invalid_credential")
        require(path.stat().st_size <= 16384, "Credential file exceeds its size limit", "invalid_credential")
        value = loads(path.read_text(encoding="utf-8"))
        require_project_connection(value)
        require(value["identity"] == self.identity(config), "Credentials belong to another project", "invalid_credential")
        return value

    def write(self, config, value):
        require_project_connection(value)
        require(value["identity"] == self.identity(config), "Credentials belong to another project", "invalid_credential")
        path = self.path(config)
        private_directory(path.parent)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)
        atomic_write(path, dumps(value).encode("utf-8"))

    def delete(self, config):
        self.path(config).unlink(missing_ok=True)

    def token(self, config, login=False):
        value = self.read(config)
        require(value is not None, "Connect this project first", "credential_missing")
        prefix = "login" if login else "upload"
        require(value[prefix + "_expires_at"] > time.time(), "Platform credentials expired; run aica connect again",
                "credential_expired")
        return value[prefix + "_token"]
