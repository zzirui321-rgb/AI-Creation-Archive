"""Run the integrated platform with repository-local modules and external data."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    web = root / "apps/web/dist"
    if not (web / "index.html").is_file():
        parser.error("Build apps/web before starting the platform")
    default_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "AI-Creation-Archive"
    data = (args.data_dir or default_data).resolve()
    if data.is_relative_to(root):
        parser.error("Keep application data outside the repository")
    environment = dict(os.environ)
    environment.update(AICA_DATA_DIR=str(data), AICA_WEB_DIR=str(web),
                       AICA_EXECUTION_SOURCES=str(root / "compat/execution-worker"),
                       PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    environment.setdefault("AICA_API_TOKENS", "{}")
    environment.setdefault("AICA_CORS_ORIGINS", "[]")
    environment["PYTHONPATH"] = os.pathsep.join(str(root / path) for path in (
        "apps/gateway/src", "packages/runtime/src", "packages/schema/src", "packages/mcp-adapters/src"))
    return subprocess.call([sys.executable, "-m", "uvicorn", "aica_gateway.app:create_app", "--factory",
                            "--host", "127.0.0.1", "--port", str(args.port), "--workers", "1"],
                           cwd=root, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
