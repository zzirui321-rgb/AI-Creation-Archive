import copy
import subprocess
from pathlib import Path

from aica_schema.protocol import digest, loads, new_id, require
from aica_schema.recording import require_step_manifest

from .capture import capture_file, checked, extend_graph, register_actor, start_source
from .files import private_directory, project_file


def extract_steps(client, path, record):
    session, source = start_source(client, record)
    if source is None:
        return {"status": "success", "recording": False, "session_id": session["session_id"]}
    try:
        original = project_file(client.project, path)
        require(original.stat().st_size <= 16 * 1024 * 1024, "Step manifest exceeds 16 MiB")
        manifest = loads(original.read_text(encoding="utf-8"))
        require_step_manifest(manifest)
        nodes = copy.deepcopy(manifest["nodes"])
        node_ids = {node["id"] for node in nodes}
        require(len(node_ids) == len(nodes), "Manifest has duplicate node IDs")
        files = manifest["files"]
        selected = {item["path"] for item in files}
        for item in files:
            require(item.get("producer_node_id") is None or item["producer_node_id"] in node_ids,
                    "Output file references a node outside this manifest")
            file_path = project_file(client.project, item["path"])
            require(file_path.stat().st_size <= 64 * 1024 * 1024, "Selected file exceeds 64 MiB")
        for node in nodes:
            provenance = node["provenance"]
            require(provenance["mode"] in {"extracted", "inferred"} and bool(provenance["evidence"]),
                    "Historical nodes require extracted/inferred provenance with evidence")
            require(all(item["source"] in selected for item in provenance["evidence"]),
                    "Every node evidence source must be an explicitly selected file")
        agent_id, harness_id = register_actor(source, "AiCA evidence extractor", "aica-extraction",
                                              {"source_format": "steps", "original_creator": "unknown"})
        evidence_files = {item["source"] for node in nodes for item in node["provenance"]["evidence"]}
        evidence_artifacts = {}
        for item in files:
            if item["path"] in evidence_files:
                artifact = capture_file(source, item["path"], item["mime_type"], agent_id)
                evidence_artifacts[item["path"]] = artifact["id"]
        for node in nodes:
            for evidence in node["provenance"]["evidence"]:
                evidence["artifact_id"] = evidence_artifacts[evidence["source"]]
            node.setdefault("extensions", {})["aica.extraction_manifest"] = digest(manifest)
        archive = checked(client.call("aica_get_archive"))["archive"]
        existing = {node["id"]: node for node in archive["graph"]["nodes"]}
        require(all(node["id"] not in existing or existing[node["id"]] == node for node in nodes),
                "A manifest node ID already has different content", "identity_conflict")
        existing_edges = {edge["id"]: edge for edge in archive["graph"]["edges"]}
        require(all(edge["id"] not in existing_edges or existing_edges[edge["id"]] == edge for edge in manifest["edges"]),
                "A manifest edge ID already has different content", "identity_conflict")
        added_nodes = [node for node in nodes if node["id"] not in existing]
        added_edges = [edge for edge in manifest["edges"] if edge["id"] not in existing_edges]
        if added_nodes or added_edges:
            extend_graph(source, added_nodes, added_edges)
        outputs = []
        for item in files:
            if item.get("producer_node_id") is not None:
                artifact = capture_file(source, item["path"], item["mime_type"], agent_id, item["producer_node_id"])
                outputs.append(artifact["id"])
        checked(source.emit("note.recorded", {"text": "Historical steps extracted from selected files; no original execution Runs were invented.",
                                              "provenance": {"mode": "extracted", "evidence": [
                                                  {"source": name, "artifact_id": artifact_id}
                                                  for name, artifact_id in evidence_artifacts.items()]}}))
        checked(source.emit("session.closed", {}))
        checked(client.call("aica_export_archive"))
        return {"status": "success", "node_count": len(nodes), "added_node_count": len(added_nodes),
                "artifact_ids": list(dict.fromkeys(outputs)), "original_runs_created": 0,
                "session_id": session["session_id"], "executed": False}
    finally:
        source.close()


def extracted_node(label, evidence, kind="decision", execution=None, description=""):
    node = {"id": new_id(), "kind": kind, "label": label, "branch_id": "main", "description": description,
            "provenance": {"mode": "extracted", "evidence": evidence,
                           "missing": ["original_environment", "complete_harness_state"]}}
    if execution is not None:
        node["execution"] = execution
    return node


def tool_node(name, inputs, evidence):
    node = extracted_node(name, evidence, "tool", {"kind": "manual", "tool": name, "version": None,
                                                  "inputs": {"arguments": {"kind": "literal", "value": inputs}},
                                                  "params": {}, "outputs": ["result"]})
    return node


