import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from aica_mcp.transfer import upload_bundle
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def test_real_https_upload_from_a_client_and_independent_image_download(tmp_path, bundle_factory, workflow):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AiCA test localhost")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
    certificate_path = tmp_path / "certificate.pem"
    key_path = tmp_path / "private-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)
    context = ssl.create_default_context(cafile=str(certificate_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(str(root / path) for path in (
        "apps/gateway/src", "packages/runtime/src", "packages/schema/src", "packages/mcp-adapters/src"))
    token = "local-https-test-platform-token-123456"
    environment["AICA_API_TOKENS"] = json.dumps({"tls-user": token})
    environment["AICA_DATA_DIR"] = str(tmp_path / "remote-server")
    command = [sys.executable, "-m", "uvicorn", "aica_gateway.app:create_app", "--factory", "--host", "127.0.0.1",
               "--port", str(port), "--ssl-keyfile", str(key_path), "--ssl-certfile", str(certificate_path),
               "--log-level", "error"]
    process = subprocess.Popen(command, env=environment, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base = f"https://127.0.0.1:{port}"
    try:
        with httpx.Client(verify=context, trust_env=False, timeout=2,
                          headers={"Authorization": "Bearer " + token}) as first:
            ready = False
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and process.poll() is None and not ready:
                try:
                    ready = first.get(base + "/health").status_code == 200
                except httpx.HTTPError:
                    time.sleep(0.05)
            assert ready, "HTTPS server did not start"
            response = first.post(base + "/api/projects", json={"title": "HTTPS integration"})
            assert response.status_code == 201, response.text
            project_id = response.json()["project"]["id"]
        path, report, image, document = bundle_factory(workflow)
        destination = base + f"/api/projects/{project_id}/imports"
        receipts = []
        for attempt in range(2):
            result = upload_bundle(path, destination, token, confirm_upload=True,
                                   transport=httpx.HTTPTransport(verify=context))
            receipts.append(result["receipt"])
        assert receipts[0] == receipts[1]
        shutil.rmtree(path.parent / "source-1")
        with httpx.Client(verify=context, trust_env=False, headers={"Authorization": "Bearer " + token}) as second:
            response = second.get(base + f"/api/projects/{project_id}/artifacts/picture")
            assert response.content == image and response.headers["content-type"] == "image/png"
            graph = second.get(base + f"/api/projects/{project_id}/graph")
            assert graph.json()["graph"] == document["graph"]
            assert len(second.get(base + f"/api/projects/{project_id}/archives").json()["archives"]) == 1
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
