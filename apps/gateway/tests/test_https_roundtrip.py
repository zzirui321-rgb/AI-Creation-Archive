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
from conftest import TOKEN
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def test_real_tls_a_client_to_independent_platform_reader(tmp_path, bundle_factory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AiCA loopback test")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(identity).issuer_name(identity).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), True).sign(key, hashes.SHA256()))
    cert_path = tmp_path / "certificate.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_path.chmod(0o600)
    trust = ssl.create_default_context(cafile=str(cert_path))
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    root = Path(__file__).resolve().parents[3]
    environment = {**os.environ, "AICA_DATA_DIR": str(tmp_path / "server"),
                   "AICA_API_TOKENS": json.dumps({"alice": TOKEN}),
                   "PYTHONPATH": os.pathsep.join(str(root / entry) for entry in (
                       "apps/gateway/src", "packages/runtime/src", "packages/schema/src", "packages/mcp-adapters/src"))}
    command = [sys.executable, "-m", "uvicorn", "aica_gateway.app:create_app", "--factory", "--host", "127.0.0.1",
               "--port", str(port), "--ssl-keyfile", str(key_path), "--ssl-certfile", str(cert_path),
               "--log-level", "error"]
    process = subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    base = f"https://127.0.0.1:{port}"
    try:
        with httpx.Client(verify=trust, trust_env=False, timeout=2,
                          headers={"Authorization": "Bearer " + TOKEN}) as creator:
            deadline = time.monotonic() + 15
            while True:
                try:
                    if creator.get(base + "/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None and time.monotonic() < deadline, "TLS test server failed to start"
                time.sleep(0.05)
            project = creator.post(base + "/api/projects", json={"title": "TLS roundtrip"})
            assert project.status_code == 201, project.text
            project_id = project.json()["project"]["id"]
        bundle = bundle_factory()
        destination = base + f"/api/projects/{project_id}/imports"
        receipts = [upload_bundle(bundle["path"], destination, TOKEN, True, httpx.HTTPTransport(verify=trust))["receipt"]
                    for _ in range(2)]
        assert receipts[0] == receipts[1]
        shutil.rmtree(bundle["root"])
        with httpx.Client(verify=trust, trust_env=False, headers={"Authorization": "Bearer " + TOKEN}) as reader:
            response = reader.get(base + f"/api/projects/{project_id}/artifacts/output")
            assert response.status_code == 200 and response.content == bundle["content"]
            graph = reader.get(base + f"/api/projects/{project_id}/graph")
            assert graph.status_code == 200 and graph.json()["graph"] == bundle["archive"]["graph"]
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
