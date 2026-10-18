import importlib.util
import os
import stat
import tempfile
import time
from pathlib import Path
from uuid import UUID

from aica_schema.protocol import ProtocolError, dumps, loads, require


def daemon_sockets():
    if os.name == "nt":
        return []
    directories = {Path("/tmp"), Path(tempfile.gettempdir())}
    sockets = set()
    for root in directories:
        directory = root / ("codex-daemon-" + str(os.getuid()))
        if directory.is_dir() and directory.stat().st_uid == os.getuid():
            for path in directory.iterdir():
                info = path.lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                    sockets.add(path.resolve())
    return sorted(sockets)


def probe_bridge(path, project, session_id, timeout):
    from websockets.sync.client import unix_connect

    deadline = time.monotonic() + timeout
    with unix_connect(str(path), uri="ws://localhost", open_timeout=timeout, close_timeout=0.1,
                      max_size=2 * 1024 * 1024) as socket:
        def request(identifier, method, params):
            socket.send(dumps({"id": identifier, "method": method, "params": params}))
            for attempt in range(100):
                remaining = deadline - time.monotonic()
                require(remaining > 0, "Host bridge discovery timed out", "bridge_unavailable")
                reply = loads(socket.recv(timeout=remaining))
                require(isinstance(reply, dict), "Invalid Host response", "bridge_unavailable")
                if reply.get("id") == identifier:
                    require("error" not in reply, "Host MCP connection is unavailable", "bridge_unavailable")
                    return reply["result"]
            raise ProtocolError("Host bridge response limit exceeded", "bridge_unavailable")

        request(1, "initialize", {"clientInfo": {"name": "aica-hook-routing", "version": "1.0"},
                                  "capabilities": {"experimentalApi": True}})
        socket.send(dumps({"method": "initialized"}))
        response = request(2, "mcpServer/tool/call", {"threadId": session_id, "server": "aica",
                           "tool": "aica_host_status", "arguments": {}})
        require(isinstance(response, dict), "Invalid Host tool response", "bridge_unavailable")
        value = response.get("structuredContent", {})
        require(isinstance(value, dict), "Invalid Host connection identity", "bridge_unavailable")
        require(response.get("isError") is False and value.get("status") == "success"
                and value.get("client") == "codex" and value.get("host_routing_version") == 1
                and isinstance(value.get("bridge_id"), str) and 0 < len(value["bridge_id"]) <= 200
                and isinstance(value.get("project"), str) and value["project"],
                "Host MCP connection identity is unavailable", "bridge_unavailable")
        require(Path(value["project"]).resolve() == Path(project).resolve(),
                "Host MCP connection belongs to a different workspace", "workspace_mismatch")
        return value["bridge_id"]


def discover_bridge(project, session_id):
    try:
        UUID(session_id)
    except (ValueError, TypeError):
        return None
    if importlib.util.find_spec("websockets") is None:
        return None
    from websockets.exceptions import WebSocketException

    matches = set()
    deadline = time.monotonic() + 2
    for path in daemon_sockets():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            bridge = probe_bridge(path, project, session_id, remaining)
        except ProtocolError as error:
            if error.code == "workspace_mismatch":
                raise
        except (OSError, ValueError, TimeoutError, WebSocketException):
            pass
        else:
            matches.add(bridge)
    require(len(matches) <= 1, "More than one Host serves this session", "bridge_ambiguous")
    result = next(iter(matches), None)
    return result
