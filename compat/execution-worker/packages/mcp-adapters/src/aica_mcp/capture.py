import copy
import os
import subprocess
import time

from aica_schema.protocol import ProtocolError, new_id, require, utc_now

from .client import RecordingSource
from .files import private_directory


def checked(result):
    if result["status"] != "success":
        raise ProtocolError(result.get("message", "Recording failed"), result.get("code", "recording_error"))
    return result


def start_source(client, record, include_children=False):
    result = checked(client.call("aica_start_session", {"request_id": new_id(), "record": record,
                                                       "include_children": include_children}))
    source = None
    if record:
        source = RecordingSource(client, result["session_id"], result["session_token"])
    return result, source


def register_actor(source, name, client_name, config=None):
    harness_id = new_id()
    harness = {"id": harness_id, "name": name, "version": "0.1.0", "config": config or {}, "state": {},
               "availability": {"adapter": "recorded", "host_system_instructions": "missing"}}
    checked(source.emit("harness.registered", harness))
    agent_id = new_id()
    agent = {"id": agent_id, "session_id": source.session_id, "parent_agent_id": None,
             "harness_id": harness_id, "client": client_name, "model": {}}
    checked(source.emit("agent.registered", agent))
    return agent_id, harness_id


def extend_graph(source, nodes, edges=None):
    archive = checked(source.client.call("aica_get_archive"))["archive"]
    graph = copy.deepcopy(archive["graph"])
    branches = {branch["id"] for branch in graph["branches"]}
    target = next((branch["id"] for branch in graph["branches"] if branch["state"] == "active"),
                  graph["branches"][0]["id"])
    if "main" not in branches:
        nodes = copy.deepcopy(nodes)
        for node in nodes:
            if node["branch_id"] == "main":
                node["branch_id"] = target
    graph["nodes"].extend(nodes)
    graph["edges"].extend(edges or [])
    result = checked(source.emit("definition.replaced", {"graph": graph, "variables": archive["variables"],
                                                         "interactive_questions": archive["interactive_questions"]},
                                 expected_revision=archive["graph"]["revision_id"], branch_id=target))
    return result["revision_id"]


def capture_file(source, path, mime_type, agent_id=None, node_id=None, run_id=None):
    result = checked(source.emit("artifact.recorded", {}, agent_id=agent_id,
                                 capture={"path": path, "mime_type": mime_type,
                                          "producer_node_id": node_id, "producer_run_id": run_id}))
    return result["artifact"]


def run_command(client, argv, record):
    require(bool(argv), "A command is required")
    session, source = start_source(client, record)
    if source is None:
        process = subprocess.run(argv, cwd=client.project, check=False)
        return {"status": "success", "recording": False, "executed": True, "exit_code": process.returncode,
                "session_id": session["session_id"]}
    try:
        agent_id, harness_id = register_actor(source, "AiCA command capture", "aica-cli",
                                              {"capture_adapter": "aica.run", "runtime": "local-observed@1"})
        node_id = new_id()
        node = {"id": node_id, "kind": "tool", "label": argv[0], "branch_id": "main",
                "provenance": {"mode": "recorded", "evidence": [], "missing": ["portable_runtime_binding"]},
                "execution": {"kind": "command", "tool": argv[0], "version": None, "inputs": {},
                              "params": {}, "outputs": ["stdout", "stderr"], "runtime_ref": "local-observed@1",
                              "argv": argv, "cwd": "."}}
        revision = extend_graph(source, [node])
        run_id = new_id()
        run = {"id": run_id, "trace_id": new_id(), "node_id": node_id, "revision_id": revision,
               "session_id": source.session_id, "agent_id": agent_id, "harness_id": harness_id,
               "state": "running", "started_at": utc_now(), "inputs": {"argv": argv}, "outputs": {},
               "artifact_ids": [], "provenance": {"mode": "recorded", "evidence": []}}
        checked(source.emit("tool.started", run, agent_id))
        directory = client.directory / "imports"
        private_directory(directory)
        stdout_path = directory / f"{run_id}.stdout"
        stderr_path = directory / f"{run_id}.stderr"
        started = time.monotonic()
        execution_error = None
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                os.chmod(stdout_path, 0o600)
                os.chmod(stderr_path, 0o600)
                process = subprocess.run(argv, cwd=client.project, stdout=stdout, stderr=stderr, check=False)
                exit_code = process.returncode
        except OSError as error:
            exit_code = None
            execution_error = str(error)
        stdout_artifact = capture_file(source, stdout_path.relative_to(client.project).as_posix(),
                                       "text/plain", agent_id, node_id, run_id)
        stderr_artifact = capture_file(source, stderr_path.relative_to(client.project).as_posix(),
                                       "text/plain", agent_id, node_id, run_id)
        payload = {"run_id": run_id, "state": "succeeded" if exit_code == 0 else "failed", "ended_at": utc_now(),
                   "outputs": {"exit_code": exit_code, "stdout": stdout_artifact["id"], "stderr": stderr_artifact["id"]},
                   "artifact_ids": list(dict.fromkeys([stdout_artifact["id"], stderr_artifact["id"]])),
                   "duration_ms": (time.monotonic() - started) * 1000}
        if execution_error:
            payload["error"] = execution_error
        checked(source.emit("tool.finished", payload, agent_id))
        checked(source.emit("session.closed", {}))
        snapshot = checked(client.call("aica_export_archive"))
        stdout_path.unlink()
        stderr_path.unlink()
        return {"status": "success", "recording": True, "executed": True, "exit_code": exit_code,
                "run_id": run_id, "session_id": source.session_id, "snapshot": snapshot["snapshot"]}
    finally:
        source.close()
