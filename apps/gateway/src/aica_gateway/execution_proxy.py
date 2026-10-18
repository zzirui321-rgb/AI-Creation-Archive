import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, HTTPException, Request, Response


class ExecutionWorkers:
    def __init__(self, directory):
        self.directory = directory / "execution-workers"
        self.workers = {}
        self.lock = asyncio.Lock()

    async def worker(self, subject):
        async with self.lock:
            existing = self.workers.get(subject)
            if existing and existing[0].poll() is None:
                return existing[1:]
            root = next(parent for parent in Path(__file__).resolve().parents
                        if (parent / "collaboration.json").is_file())
            sources = os.environ.get("AICA_EXECUTION_SOURCES") or str(root / "compat/execution-worker")
            if not sources or not Path(sources).is_dir():
                raise HTTPException(503, "Execution worker sources are not configured")
            data = self.directory / hashlib.sha256(subject.encode()).hexdigest()
            data.mkdir(parents=True, exist_ok=True)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            token = secrets.token_urlsafe(48)
            environment = {key: value for key, value in os.environ.items()
                           if not any(word in key.upper() for word in ("TOKEN", "SECRET", "API_KEY", "PASSWORD"))}
            environment["AICA_API_TOKENS"] = json.dumps({"local": token})
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(Path(sources) / path) for path in
                 ("apps/gateway/src", "packages/runtime/src", "packages/schema/src", "packages/mcp-adapters/src")]
                + [str(root / "apps/gateway/src")])
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PREVIEW_PROVIDER"] = "prewarmed_cache"
            script = root / "apps/gateway/src/preview/scripts/run_unified.py"
            with (data / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    [sys.executable, str(script), "--service", "runtime", "--data-dir", str(data / "data"),
                     "--port", str(port)], cwd=root, env=environment, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            (data / "worker-process.json").write_text(json.dumps({
                "pid": process.pid, "python": sys.executable,
                "started_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
            url = f"http://127.0.0.1:{port}"
            try:
                async with httpx.AsyncClient(trust_env=False) as client:
                    for _ in range(100):
                        if process.poll() is not None:
                            raise HTTPException(503, "Execution worker failed; inspect its server log")
                        try:
                            response = await client.get(url + "/health", timeout=1)
                            if response.status_code == 200:
                                self.workers[subject] = (process, url, token)
                                return url, token
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(0.2)
                raise HTTPException(503, "Execution worker startup timed out")
            except BaseException:
                if process.poll() is None:
                    process.terminate()
                raise

    def close(self):
        for process, _, _ in self.workers.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


def attach_execution(app, principal):
    @app.api_route("/runtime-api/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request, subject=Depends(principal)):
        if not subject.startswith("user:"):
            raise HTTPException(401, "Sign in with a platform account")
        if not path or any(part in {".", "..", ""} for part in path.split("/")):
            raise HTTPException(404, "Unknown execution route")
        url, token = await app.state.execution.worker(subject)
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() in {"content-type", "idempotency-key", "x-aica-bundle-sha256"}}
        headers["Authorization"] = "Bearer " + token
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=330) as client:
                response = await client.request(request.method, url + "/api/" + path,
                                                params=request.query_params, headers=headers,
                                                content=request.stream() if request.method == "POST" else None)
        except httpx.HTTPError:
            raise HTTPException(503, "Execution worker unavailable; no automatic model retry") from None
        return Response(response.content, status_code=response.status_code,
                        headers={key: value for key, value in response.headers.items()
                                 if key.lower() in {"content-type", "content-disposition", "x-aica-sha256"}},
                        media_type=None)
