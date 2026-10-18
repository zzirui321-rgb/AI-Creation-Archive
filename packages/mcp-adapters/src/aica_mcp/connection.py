import secrets
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from aica_schema.protocol import ProtocolError, loads, new_id, require

from .transfer import require_upload_url


class ConnectionPages:
    def __init__(self, coordinator, transport=None):
        self.coordinator = coordinator
        self.transport = transport
        self.origin = None
        self.requests = {}
        self.lock = threading.RLock()

    def create(self, server_url=None, allow_loopback_http=False):
        if server_url is None:
            config = self.coordinator.platform.read_config()
            require(config is not None, "Supply --server for the first connection", "platform_unbound")
            server_url = config["server_url"]
            allow_loopback_http = config["allow_loopback_http"]
        server_url = server_url.rstrip("/")
        require_upload_url(server_url, allow_loopback_http)
        require(urlsplit(server_url).path == "", "Platform URL must be an origin", "unsafe_destination")
        with self.lock:
            self.expire()
            require(len(self.requests) < 4, "Close an existing connection page first", "connection_busy")
            nonce = secrets.token_urlsafe(32)
            self.requests[nonce] = {"server_url": server_url, "allow_loopback_http": allow_loopback_http,
                                    "created": time.monotonic(), "login": None}
        return {"status": "success", "url": self.origin + "/connect#" + nonce, "expires_in": 600}

    def expire(self):
        for nonce, item in list(self.requests.items()):
            if time.monotonic() - item["created"] > 600:
                self.requests.pop(nonce)

    def remote(self, item, method, path, body=None):
        headers = {"Accept": "application/json"}
        if item["login"]:
            headers["Authorization"] = "Bearer " + item["login"]["access_token"]
        try:
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                with client.stream(method, item["server_url"] + "/api" + path, headers=headers, json=body) as response:
                    require(response.status_code in {200, 201},
                            f"Platform request failed (HTTP {response.status_code}); check login and project access",
                            "platform_login_failed" if response.status_code == 401 else "platform_request_failed")
                    raw = bytearray()
                    for block in response.iter_bytes():
                        raw.extend(block)
                        require(len(raw) <= 256 * 1024, "Platform response exceeds its size limit")
            result = loads(raw.decode("utf-8"))
            require(isinstance(result, dict) and result.get("status") == "success", "Invalid platform response")
        except httpx.HTTPError as error:
            raise ProtocolError("Could not connect to the platform; check its address and TLS certificate",
                                "platform_unavailable") from error
        return result

    def dispatch(self, action, nonce, **arguments):
        with self.lock:
            self.expire()
            require(isinstance(nonce, str) and nonce in self.requests, "Connection page expired; run aica connect again",
                    "connection_expired")
            item = self.requests[nonce]
            recorder = self.coordinator.recorder
            if action == "view":
                with recorder.mutex:
                    return {"status": "success", "project": recorder.project.name, "server_url": item["server_url"],
                            "node_count": len(recorder.document["graph"]["nodes"]),
                            "artifact_count": len(recorder.document["artifacts"]), "logged_in": item["login"] is not None}
            if action == "cancel":
                self.requests.pop(nonce)
                return {"status": "success", "connected": False}
            if action == "login":
                require(set(arguments) == {"username", "password"} and all(isinstance(v, str) and 0 < len(v) <= 1024
                        for v in arguments.values()), "Enter your username and password")
                item["login"] = None
                result = self.remote(item, "POST", "/auth/login", arguments)
                require(isinstance(result.get("access_token"), str) and 32 <= len(result["access_token"]) <= 512
                        and isinstance(result.get("expires_in"), (int, float)) and result["expires_in"] > 0,
                        "Invalid login response")
                item["login"] = {"access_token": result["access_token"],
                                 "expires_at": time.time() + result["expires_in"]}
                return {"status": "success", "logged_in": True}
            require(item["login"] is not None and item["login"]["expires_at"] > time.time(), "Log in first",
                    "platform_login_required")
            if action == "projects":
                result = self.remote(item, "GET", "/projects?limit=200")
                require(isinstance(result.get("projects"), list), "Invalid projects response")
                return {"status": "success", "projects": [{"id": p["id"], "title": p["title"]} for p in result["projects"]]}
            if action == "repositories":
                result = self.remote(item, "GET", "/repos?scope=mine&limit=200")
                return {"status": "success", "repositories": [{"id": p["id"], "title": p["title"]} for p in result["repositories"]]}
            require(action == "bind" and arguments.get("confirm_project") is True,
                    "Explicit project authorization is required", "project_not_authorized")
            project_id = arguments.get("project_id")
            if not project_id:
                title = arguments.get("title")
                require(isinstance(title, str) and 0 < len(title.strip()) <= 200, "Choose a project or enter a project name")
                result = self.remote(item, "POST", "/projects", {"title": title.strip()})
                project_id = result["project"]["id"]
            project_id = str(UUID(project_id))
            repo_id = None
            if arguments.get("sync_enabled") is True:
                repo_id = arguments.get("repo_id")
                if not repo_id:
                    result = self.remote(item, "POST", "/repos", {"name": "sync-" + new_id(),
                                         "title": arguments.get("title") or recorder.project.name, "visibility": "private"})
                    repo_id = result["repository"]["id"]
                repo_id = str(UUID(repo_id))
            issued = self.remote(item, "POST", "/mcp/upload-tokens", {"project_id": project_id})
            require(issued.get("project_id") == project_id and isinstance(issued.get("expires_in"), (int, float))
                    and issued["expires_in"] > 0, "Invalid upload credential response")
            platform = self.coordinator.platform
            config = {"version": 1, "server_url": item["server_url"], "project_id": project_id,
                      "token_env": "AICA_UPLOAD_TOKEN", "auto_upload": True, "interval_seconds": 5,
                      "allow_loopback_http": item["allow_loopback_http"]}
            credential = {"version": 1, "identity": platform.credentials.identity(config), "grant_id": new_id(),
                          "enabled": True, "upload_token": issued["access_token"], "upload_token_id": str(UUID(issued["id"])),
                          "upload_expires_at": time.time() + issued["expires_in"],
                          "login_token": item["login"]["access_token"], "login_expires_at": item["login"]["expires_at"]}
            if repo_id:
                config["sync"] = {"repo_id": repo_id, "branch": "main"}
                credential.update(sync_repo_id=repo_id, sync_files=True)
            try:
                platform.connect(config, credential)
            except BaseException:
                self.revoke(item, credential["upload_token_id"])
                raise
            self.requests.pop(nonce)
            return {"status": "success", "connected": True, "project_id": project_id,
                    "message": "项目已连接。后续自动记录、登记选定产物并上传；可随时暂停或断开。"}

    def revoke(self, item, token_id):
        try:
            self.remote(item, "DELETE", "/mcp/upload-tokens/" + str(UUID(token_id)))
            revoked = True
        except (ProtocolError, OSError, ValueError):
            revoked = False
        return revoked
