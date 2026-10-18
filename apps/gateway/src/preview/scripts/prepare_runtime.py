"""Isolate the execution Runtime from D's newer, incompatible graph parser package."""

import argparse
import hashlib
import io
import shutil
import subprocess
import zipfile
from pathlib import Path

BASELINE = "6b98aa4"
RUNTIME_A_BASELINE = "a0cc735"
PATHS = ["packages/schema/src", "packages/mcp-adapters/src", "packages/runtime/src", "apps/gateway/src"]


def prepare(root: Path, destination: Path):
    bundled = root / "compat/execution-worker"
    if (bundled / "SOURCE.json").is_file():
        files = sorted(path for path in bundled.rglob("*") if path.is_file()
                       and "__pycache__" not in path.parts and path.suffix != ".pyc")
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(bundled).as_posix().encode())
            digest.update(path.read_bytes())
        identity = "bundled-worker-v1\n" + digest.hexdigest()
        marker = destination / ".aica-runtime-source"
        if marker.is_file() and marker.read_text() == identity:
            return
        if destination.exists() and any(destination.iterdir()):
            raise RuntimeError("Runtime source directory is not empty; choose a new destination")
        shutil.copytree(bundled, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        marker.write_text(identity)
        return
    patch = root / "packages/artifact-renderers/handoff/integration/d-runtime.patch"
    provider_patch = root / "packages/artifact-renderers/handoff/integration/d-request-providers.patch"
    revision = subprocess.check_output(["git", "rev-parse", BASELINE], cwd=root, text=True).strip()
    identity = revision + "\n" + hashlib.sha256(patch.read_bytes() + provider_patch.read_bytes()).hexdigest()
    identity += "\n" + subprocess.check_output(["git", "rev-parse", RUNTIME_A_BASELINE], cwd=root, text=True).strip()
    marker = destination / ".aica-runtime-source"
    if marker.is_file() and marker.read_text() == identity:
        return
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError("Runtime source directory is not empty; choose a new destination")
    destination.mkdir(parents=True, exist_ok=True)
    content = subprocess.check_output(["git", "archive", "--format=zip", revision, *PATHS], cwd=root)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(destination)
    content = subprocess.check_output(
        [
            "git",
            "archive",
            "--format=zip",
            RUNTIME_A_BASELINE,
            "packages/schema/src",
            "packages/mcp-adapters/src",
        ],
        cwd=root,
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(destination)
    subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=destination, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=destination, check=True)
    subprocess.run(["git", "apply", "--check", str(provider_patch)], cwd=destination, check=True)
    subprocess.run(["git", "apply", str(provider_patch)], cwd=destination, check=True)
    marker.write_text(identity)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parents[5], args.destination.resolve())
