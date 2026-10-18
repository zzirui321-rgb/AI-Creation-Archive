from dataclasses import dataclass

from aica_schema.protocol import ProtocolError, digest, loads, require, require_archive, require_shape
from aica_schema.recording import require_event

from ..evidence import HISTORY_LIMIT, redact
from .contract import Source
from .input_budget import fit_model_input
from .materials import MATERIAL_LIMIT, TEXT_MIMES, read_material
from .module_content import content_catalog
from .reusability import reuse_hints
from .validation import pointer, sha256

PARSER_VERSION = "aica-graph-parser/0.5.3"
PROMPT_VERSION = "aica-graph-prompt/0.5.3"


def stable_id(prefix, value):
    return prefix + "-" + digest(value)[:24]


def issue(code, message, identity=None):
    return {"id": stable_id("issue", [code, identity]), "code": code, "severity": "warning",
            "message": message, "node_ids": [], "evidence_ids": []}


@dataclass
class ParseContext:
    archive_bytes: bytes
    archive: dict
    history: dict
    prepared: dict
    evidence: list
    node_evidence: dict
    module_catalog: dict | None = None


def parameter_locations(node, evidence_id):
    execution = node.get("execution", {})
    locations = []

    def add(path, value):
        if value is None:
            return
        safe = pointer(redact(node), path)
        decoder, candidates = "identity", [("", value)]
        if isinstance(value, dict):
            candidates = [("/" + key.replace("~", "~0").replace("/", "~1"), item) for key, item in value.items()]
        if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                parsed = loads(value)
            except ProtocolError:
                pass
            else:
                if isinstance(parsed, dict):
                    decoder = "json_string"
                    if not isinstance(safe, str):
                        return
                    safe = loads(safe)
                    candidates = [("/" + key.replace("~", "~0").replace("/", "~1"), item)
                                  for key, item in parsed.items()]
        for value_pointer, current in candidates:
            if current is not None and pointer(safe, value_pointer) == current and "[REDACTED" not in str(current):
                locations.append({"source_node_id": node["id"], "evidence_id": evidence_id,
                                  "pointer": path, "decode": decoder, "value_pointer": value_pointer})

    for name, binding in execution.get("inputs", {}).items():
        if binding["kind"] == "literal":
            add("/execution/inputs/" + name.replace("~", "~0").replace("/", "~1") + "/value", binding["value"])
    for name, value in execution.get("params", {}).items():
        add("/execution/params/" + name.replace("~", "~0").replace("/", "~1"), value)
    return locations


