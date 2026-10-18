import argparse
import ctypes
import hashlib
import io
import os
import secrets
import zipfile


def attach_transfer(app):
    from aica_schema.protocol import dumps, loads, require
    from fastapi import Header, Response

    tokens = loads(os.environ["AICA_API_TOKENS"])

    @app.get("/api/projects/{project_id}/transfer")
    def transfer(project_id: str, archive_id: str, authorization: str = Header(default="")):
        require(
            authorization.startswith("Bearer ") and len(authorization) <= 519,
            "Platform authentication required",
            "unauthenticated",
        )
        candidate = hashlib.sha256(authorization.removeprefix("Bearer ").encode()).digest()
        subject = next(
            (
                user
                for user, token in tokens.items()
                if secrets.compare_digest(candidate, hashlib.sha256(token.encode()).digest())
            ),
            None,
        )
        require(subject is not None, "Platform authentication required", "unauthenticated")
        store = app.state.store
        archive = store.read_archive(subject, project_id, archive_id)["archive"]
        raw = dumps(archive).encode("utf-8")
        manifest = {
            "format": "aica-transfer/0.1",
            "archive": {"path": "aica.json", "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)},
            "artifacts": [],
        }
        buffer = io.BytesIO()
        stored = set()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("aica.json", raw)
            total = len(raw)
            for artifact in archive["artifacts"]:
                data = store.read_blob(project_id, artifact["sha256"])
                require(
                    hashlib.sha256(data).hexdigest() == artifact["sha256"], "Artifact hash mismatch", "storage_corrupt"
                )
                require(len(data) <= 64 * 1024 * 1024, "Artifact exceeds transfer budget", "transfer_limit")
                path = "objects/" + artifact["sha256"]
                manifest["artifacts"].append(
                    {"artifact_id": artifact["id"], "path": path, "sha256": artifact["sha256"], "size": len(data)}
                )
                if path not in stored:
                    total += len(data)
                    require(total <= 510 * 1024 * 1024, "Bundle exceeds transfer budget", "transfer_limit")
                    bundle.writestr(path, data)
                    stored.add(path)
            bundle.writestr("manifest.json", dumps(manifest).encode("utf-8"))
        content = buffer.getvalue()
        return Response(
            content,
            media_type="application/vnd.aica.bundle+zip",
            headers={
                "X-AiCA-SHA256": hashlib.sha256(content).hexdigest(),
                "Content-Disposition": 'attachment; filename="archive.aica.zip"',
                "Cache-Control": "private, no-store",
            },
        )


def main():
    dll = os.environ.get("AICA_TEST_SQLITE_DLL")
    if dll:
        ctypes.WinDLL(dll)
    import uvicorn
    from aica_gateway.app import create_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--service", choices=["runtime", "parser"], required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.service == "runtime":
        from aica_runtime.executors import default_registry
        from preview.runtime_bridge import register_preview_tools

        app = create_app(args.data_dir, registry=register_preview_tools(default_registry()))
        attach_transfer(app)
        from aica_schema.protocol import loads
        from preview.user_previews import attach_user_previews

        attach_user_previews(app, args.data_dir, loads(os.environ["AICA_API_TOKENS"]))
    else:
        app = create_app(args.data_dir)
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
