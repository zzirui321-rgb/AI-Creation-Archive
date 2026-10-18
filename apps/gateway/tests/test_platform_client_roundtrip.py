import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_real_http_c_client_to_platform(tmp_path, bundle_factory):
    node = shutil.which("node")
    assert node is not None, "Use the existing team environment with Node.js"
    root = Path(__file__).resolve().parents[3]
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "AICA_DATA_DIR": str(tmp_path / "server"), "AICA_API_TOKENS": json.dumps({}),
                   "PYTHONPATH": os.pathsep.join(str(root / entry) for entry in (
                       "apps/gateway/src", "packages/schema/src", "packages/mcp-adapters/src", "packages/runtime/src"))}
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "aica_gateway.app:create_app", "--factory",
                                "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
                               cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=2) as client:
            while True:
                try:
                    if client.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None and time.monotonic() < deadline, "Platform test server failed to start"
                time.sleep(0.05)
        bundle = bundle_factory()
        result = subprocess.run([node, "apps/gateway/integration/platform-roundtrip.mjs", base, str(bundle["path"])],
                                cwd=root, env=environment, capture_output=True, text=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Real HTTP platform client roundtrip passed" in result.stdout
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
