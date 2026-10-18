import argparse
import asyncio
import copy
import hashlib
import ipaddress
import os
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from contextlib import AsyncExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from aica_mcp.client import RecorderClient
from aica_mcp.transfer import build_bundle, upload_bundle
from aica_schema.protocol import dumps, loads, new_id, require, utc_now
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROMPT = ("Use case: illustration-story\nAsset type: AiCA real workflow test image\n"
          "Primary request: A black cat astronaut standing on the Moon, with Earth in the background.\n"
          "Style/medium: warm watercolor illustration.\n"
          "Composition/framing: one square image, full cat visible.\n"
          "Constraints: no text, no logos, no watermark.")


def save(path, value):
    path.write_text(dumps(value, pretty=True) + "\n", encoding="utf-8")


class Host:
    def __init__(self, session, root):
        self.session = session
        self.path = root / "private-state.json"
        self.state = loads(self.path.read_text()) if self.path.exists() else {"source_id": new_id(), "seq": 0}

    async def call(self, name, arguments):
        response = await self.session.call_tool(name, arguments)
        result = response.structured_content
        require(result is not None and not response.is_error and result["status"] == "success",
                "MCP call failed: " + name)
        return result

    async def event(self, kind, payload, agent=True, capture=None, **extra):
        state = self.state
        await self.call("aica_check_consent", {"session_id": state["session_id"],
                                              "session_token": state["session_token"], "event_type": kind})
        state["seq"] += 1
        event = {"event_id": new_id(), "source_id": state["source_id"], "source_seq": state["seq"],
                 "session_id": state["session_id"], "agent_id": state.get("agent_id") if agent else None,
                 "type": kind, "timestamp": utc_now(), "causal_parents": [], "payload": payload, **extra}
        save(self.path, state)
        arguments = {"session_token": state["session_token"], "event": event, **(capture or {})}
        result = await self.call("aica_capture_artifact" if capture else "aica_submit_event", arguments)
        with (self.path.parent / "mcp-receipts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(dumps({"event": event, "receipt": result}) + "\n")
        return result


async def record(root, stage, image):
    source = root / "source"
    source.mkdir(exist_ok=True)
    async with AsyncExitStack() as stack:
        streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
            command=sys.executable, args=["-m", "aica_mcp", "serve", "--project", str(source)])))
        session = await stack.enter_async_context(ClientSession(*streams))
        await session.initialize()
        host = Host(session, root)
        state = host.state
        if stage == "begin":
            require(not host.path.exists(), "Existing recording preserved; do not begin again")
            tools = [item.name for item in (await session.list_tools()).tools]
            consent = await host.call("aica_start_session", {"request_id": new_id(), "record": True})
            state.update(session_id=consent["session_id"], session_token=consent["session_token"],
                         agent_id=new_id(), harness_id=new_id(), node_id=new_id(), run_id=new_id())
            save(host.path, state)
            await host.event("harness.registered", {
                "id": state["harness_id"], "name": "Codex explicitly forwarded real image generation",
                "version": "0.1.0", "config": {"capture_adapter": "MCP stdio host forwarding",
                    "automatic_hook": False, "image_tool": "image_gen.imagegen"}, "state": {},
                "availability": {"adapter": "recorded", "host_system_instructions": "missing"}}, agent=False)
            await host.event("agent.registered", {"id": state["agent_id"], "session_id": state["session_id"],
                "parent_agent_id": None, "harness_id": state["harness_id"], "client": "codex", "model": {}}, agent=False)
            await host.event("message.recorded", {"role": "user", "content":
                "实际测试图像生成任务经 MCP 录制、语义解析进入数据库，千万不要编任何结果。"})
            archive = (await host.call("aica_get_archive", {}))["archive"]
            graph = copy.deepcopy(archive["graph"])
            branch = next(item["id"] for item in graph["branches"] if item["state"] == "active")
            graph["nodes"].append({"id": state["node_id"], "kind": "tool", "label": "image_gen.imagegen",
                "branch_id": branch, "description": "真实生成黑猫宇航员水彩图；Host 显式转交，非自动监听。",
                "provenance": {"mode": "recorded", "evidence": [], "missing": ["portable_image_tool_binding"]},
                "execution": {"kind": "manual", "tool": "image_gen.imagegen", "version": None,
                    "inputs": {"prompt": {"kind": "literal", "value": PROMPT},
                               "transparent_background": {"kind": "literal", "value": False}},
                    "params": {}, "outputs": ["image"]}})
            revision = await host.event("definition.replaced", {"graph": graph, "variables": archive["variables"],
                "interactive_questions": archive["interactive_questions"]},
                expected_revision=archive["graph"]["revision_id"], branch_id=branch)
            state["revision_id"] = revision["revision_id"]
            state["started_at"] = utc_now()
            await host.event("tool.started", {"id": state["run_id"], "trace_id": new_id(),
                "node_id": state["node_id"], "revision_id": state["revision_id"], "session_id": state["session_id"],
                "agent_id": state["agent_id"], "harness_id": state["harness_id"], "state": "running",
                "started_at": state["started_at"], "inputs": {"prompt": PROMPT, "transparent_background": False},
                "outputs": {}, "artifact_ids": [], "provenance": {"mode": "recorded", "evidence": []}})
            save(root / "prompt.json", {"prompt": PROMPT, "transparent_background": False})
            save(root / "mcp-installation.json", {"python": sys.executable, "entrypoint": "aica_mcp serve",
                "transport": "stdio", "initialize_succeeded": True, "tools": tools, "automatic_hook": False})
            print(dumps({"stage": "recording_started", "tool_count": len(tools), "run_id": state["run_id"]}))
        else:
            require(image is not None and image.is_file(), "Actual generated image path is required")
            require(not state.get("completed"), "Completed recording preserved")
            content = image.read_bytes()
            require(content.startswith(b"\x89PNG\r\n\x1a\n"), "Generated artifact must be an actual PNG")
            assets = source / "assets"
            assets.mkdir(exist_ok=True)
            target = assets / "black-cat-astronaut.png"
            require(not target.exists(), "Existing image preserved")
            shutil.copyfile(image, target)
            captured = await host.event("artifact.recorded", {}, capture={"path": "assets/black-cat-astronaut.png",
                "mime_type": "image/png", "producer_node_id": state["node_id"], "producer_run_id": state["run_id"]})
            artifact = captured["artifact"]
            require(artifact["sha256"] == hashlib.sha256(content).hexdigest(), "MCP image hash mismatch")
            await host.event("tool.finished", {"run_id": state["run_id"], "state": "succeeded", "ended_at": utc_now(),
                "outputs": {"image_artifact_id": artifact["id"], "observed": "image_gen returned a local PNG"},
                "artifact_ids": [artifact["id"]]})
            await host.event("session.closed", {})
            exported = await host.call("aica_export_archive", {})
            state.update(completed=True, image_artifact_id=artifact["id"], image_sha256=artifact["sha256"])
            save(host.path, state)
            save(root / "recording-proof.json", {"artifact": artifact, "run_id": state["run_id"],
                "snapshot": exported["snapshot"], "automatic_hook": False, "actual_image_tool_executed": True})
            print(dumps({"stage": "recording_completed", "image_sha256": artifact["sha256"], "bytes": len(content)}))


