import atexit
import ctypes
import importlib
import io
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from PIL import Image

root = Path(__file__).resolve().parents[5]
if os.name == "nt" and os.environ.get("AICA_TEST_SQLITE_DLL"):
    ctypes.WinDLL(os.environ["AICA_TEST_SQLITE_DLL"])

# The B branch keeps teammate directories untouched; integration tests use pinned sources in temporary storage.
bundled = root / "compat/execution-worker"
if (bundled / "packages/runtime/src/aica_runtime/engine.py").is_file():
    for path in reversed(["packages/schema/src", "packages/mcp-adapters/src", "packages/runtime/src", "apps/gateway/src"]):
        sys.path.insert(0, str(bundled / path))
    importlib.import_module("aica_gateway")
elif not (root / "packages/runtime/src/aica_runtime/engine.py").is_file():
    sources = tempfile.TemporaryDirectory(prefix="aica-b-test-contract-")
    atexit.register(sources.cleanup)
    paths = ["packages/schema/src", "packages/mcp-adapters/src", "packages/runtime/src", "apps/gateway/src"]
    content = subprocess.check_output(["git", "archive", "--format=zip", "6b98aa4", *paths], cwd=root)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(sources.name)
    content = subprocess.check_output(
        [
            "git",
            "archive",
            "--format=zip",
            "a0cc735",
            "packages/schema/src",
            "packages/mcp-adapters/src",
        ],
        cwd=root,
    )
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        archive.extractall(sources.name)
    patch = root / "packages/artifact-renderers/handoff/integration/d-runtime.patch"
    subprocess.run(["git", "init", "--quiet", sources.name], check=True, capture_output=True)
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=sources.name, check=True, capture_output=True)
    subprocess.run(["git", "apply", str(patch)], cwd=sources.name, check=True, capture_output=True)
    patch = root / "packages/artifact-renderers/handoff/integration/d-request-providers.patch"
    subprocess.run(["git", "apply", str(patch)], cwd=sources.name, check=True, capture_output=True)
    for path in reversed(paths):
        sys.path.insert(0, str(Path(sources.name) / path))
    # Keep pytest's later source-path prepending from selecting the newer parser Gateway.
    importlib.import_module("aica_gateway")

sys.path.append(str(Path(__file__).resolve().parents[2]))

from preview import ArtifactStore, ExecutionContext, PreviewEngine, PreviewSettings  # noqa: E402
from preview.schema_contract import load_validation_protocol  # noqa: E402


@pytest.fixture(scope="session")
def a_protocol():
    return load_validation_protocol()


@pytest.fixture
def node():
    return {
        "id": "image-node",
        "kind": "model",
        "label": "Concept study",
        "branch_id": "main",
        "provenance": {"mode": "recorded", "evidence": [{"source": "test"}]},
        "execution": {
            "kind": "model",
            "tool": "image_gen",
            "version": "1",
            "model": None,
            "inputs": {"prompt": {"kind": "literal", "value": "A geometric city at night"}},
            "params": {"width": 768, "height": 512},
            "outputs": ["image"],
        },
    }


@pytest.fixture
def context():
    return ExecutionContext(run_id="run-test", cache_key="base-key", seed=73)


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "artifacts", prewarmed_root=tmp_path / "prewarmed")


@pytest.fixture
def engine(store):
    return PreviewEngine(store, settings=PreviewSettings(provider="mock_svg"))


@pytest.fixture
def png_bytes():
    image = Image.new("RGB", (96, 64), "#b34477")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()
