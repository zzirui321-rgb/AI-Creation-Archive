"""Optional LAN listener for an existing loopback-only local image service."""

import argparse
import re

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def relay(path: str, request: Request):
    if path not in {"models", "images/generations", "txt2img", "img2img"} and not re.fullmatch(
        r"files/[A-Za-z0-9_.-]+", path
    ):
        raise HTTPException(404)
    data = bytearray()
    async for block in request.stream():
        data.extend(block)
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(413)
    async with httpx.AsyncClient(timeout=300, trust_env=False, follow_redirects=False) as client:
        upstream = await client.request(
            request.method,
            "http://127.0.0.1:8000/v1/" + path,
            content=bytes(data),
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=8000, log_level="warning")
