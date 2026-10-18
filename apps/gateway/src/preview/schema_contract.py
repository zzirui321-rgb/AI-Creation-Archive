from __future__ import annotations

import importlib.util
import json
import subprocess
import types
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .contracts import PreviewError

SCHEMA_PATH = "packages/schema/src/aica_schema/aica.schema.json"
SCHEMA_COMMIT = "f0fc2942d517c793447140d93670cf5428164a83"


@lru_cache(maxsize=4)
def load_schema(path: str | None = None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    package = importlib.util.find_spec("aica_schema")
    if package and package.origin:
        candidate = Path(package.origin).with_name("aica.schema.json")
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[4]
    candidate = root / SCHEMA_PATH
    if candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    # Before A is merged, consume its pinned Git object without copying or changing A's files.
    try:
        text = subprocess.check_output(
            ["git", "show", f"{SCHEMA_COMMIT}:{SCHEMA_PATH}"], cwd=root, stderr=subprocess.PIPE
        )
        return json.loads(text)
    except (OSError, subprocess.CalledProcessError) as error:
        raise PreviewError("schema_unavailable", "Install A's schema package or provide schema_path") from error


class ProtocolContract:
    def __init__(self, schema_path: str | None = None):
        self.schema = load_schema(schema_path)

    def validate(self, definition: str, value: Any) -> None:
        validator = Draft202012Validator({"$ref": f"#/$defs/{definition}", "$defs": self.schema["$defs"]})
        errors = sorted(validator.iter_errors(value), key=lambda item: str(item.path))
        if errors:
            error = errors[0]
            raise PreviewError("invalid_protocol", f"{definition}/{'/'.join(map(str, error.path))}: {error.message}")


@lru_cache(maxsize=1)
def load_validation_protocol():
    if importlib.util.find_spec("aica_schema"):
        from aica_schema import protocol

        return protocol
    root = Path(__file__).resolve().parents[4]
    relative = "packages/schema/src/aica_schema/protocol.py"
    local = root / relative
    source = (
        local.read_bytes()
        if local.is_file()
        else subprocess.check_output(["git", "show", f"{SCHEMA_COMMIT}:{relative}"], cwd=root, stderr=subprocess.PIPE)
    )
    module = types.ModuleType("aica_shared_validation")
    exec(compile(source, relative, "exec"), module.__dict__)
    module.archive_schema = load_schema
    return module