def parse_agent_log(text, source_format, evidence_source, artifact_id):
    nodes = []
    calls = {}
    warnings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            entry = loads(line)
            require(isinstance(entry, dict), f"Log line {line_number} must be an object")
            evidence = [{"source": evidence_source, "line": line_number, "artifact_id": artifact_id}]
            parts = []
            if source_format == "claude_code":
                message = entry.get("message", {})
                if isinstance(message, dict):
                    content = message.get("content", [])
                    parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
            elif source_format == "codex":
                payload = entry.get("payload", {})
                if entry.get("type") == "response_item" and isinstance(payload, dict):
                    kind = payload.get("type")
                    if kind == "function_call":
                        parts = [{"type": "tool_use", "id": payload.get("call_id"), "name": payload.get("name"),
                                  "input": payload.get("arguments", {})}]
                    elif kind == "function_call_output":
                        parts = [{"type": "tool_result", "tool_use_id": payload.get("call_id"),
                                  "content": payload.get("output")}]
                    elif kind == "message":
                        parts = [{"type": "text", "text": item.get("text", "")} for item in payload.get("content", [])
                                 if isinstance(item, dict) and item.get("type") in ("input_text", "output_text")]
            else:
                kind = entry.get("type")
                if kind == "tool_call":
                    parts = [{"type": "tool_use", "id": entry.get("id"), "name": entry.get("name"),
                              "input": entry.get("arguments", {})}]
                elif kind == "tool_result":
                    parts = [{"type": "tool_result", "tool_use_id": entry.get("call_id"),
                              "content": entry.get("output")}]
                elif kind == "message":
                    parts = [{"type": "text", "text": entry.get("content", "")}]
            if not parts:
                warnings.append({"line": line_number, "reason": "Unrecognized record; retained in source artifact"})
            for part in parts:
                require(isinstance(part, dict), f"Invalid content block at line {line_number}")
                kind = part.get("type")
                if kind == "tool_use":
                    require(isinstance(part.get("name"), str) and bool(part["name"]),
                            f"Missing tool name at line {line_number}")
                    node = tool_node(part["name"], part.get("input", {}), evidence)
                    nodes.append(node)
                    if part.get("id"):
                        calls[part["id"]] = node
                elif kind == "tool_result":
                    node = calls.get(part.get("tool_use_id"))
                    if node is not None:
                        node["provenance"]["evidence"].extend(evidence)
                        node.setdefault("extensions", {})["aica.observed_result"] = copy.deepcopy(part.get("content"))
                    else:
                        warnings.append({"line": line_number, "reason": "Result has no observed call"})
                elif kind == "text" and isinstance(part.get("text"), str) and part["text"]:
                    nodes.append(extracted_node("Conversation message", evidence, "prompt", description=part["text"]))
    return {"nodes": nodes, "warnings": warnings}


def extract_log(client, path, source_format, record):
    session, source = start_source(client, record)
    if source is None:
        return {"status": "success", "recording": False, "session_id": session["session_id"]}
    staged = None
    try:
        original = Path(path).expanduser().resolve(strict=True)
        require(original.is_file() and original.stat().st_size <= 16 * 1024 * 1024,
                "Select an authorized JSONL export of at most 16 MiB")
        private_directory(client.directory / "imports")
        staged = client.directory / "imports" / f"{new_id()}.jsonl"
        staged.write_bytes(original.read_bytes())
        staged.chmod(0o600)
        agent_id, harness_id = register_actor(source, "AiCA log extractor", "aica-extraction",
                                              {"source_format": source_format})
        artifact = capture_file(source, staged.relative_to(client.project).as_posix(),
                                "application/x-ndjson", agent_id)
        parsed = parse_agent_log(staged.read_text(encoding="utf-8"), source_format, original.name, artifact["id"])
        require(bool(parsed["nodes"]), "No supported creation records found; original export was retained")
        extend_graph(source, parsed["nodes"])
        checked(source.emit("note.recorded", {"text": f"Imported {source_format}; warnings: {len(parsed['warnings'])}",
                                               "provenance": {"mode": "extracted", "evidence": [
                                                   {"source": original.name, "artifact_id": artifact["id"]}]}}, agent_id))
        checked(source.emit("session.closed", {}))
        checked(client.call("aica_export_archive"))
        return {"status": "success", "node_count": len(parsed["nodes"]), "warnings": parsed["warnings"],
                "session_id": session["session_id"], "artifact_id": artifact["id"]}
    finally:
        source.close()
        if staged is not None:
            staged.unlink(missing_ok=True)


def extract_git(client, record, limit=50):
    session, source = start_source(client, record)
    if source is None:
        return {"status": "success", "recording": False, "session_id": session["session_id"]}
    try:
        register_actor(source, "AiCA Git extractor", "aica-extraction", {"source_format": "git"})
        process = subprocess.run(["git", "-C", str(client.project), "log", f"-{limit}", "--reverse",
                                  "--format=%H%x1f%P%x1f%s"], capture_output=True, text=True, check=False)
        require(process.returncode == 0, process.stderr.strip() or "Could not read Git history")
        commits = []
        for line in process.stdout.splitlines():
            commit, parents, subject = line.split("\x1f", 2)
            node = extracted_node(subject, [{"source": "git", "commit": commit}], description=subject)
            commits.append({"commit": commit, "parents": parents.split(), "node": node})
        require(bool(commits), "Repository has no commits")
        by_commit = {item["commit"]: item["node"]["id"] for item in commits}
        edges = [{"id": new_id(), "source": by_commit[parent], "target": item["node"]["id"], "kind": "control"}
                 for item in commits for parent in item["parents"] if parent in by_commit]
        extend_graph(source, [item["node"] for item in commits], edges)
        checked(source.emit("session.closed", {}))
        checked(client.call("aica_export_archive"))
        return {"status": "success", "node_count": len(commits), "session_id": session["session_id"],
                "limitations": ["Git ancestry identifies milestones, not original prompts or tool invocations"]}
    finally:
        source.close()
