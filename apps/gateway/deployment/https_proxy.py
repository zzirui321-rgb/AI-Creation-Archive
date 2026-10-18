import argparse
import asyncio
import ipaddress
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def certificates(directory, address):
    directory.mkdir(parents=True, exist_ok=True)
    ca, cert, key = (directory / name for name in ("ca.pem", "server.pem", "server.key"))
    if cert.exists() and key.exists() and ca.exists():
        return cert, key
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AiCA Local LAN CA")])
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .sign(ca_key, hashes.SHA256()))
    san = x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                       x509.IPAddress(ipaddress.ip_address(address))])
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)]))
            .issuer_name(name).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=180))
            .add_extension(san, False).add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .sign(ca_key, hashes.SHA256()))
    ca.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    return cert, key


async def serve(args):
    cert, key = certificates(args.certificates, args.address)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)

    async def relay(reader, writer):
        try:
            while chunk := await reader.read(65536):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()

    async def connection(reader, writer):
        upstream = None
        try:
            remote, upstream = await asyncio.open_connection("127.0.0.1", args.upstream)
            await asyncio.gather(relay(reader, upstream), relay(remote, writer))
        except (OSError, ConnectionError, ssl.SSLError):
            writer.close()
            if upstream:
                upstream.close()

    server = await asyncio.start_server(connection, "0.0.0.0", args.port, ssl=context)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--certificates", required=True, type=Path)
    parser.add_argument("--address", required=True)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--upstream", type=int, default=8000)
    asyncio.run(serve(parser.parse_args()))
