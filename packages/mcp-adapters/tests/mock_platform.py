import argparse
import hashlib
import os
import socket
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from aica_mcp.transfer import BUNDLE_LIMIT, CONTENT_TYPE, verify_bundle
from aica_schema.protocol import ProtocolError, dumps, loads, new_id

PROJECT_ID = "00000000-0000-4000-8000-000000000001"


class MockPlatform:
    def __init__(self, directory, token, port=0):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.token = token
        self.receipts = {}
        self.requests = []
        self.lose_next_ack = False
        self.server = None
        platform = self

        class Handler(BaseHTTPRequestHandler):
            def send_body(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def permitted(self):
                allowed = self.headers.get("Authorization") == "Bearer " + platform.token
                if not allowed:
                    self.send_body(401, b'{"status":"error","code":"unauthenticated"}')
                return allowed

            def do_POST(self):
                if not self.permitted():
                    return
                parts = urlsplit(self.path).path.split("/")
                if len(parts) != 5 or parts[1:3] != ["api", "projects"] or parts[4] != "imports":
                    self.send_body(404, b'{}')
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= BUNDLE_LIMIT or self.headers.get("Content-Type") != CONTENT_TYPE:
                    self.send_body(413, b'{}')
                    return
                content = self.rfile.read(size)
                content_hash = hashlib.sha256(content).hexdigest()
                key = (parts[3], content_hash)
                path = platform.directory / (content_hash + ".zip")
                path.write_bytes(content)
                try:
                    report = verify_bundle(path)
                    if (self.headers.get("Idempotency-Key") != "sha256:" + content_hash
                            or self.headers.get("X-AiCA-Bundle-SHA256") != content_hash):
                        raise ValueError("Hash headers differ")
                except (ProtocolError, ValueError):
                    self.send_body(422, b'{}')
                    return
                created = key not in platform.receipts
                if created:
                    platform.receipts[key] = {"status": "success", "format": "aica-transfer/0.1",
                                              "project_id": parts[3], "import_id": new_id(),
                                              "bundle_sha256": content_hash, "archive_sha256": report["archive_sha256"]}
                platform.requests.append(content_hash)
                if platform.lose_next_ack:
                    platform.lose_next_ack = False
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                else:
                    self.send_body(201 if created else 200, dumps(platform.receipts[key]).encode())

            def do_GET(self):
                if self.path == "/health":
                    self.send_body(200, b'{"status":"success","mode":"mock"}')
                    return
                if not self.permitted():
                    return
                parsed = urlsplit(self.path)
                parts = parsed.path.split("/")
                if parsed.path == "/api/projects":
                    self.send_body(200, dumps({"status": "success", "projects": [{"id": PROJECT_ID,
                                               "title": "Local upload contract fixture"}]}).encode())
                    return
                if len(parts) < 5 or parts[1:3] != ["api", "projects"]:
                    self.send_body(404, b'{}')
                    return
                receipts = [receipt for (project, content_hash), receipt in platform.receipts.items() if project == parts[3]]
                selected = parse_qs(parsed.query).get("import_id", [None])[0]
                matches = [receipt for receipt in receipts if selected is None or receipt["import_id"] == selected]
                if parts[4] == "archives":
                    self.send_body(200, dumps({"status": "success", "archives": receipts}).encode())
                elif matches:
                    receipt = matches[-1]
                    with zipfile.ZipFile(platform.directory / (receipt["bundle_sha256"] + ".zip")) as bundle:
                        archive = loads(bundle.read("aica.json").decode())
                        if parts[4] == "archive":
                            self.send_body(200, dumps({"status": "success", "import_id": receipt["import_id"],
                                                       "archive": archive}).encode())
                        elif parts[4] == "artifacts" and len(parts) == 6:
                            artifact = next((item for item in archive["artifacts"] if item["id"] == unquote(parts[5])), None)
                            if artifact:
                                self.send_body(200, bundle.read("objects/" + artifact["sha256"]), artifact["mime_type"])
                            else:
                                self.send_body(404, b'{}')
                        else:
                            self.send_body(404, b'{}')
                else:
                    self.send_body(404, b'{}')

            def log_message(self, format, *arguments):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="Loopback-only AiCA upload contract fixture; not a production backend")
    parser.add_argument("--port", type=int, default=8008)
    arguments = parser.parse_args()
    token = os.environ.get("AICA_UPLOAD_TOKEN", "")
    if len(token) < 32:
        parser.error("Set AICA_UPLOAD_TOKEN to a development token of at least 32 characters")
    directory = tempfile.mkdtemp(prefix="aica-mock-platform-")
    platform = MockPlatform(directory, token, arguments.port)
    platform.start()
    print(f"Mock platform: {platform.url}", flush=True)
    print(f"Project ID: {PROJECT_ID}", flush=True)
    print(f"Private test storage: {directory}", flush=True)
    try:
        while True:
            input()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        platform.close()


if __name__ == "__main__":
    main()