def prepare_graph(archive_bytes, import_id, read_history=None, node_ids=None):
    archive = loads(archive_bytes.decode("utf-8"))
    require_archive(archive)
    nodes = {node["id"]: node for node in archive["graph"]["nodes"]}
    selected = set(nodes) if node_ids is None else set(node_ids)
    require(node_ids is None or len(node_ids) == len(selected), "Duplicate selected node", "invalid_analysis_scope")
    require(selected <= nodes.keys(), "Unknown selected node", "invalid_analysis_scope")
    require(len(selected) <= 200, "Select at most 200 source nodes", "analysis_limit")
    snapshot = archive["snapshot"]
    source = Source(import_id=import_id, archive_sha256=sha256(archive_bytes),
                    archive_revision_id=archive["graph"]["revision_id"], snapshot_id=snapshot["snapshot_id"],
                    stream_id=snapshot["stream_id"], through_seq=snapshot["commit_seq"]).model_dump()
    facts, evidence, node_evidence, timeline, history, issues, omitted = [], [], {}, [], {}, [], []

    def add_archive(path, value, related=()):
        identifier = stable_id("ev", ["archive", path])
        evidence.append({"id": identifier, "kind": "archive", "pointer": path,
                         "source_sha256": source["archive_sha256"]})
        facts.append({"id": identifier, "role": "unknown", "actor_id": None,
                      "node_ids": list(related), "value": redact(value)})
        return identifier

    locations = []
    for index, node in enumerate(archive["graph"]["nodes"]):
        if node["id"] in selected:
            identifier = add_archive(f"/graph/nodes/{index}", node, [node["id"]])
            node_evidence[node["id"]] = identifier
            locations.extend(parameter_locations(node, identifier))
    current_runs = []
    for index, run in enumerate(archive["runs"]):
        if run["node_id"] in selected:
            add_archive(f"/runs/{index}", run, [run["node_id"]])
            if run["revision_id"] == source["archive_revision_id"]:
                current_runs.append(run)
    history_ids = {ref["artifact_id"] for ref in archive["history"]}
    remaining_material = MATERIAL_LIMIT
    full_materials = {}
    for index, artifact in enumerate(archive["artifacts"]):
        assigned = artifact["producer_node_id"] in selected
        orphan = node_ids is None and artifact["producer_node_id"] is None and artifact["id"] not in history_ids
        if assigned or orphan:
            identifier = add_archive(f"/artifacts/{index}", artifact, [artifact["producer_node_id"]] if assigned else [])
            fact = facts[-1]
            fact["artifact_id"] = artifact["id"]
            fact["content_state"] = "metadata_only"
            if artifact["mime_type"].split(";")[0].strip() in TEXT_MIMES:
                if read_history is None:
                    fact["content_state"] = "unavailable"
                else:
                    try:
                        material, raw = read_material(artifact, read_history, max(512, min(24 * 1024, remaining_material)),
                                                      on_material=lambda value: full_materials.update({identifier: value}))
                    except (OSError, ProtocolError, UnicodeError, ValueError):
                        fact["content_state"] = "unavailable_or_over_budget"
                    else:
                        remaining_material = max(0, remaining_material - len(raw))
                        fact.update(content=material, content_state="available")
                if fact["content_state"] != "available":
                    entry = issue("MATERIAL_CONTENT_UNAVAILABLE", "附件文本内容未读取；保留元数据但不能据此推断内部内容。", artifact["id"])
                    entry["evidence_ids"] = [identifier]
                    issues.append(entry)

    artifacts = {item["id"]: item for item in archive["artifacts"]}
    remaining, seen_artifacts, seen_events, seen_commits, seen_sources = HISTORY_LIMIT, set(), {}, {}, {}
    for reference in archive["history"]:
        artifact_id = reference["artifact_id"]
        if reference["kind"] != "events" or artifact_id in seen_artifacts:
            continue
        seen_artifacts.add(artifact_id)
        if read_history is None or len(history) >= 16:
            omitted.append(artifact_id)
            issues.append(issue("HISTORY_UNAVAILABLE", "事件附件未提供或超出附件数量范围。", artifact_id))
            continue
        try:
            content = read_history(artifact_id, remaining)
        except (OSError, ProtocolError) as error:
            if isinstance(error, ProtocolError) and error.code == "analysis_limit":
                raise
            omitted.append(artifact_id)
            issues.append(issue("HISTORY_UNAVAILABLE", "事件附件不可读取或校验失败。", artifact_id))
            continue
        require(len(content) <= remaining, "Event history exceeds 1 MiB; select a smaller export", "analysis_limit")
        remaining -= len(content)
        require(sha256(content) == artifacts[artifact_id]["sha256"], "History hash mismatch", "storage_corrupt")
        pending = []
        try:
            for line_number, line in enumerate(content.decode("utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                envelope = loads(line)
                event = envelope["event"]
                require_shape(event, "event")
                if event["type"] in {"message.recorded", "note.recorded"}:
                    require_event(event)
                require(envelope["stream_id"] == source["stream_id"], "Event stream mismatch")
                sequence = envelope["commit_seq"]
                require(type(sequence) is int and 1 <= sequence <= source["through_seq"], "Event exceeds snapshot")
                require(sequence <= reference["through_seq"], "Event exceeds declared history boundary")
                pending.append((line_number, sequence, event))
        except (ProtocolError, ValueError, KeyError, TypeError, UnicodeError):
            omitted.append(artifact_id)
            issues.append(issue("HISTORY_INVALID", "事件附件格式或快照边界无效，整份附件未用于解析。", artifact_id))
            continue
        history[artifact_id] = content
        for line_number, sequence, event in pending:
            event_id, signature = event["event_id"], digest(event)
            require(event_id not in seen_events or seen_events[event_id] == (sequence, signature),
                    "Conflicting event identity", "invalid_analysis_scope")
            require(sequence not in seen_commits or seen_commits[sequence] == event_id,
                    "Conflicting event commit", "invalid_analysis_scope")
            source_key = (event["source_id"], event["source_seq"])
            require(source_key not in seen_sources or seen_sources[source_key] == event_id,
                    "Conflicting event source sequence", "invalid_analysis_scope")
            duplicate = event_id in seen_events
            seen_events[event_id], seen_commits[sequence], seen_sources[source_key] = (sequence, signature), event_id, event_id
            if duplicate or event["type"] not in {"message.recorded", "note.recorded"}:
                continue
            payload = event["payload"]
            if event["type"] == "message.recorded" and payload["role"] == "system":
                continue
            if payload.get("node_id") is not None and payload["node_id"] not in selected:
                continue
            role = payload.get("role", "unknown")
            role = "agent" if role == "assistant" else role
            path = "/payload/content" if event["type"] == "message.recorded" else "/payload/text"
            identifier = stable_id("ev", ["event", artifact_id, line_number, path])
            evidence.append({"kind": "event", "id": identifier, "artifact_id": artifact_id, "line": line_number,
                             "event_id": event_id, "pointer": path, "source_sha256": sha256(content)})
            metadata = {key: event[key] for key in ("event_id", "source_id", "source_seq", "session_id", "agent_id",
                                                   "timestamp", "causal_parents", "type")}
            metadata.update(commit_seq=sequence, evidence_id=identifier, role=role)
            timeline.append(metadata)
            facts.append({"id": identifier, "role": role, "actor_id": event.get("agent_id"),
                          "node_ids": [payload["node_id"]] if payload.get("node_id") else [],
                          "event": metadata, "value": redact(pointer(event, path))})
    if not snapshot["complete"] or snapshot["gaps"]:
        issues.append(issue("RECORDING_INCOMPLETE", "录制存在缺口；不能将已观察范围视作完整过程。"))
    edges = archive["graph"]["edges"]
    if any((edge["source"] in selected) != (edge["target"] in selected) for edge in edges):
        issues.append(issue("EXTERNAL_DEPENDENCY", "选定范围与外部节点之间存在显式依赖。"))
    if any(run["revision_id"] != source["archive_revision_id"] for run in archive["runs"]):
        issues.append(issue("HISTORICAL_RUN_SCOPE", "其他定义修订的执行保留在原始档案，本图不将其视为当前执行。"))
    if any(fact.get("artifact_id") for fact in facts) and not selected:
        issues.append(issue("ARTIFACT_ONLY_SOURCE", "档案登记了创作附件，但没有原始节点或执行记录；步骤只能由材料推断，不能声称录制了工具调用。"))
    if not facts:
        issues.append(issue("NO_ANALYSABLE_CONTENT", "选定范围没有可分析的节点、消息或创作附件。"))
    coverage = {"recording_complete": snapshot["complete"], "selected_source_node_ids": sorted(selected),
                "unassigned_source_node_ids": [], "omitted_history_artifact_ids": omitted, "issues": issues}
    identities = [run["id"] for run in archive["runs"] if run["node_id"] in selected]
    identities += [item["id"] for item in archive["artifacts"] if item["producer_node_id"] in selected]
    identities += [fact["artifact_id"] for fact in facts if fact.get("artifact_id")]
    require(redact(identities) == identities and redact(edges) == edges,
            "Source reference contains sensitive content", "analysis_sensitive_identifiers")
    prepared = {"format": "aica-graph-input/0.1", "source": source, "parser_version": PARSER_VERSION,
                "prompt_version": PROMPT_VERSION, "source_node_ids": sorted(selected), "facts": facts,
                "evidence_catalog": evidence, "parameter_locations": locations,
                "declared_dependencies": [edge for edge in edges if edge["source"] in selected and edge["target"] in selected],
                "conversation_state": {"confirmed_interpretations": [], "message_timeline": sorted(timeline, key=lambda e: e["commit_seq"]),
                                       "attempts_by_node": {key: [run["id"] for run in current_runs if run["node_id"] == key]
                                                            for key in sorted(selected)},
                                       "ordering": "commit_seq is recording order; only causal_parents establish event causality"},
                "coverage": coverage, "module_hints": reuse_hints(facts)}
    catalog = content_catalog([{**fact, "content": full_materials[fact["id"]], "content_state": "available"}
                               if fact["id"] in full_materials else fact for fact in facts], archive)
    prepared["prompt_catalog"] = [{key: item[key] for key in ("id", "evidence_id", "pointer", "page_id")}
                                  for item in catalog["prompts"]]
    for entry, original in zip(prepared["prompt_catalog"], catalog["prompts"], strict=True):
        entry["preview"] = original["text"][:240]
    prepared["page_catalog"] = [{key: item[key] for key in
                                 ("id", "label", "page_number", "evidence_id", "pointer", "prompt_ids", "artifact_ids")}
                                for item in catalog["pages"]]
    for entry, original in zip(prepared["page_catalog"], catalog["pages"], strict=True):
        entry["description_preview"] = original["description"][:400]
    require(redact({"source": source, "evidence": evidence, "locations": locations,
                    "timeline": timeline, "nodes": sorted(selected)}) == {
                        "source": source, "evidence": evidence, "locations": locations, "timeline": timeline, "nodes": sorted(selected)},
            "Source identifiers contain sensitive content", "analysis_sensitive_identifiers")
    fit_model_input(prepared)
    if len(catalog["pages"]) > 12:
        prepared["output_strategy"] = "modules_only_pages_restored_from_source"
        prepared["output_token_budget"] = 8192
        coverage["issues"].append(issue("LARGE_PAGE_SOURCE_RESTORE",
            "页面较多，AI集中提炼共享模块；逐页内容、原始提示词与图片从源档案保留，本次不逐页生成AI扩展。"))
    prepared["input_sha256"] = digest(prepared)
    return ParseContext(archive_bytes, archive, history, prepared, evidence, node_evidence, catalog)
