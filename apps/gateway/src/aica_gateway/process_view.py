"""Display recorded execution facts without changing the portable archive."""

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime

from aica_schema.protocol import ProtocolError

VERSION = "mcp-fields/1"
MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_MESSAGES = 2000
SENSITIVE = re.compile(r"^(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret|credentials|client[_-]?secret)$", re.I)


def redacted(value):
    if isinstance(value, dict):
        return {key: redacted(item) for key, item in value.items() if not SENSITIVE.fullmatch(key)}
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def model_name(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return next((value[key] for key in ("id", "model", "name", "model_id", "model_name")
                     if isinstance(value.get(key), str) and value[key]), model_name(value.get("value")))
    return None


def input_text(value, variables):
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    if value.get("kind") == "variable":
        variable = variables.get(value.get("variable_id"), {})
        result = variable.get("value", variable.get("default"))
        return result if isinstance(result, str) else ""
    if value.get("kind") == "template":
        def substitute(match):
            key = value.get("variables", {}).get(match[1], match[1])
            variable = variables.get(key, {})
            result = variable.get("value", variable.get("default"))
            return result if isinstance(result, str) else match[0]

        return re.sub(r"\{\{([^}]+)\}\}", substitute, value.get("template", ""))
    return input_text(value.get("value"), variables)


def history_messages(source, read_artifact):
    messages, issues, seen = [], [], set()
    artifacts = {item["id"]: item for item in source.get("artifacts", [])}
    total = 0
    for index, reference in enumerate(source.get("history", [])):
        if reference.get("kind") != "events":
            continue
        artifact_id = reference["artifact_id"]
        artifact = artifacts.get(artifact_id, {})
        try:
            if not read_artifact:
                raise ValueError("历史附件内容未上传")
            path = read_artifact(artifact_id)
            total += path.stat().st_size
            if total > MAX_HISTORY_BYTES:
                raise ValueError("历史附件超过本次展示预算，原附件仍可下载")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != artifact.get("sha256"):
                    raise ValueError("历史附件哈希校验失败")
                stream.seek(0)
                for line_number, line in enumerate(iter(lambda: stream.readline(2 * 1024 * 1024 + 1), b""), 1):
                    if len(line) > 2 * 1024 * 1024:
                        raise ValueError("单条历史事件过大，原附件仍可下载")
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    event = record.get("event", record)
                    if event.get("type") != "message.recorded" or event.get("payload", {}).get("role") != "user":
                        continue
                    event_id = event.get("event_id")
                    if not event_id or event_id in seen:
                        continue
                    text = event["payload"].get("content")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    if len(messages) >= MAX_MESSAGES:
                        raise ValueError("用户历史超过本次展示条数，原附件仍可下载")
                    seen.add(event_id)
                    messages.append({"id": event_id, "text": text, "session_id": event.get("session_id"),
                                     "agent_id": event.get("agent_id"), "node_id": event["payload"].get("node_id"),
                                     "timestamp": event.get("timestamp"), "artifact_id": artifact_id,
                                     "source_pointer": f"/history/{index}#line={line_number}"})
        except (ProtocolError, OSError, ValueError, TypeError, AttributeError) as error:
            message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else "历史附件缺失、不可读取或格式无效"
            issues.append({"artifact_id": artifact_id, "state": "not_expanded", "message": message})
    return messages, issues


def archive_process_view(source, read_artifact=None):
    graph = source["graph"]
    variables = source.get("variables", {})
    agents = {item["id"]: (index, item) for index, item in enumerate(source.get("agents", []))}
    harnesses = {item["id"]: item for item in source.get("harnesses", [])}
    artifacts = source.get("artifacts", [])
    by_node = defaultdict(list)
    for index, run in enumerate(source.get("runs", [])):
        by_node[run["node_id"]].append((index, run))
    messages, history_issues = history_messages(source, read_artifact)
    nodes, evidence = [], []
    for index, node in enumerate(graph["nodes"]):
        execution = node.get("execution") or {}
        candidates = sorted(by_node[node["id"]], key=lambda pair: (datetime.fromisoformat(pair[1]["started_at"]).timestamp(), pair[0]))
        run_index, run = candidates[-1] if candidates else (None, {})
        agent_index, agent = agents.get(run.get("agent_id"), (None, {}))
        defined_inputs = execution.get("inputs", {})
        actual_inputs = run.get("inputs", {})
        inputs = {**defined_inputs, **actual_inputs}
        params = execution.get("params", {})
        choices = [(actual_inputs.get("prompt"), f"/runs/{run_index}/inputs/prompt"),
                   (node.get("prompt"), f"/graph/nodes/{index}/prompt"),
                   (defined_inputs.get("prompt"), f"/graph/nodes/{index}/execution/inputs/prompt"),
                   (params.get("prompt"), f"/graph/nodes/{index}/execution/params/prompt")]
        prompt, prompt_source = next(((text, pointer) for value, pointer in choices
                                      if (text := input_text(value, variables))), ("", None))
        models = [(actual_inputs.get("model"), f"/runs/{run_index}/inputs/model"),
                  (node.get("model"), f"/graph/nodes/{index}/model"),
                  (execution.get("model"), f"/graph/nodes/{index}/execution/model")]
        model, model_source = next(((name, pointer) for value, pointer in models if (name := model_name(value))), (None, None))
        agent_model = model_name(agent.get("model"))
        unresolved_prompt = next((pointer for value, pointer in choices if value), None)
        unresolved_model = next((pointer for value, pointer in models if value), None)
        evidence_id = "recorded-fields-" + node["id"]
        fields = {"prompt": {"state": "recorded" if prompt_source else "unresolved" if unresolved_prompt else "not_recorded",
                              "source_pointer": prompt_source or unresolved_prompt},
                  "model": {"state": "recorded" if model_source else "unresolved" if unresolved_model else "not_recorded",
                            "source_pointer": model_source or unresolved_model},
                  "agent_model": {"state": "recorded" if agent_model else "unresolved" if agent.get("model") else "not_recorded",
                                  "source_pointer": f"/agents/{agent_index}/model" if agent else None}}
        evidence.append({"id": evidence_id, "run_id": run.get("id"), "agent_id": agent.get("id"), "fields": fields,
                         "run_revision_id": run.get("revision_id"), "definition_revision_id": graph.get("revision_id"),
                         "definition_inputs": defined_inputs, "original_evidence": node.get("provenance", {}).get("evidence", [])})
        harness_id = run.get("harness_id") or agent.get("harness_id") or node.get("harness_id")
        nodes.append({"id": node["id"], "label": node.get("label", node["id"]), "kind": node.get("kind", "tool"),
                      "prompt": prompt, "model": model, "agent_model": agent_model, "agent": agent or None,
                      "mode": execution.get("kind"), "harness": harnesses.get(harness_id, harness_id),
                      "params": params, "inputs": inputs, "outputs": run.get("outputs", execution.get("outputs", [])),
                      "status": run.get("state"), "provenance": node.get("provenance", {}).get("mode", "recorded"),
                      "evidence_ids": [evidence_id], "field_status": fields, "branch_id": node.get("branch_id"),
                      "artifact_ids": list(dict.fromkeys([item["id"] for item in artifacts if item.get("producer_node_id") == node["id"]]
                                                        + run.get("artifact_ids", []))),
                      "runs": [item for _, item in candidates]})
    return redacted({"format": "aica-process-view/0.1", "parser_version": VERSION, "nodes": nodes,
                     "edges": [{**edge, "state": "verified", "explanation": "原始档案登记的关系"} for edge in graph["edges"]],
                     "evidence": evidence, "warnings": [item["message"] for item in history_issues],
                     "source_context": {"golden_path_node_ids": graph["golden_path"], "branches": graph["branches"],
                                        "user_messages": messages, "history_issues": history_issues}})


def stored_process_view(service, commit, source):
    from .repo_media import source_snapshot

    snapshot = None

    def read_artifact(identifier):
        nonlocal snapshot
        if snapshot is None:
            snapshot = source_snapshot(service, commit)
        return service.store.artifact_in_snapshot(snapshot, identifier)[1]

    return archive_process_view(source, read_artifact)


def repository_process_view(service, commit, document):
    if "aica_version" in document:
        return stored_process_view(service, commit, document)
    if document.get("format") != "aica-ui-module-record" or not commit["project_id"]:
        return None
    with service.store.connection() as db:
        project = db.execute("SELECT subject FROM projects WHERE id=?", (commit["project_id"],)).fetchone()
    if project is None:
        return None
    source = service.store.snapshot(project["subject"], commit["project_id"], commit["import_id"])["archive"]
    view = stored_process_view(service, commit, source)
    saved = {node["id"]: (index, node) for index, node in enumerate(document.get("steps", document.get("modules", [])))}
    ui = document.get("ui") or {}
    for node in view["nodes"]:
        if node["id"] not in saved:
            continue
        index, current = saved[node["id"]]
        for field, baseline in (("prompt", "basePrompts"), ("model", "baseModels")):
            if field in current and (current[field] or (ui.get(baseline) or {}).get(node["id"]) or current.get("fieldStatus")):
                node[field] = current[field]
                node["field_status"][field] = {"state": "saved", "source_pointer": f"/steps/{index}/{field}"}
        for field in ("outputs", "harness", "status"):
            if current.get(field):
                node[field] = current[field]
        node["inputs"] = {**node["inputs"], **(current.get("inputs") or {})}
        next(item for item in view["evidence"] if item["id"] in node["evidence_ids"])["fields"] = node["field_status"]
    if document.get("recorded_messages"):
        view["source_context"]["user_messages"] = document["recorded_messages"]
    return redacted(view)