@contextmanager
def serve(root, token):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AiCA real image loopback")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(identity).issuer_name(identity).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
    cert_path, key_path = root / "certificate.pem", root / "private-tls-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    trust = ssl.create_default_context(cafile=str(cert_path))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "AICA_DATA_DIR": str(root / "platform"),
                   "AICA_API_TOKENS": dumps({"live-image-user": token})}
    with (root / "gateway.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "aica_gateway.app:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--ssl-keyfile", str(key_path),
            "--ssl-certfile", str(cert_path), "--log-level", "warning"], env=environment, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            base = f"https://127.0.0.1:{port}"
            with httpx.Client(verify=trust, trust_env=False, timeout=90,
                              headers={"Authorization": "Bearer " + token}) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        if client.get(base + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    require(process.poll() is None and time.monotonic() < deadline, "HTTPS gateway failed to start")
                    time.sleep(0.1)
                yield base, client, trust
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def response_json(response, expected=200):
    require(response.status_code == expected,
            f"Gateway failed (HTTP {response.status_code}): {response.text[:500]}")
    return response.json()


def platform(root):
    state = loads((root / "private-state.json").read_text())
    require(state.get("completed"), "Real image recording is not complete")
    require(not (root / "analysis-request.json").exists(), "Existing model attempt preserved; inspect it before retrying")
    transfer = build_bundle(root / "source", root / "recording.zip")
    save(root / "bundle-verification.json", transfer)
    token = secrets.token_urlsafe(32)
    with serve(root, token) as (base, client, trust):
        created = response_json(client.post(base + "/api/projects", json={"title": "真实黑猫宇航员生图实测"}), 201)
        project = created["project"]["id"]
        url = base + f"/api/projects/{project}"
        receipt = upload_bundle(root / "recording.zip", url + "/imports", token, True,
                                httpx.HTTPTransport(verify=trust))["receipt"]
        save(root / "import-receipt.json", receipt)
        before = response_json(client.get(url + "/archive"))["archive"]
        prepared = response_json(client.get(url + "/analysis-input", params={"import_id": receipt["import_id"]}))["input"]
        save(root / "analysis-input.json", prepared)
        payload = {"request_id": new_id(), "import_id": receipt["import_id"],
                   "input_sha256": prepared["input_sha256"], "allow_external_processing": True}
        save(root / "analysis-request.json", payload)
        analyzed = client.post(url + "/analyses", json=payload)
        save(root / "analysis-http-result.json", {"http_status": analyzed.status_code, "body": analyzed.json()})
        analysis = response_json(analyzed, 201)["analysis"]
        save(root / "analysis-result.json", analysis)
        readback = response_json(client.get(url + "/analyses/" + analysis["id"]))["analysis"]
        require(readback == analysis, "Saved API analysis differs")
        repeated = response_json(client.post(url + "/analyses", json=payload))["analysis"]
        require(repeated == analysis, "Idempotent retry differs")
        picture = client.get(url + "/artifacts/" + state["image_artifact_id"])
        require(picture.status_code == 200 and hashlib.sha256(picture.content).hexdigest() == state["image_sha256"],
                "Platform image bytes differ")
        require(response_json(client.get(url + "/archive"))["archive"] == before, "Analysis changed original facts")
    database_path = root / "platform/platform.sqlite3"
    with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        integrity = database.execute("PRAGMA integrity_check").fetchone()[0]
        row = dict(database.execute("SELECT id,project_id,import_id,subject,request_id,state,result,error_code "
                                    "FROM semantic_analyses WHERE id=?", (analysis["id"],)).fetchone())
        stored_report = loads(row.pop("result"))
        require(row["state"] == "succeeded" and stored_report == analysis["result"], "SQLite result differs")
        counts = {table: database.execute("SELECT count(*) FROM " + table).fetchone()[0]
                  for table in ("projects", "imports", "receipts", "semantic_analyses")}
    with serve(root, token) as (base, client, trust):
        after_restart = response_json(client.get(base + f"/api/projects/{project}/analyses/" + analysis["id"]))["analysis"]
        require(after_restart == analysis, "Analysis did not survive gateway restart")
        picture = client.get(base + f"/api/projects/{project}/artifacts/" + state["image_artifact_id"])
        require(picture.status_code == 200 and hashlib.sha256(picture.content).hexdigest() == state["image_sha256"],
                "Image did not survive gateway restart")
    report = analysis["result"]
    proof = {"mode": "real_mcp_real_image_real_deepseek", "tested_at": utc_now(), "automatic_hook": False,
        "actual_image_tool_executed": True, "project_id": project, "import_id": receipt["import_id"],
        "analysis_id": analysis["id"], "image_sha256": state["image_sha256"], "model": report["engine"]["model"],
        "usage": report["usage"], "table_counts": counts, "sqlite_integrity": integrity, "sqlite_row": row,
        "api_equals_sqlite": True, "restart_readback_equal": True, "idempotent_retry_equal": True,
        "original_archive_unchanged": True, "steps": len(report["analysis"]["steps"]),
        "variables": len(report["analysis"]["variables"]), "golden_path": report["analysis"]["golden_path"]["node_ids"]}
    save(root / "verification.json", proof)
    print(dumps(proof, pretty=True))


def main():
    parser = argparse.ArgumentParser(description="Explicit host forwarding of an actual image task through stdio MCP")
    parser.add_argument("stage", choices=["begin", "complete", "platform"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--confirm-record", action="store_true")
    parser.add_argument("--confirm-external", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    require(root.is_dir(), "Create a private run directory outside the checkout first")
    checkout = Path(__file__).resolve().parents[3]
    require(not root.is_relative_to(checkout), "Keep recordings and credentials outside the checkout")
    if arguments.stage == "platform":
        require(arguments.confirm_external, "Confirm sending this test's reviewed facts to DeepSeek")
        platform(root)
    else:
        require(arguments.confirm_record, "Explicit recording consent is required")
        asyncio.run(record(root, arguments.stage, arguments.image))
        if arguments.stage == "complete":
            client = RecorderClient(root / "source")
            client.load_endpoint()
            client.request("shutdown")


if __name__ == "__main__":
    main()
