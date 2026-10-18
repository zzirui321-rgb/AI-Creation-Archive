import copy

from aica_schema.protocol import dumps, empty_archive, require_archive
from aica_schema.recording import require_event

from .validation import sha256


def claim(text, evidence_ids, role="analyst", speech="interpretation"):
    return {"text": text, "attributed_to": role, "actor_id": "fixture-agent" if role == "agent" else None,
            "speech_act": speech, "evidence_ids": evidence_ids, "origin": "human", "review": "accepted"}


def make_case(name="multiturn"):
    archive = empty_archive("Synthetic graph contract case", "fixture-project", "fixture-stream")
    archive["metadata"].update(created_at="2026-10-17T00:00:00Z", updated_at="2026-10-17T00:10:00Z")
    archive["graph"]["revision_id"] = "raw-revision-1"
    archive["snapshot"].update(snapshot_id="fixture-snapshot", commit_seq=4)
    archive["extensions"] = {"aica.fixture": True}
    archive["sessions"] = [{"id": "fixture-session", "state": "closed", "created_at": "2026-10-17T00:00:00Z",
                             "consent": {"record": True, "include_children": False,
                                         "decided_at": "2026-10-17T00:00:00Z"}}]
    archive["harnesses"] = [{"id": "fixture-harness", "name": "Synthetic only", "version": None,
                              "config": {}, "state": {}, "availability": {}}]
    archive["agents"] = [{"id": "fixture-agent", "session_id": "fixture-session", "parent_agent_id": None,
                           "harness_id": "fixture-harness", "client": "synthetic", "model": {}}]
    provenance = {"mode": "recorded", "evidence": [{"source": "synthetic-contract-fixture"}]}
    nodes = [{"id": "raw-plan", "kind": "prompt", "label": "创作要求", "branch_id": "main",
              "provenance": copy.deepcopy(provenance)}]
    definitions = [
        ("raw-character-first", "image.generate", "黑猫宇航员，水彩风格", "image"),
        ("raw-character-revised", "image.generate", "黑猫宇航员，红色宇航服，水彩风格", "image"),
        ("raw-panel", "comic.panel", "用选定角色图画月球分镜", "image"),
        ("raw-audio", "audio.generate", "太空氛围配乐", "audio"),
    ]
    for node_id, tool, prompt, output in definitions:
        inputs = {"arguments": {"kind": "literal", "value": dumps({"prompt": prompt, "seed": 42})}}
        if node_id == "raw-panel":
            inputs["reference_image"] = {"kind": "node_output", "node_id": "raw-character-revised", "output": "image"}
        nodes.append({"id": node_id, "kind": "model", "label": tool, "branch_id": "main",
                      "provenance": copy.deepcopy(provenance), "execution": {
                          "kind": "mcp", "tool": tool, "version": "fixture-1", "inputs": inputs,
                          "params": {}, "outputs": [output]}})
    archive["graph"]["nodes"] = nodes
    archive["graph"]["edges"] = [{"id": "raw-character-panel", "source": "raw-character-revised",
                                    "target": "raw-panel", "kind": "data", "source_output": "image",
                                    "target_input": "reference_image"}]
    messages = [
        ("user", "生成一页太空漫画，再配一段音乐。"),
        ("assistant", "我选择黑猫宇航员和水彩风格，先生成角色图。"),
        ("user", "保留黑猫设定，改成红色宇航服，再用新图画月球分镜。"),
        ("tool", "初次角色图调用失败；修订角色图、分镜和配乐的 fixture 结果见 Run。"),
    ]
    events = []
    for index, (role, content) in enumerate(messages, 1):
        event = {"event_id": f"event-{index}", "source_id": "fixture-host", "source_seq": index,
                 "session_id": "fixture-session", "agent_id": "fixture-agent" if role == "assistant" else None,
                 "type": "message.recorded", "timestamp": f"2026-10-17T00:0{6 if index == 4 else index}:00Z",
                 "causal_parents": [f"event-{index - 1}"] if index > 1 else [],
                 "payload": {"role": role, "content": content}}
        require_event(event)
        events.append({"stream_id": "fixture-stream", "commit_seq": index, "event": event})
    history_bytes = "".join(dumps(event) + "\n" for event in events).encode()
    history_artifact = {"id": "fixture-history", "uri": "events.jsonl", "mime_type": "application/x-ndjson",
                        "sha256": sha256(history_bytes), "size": len(history_bytes), "availability": "available",
                        "producer_node_id": None, "producer_run_id": None}
    archive["artifacts"] = [history_artifact]
    archive["history"] = [{"kind": "events", "artifact_id": "fixture-history", "through_seq": 4}]
    for index, (node_id, _, _, output) in enumerate(definitions, 1):
        failed = node_id == "raw-character-first"
        artifact_ids = [] if failed else ["artifact-" + node_id]
        if not failed:
            archive["artifacts"].append({"id": artifact_ids[0], "uri": f"outputs/{node_id}.bin",
                                         "mime_type": "image/png" if output == "image" else "audio/wav",
                                         "sha256": None, "availability": "unverified",
                                         "producer_node_id": node_id, "producer_run_id": "run-" + node_id})
        run = {"id": "run-" + node_id, "trace_id": "fixture-trace", "node_id": node_id,
               "revision_id": "raw-revision-1", "session_id": "fixture-session", "agent_id": "fixture-agent",
               "harness_id": "fixture-harness", "state": "failed" if failed else "succeeded",
               "started_at": f"2026-10-17T00:0{index + 1}:10Z", "ended_at": f"2026-10-17T00:0{index + 1}:20Z",
               "inputs": {}, "outputs": {} if failed else {output: {"artifact_id": artifact_ids[0]}},
               "artifact_ids": artifact_ids, "provenance": copy.deepcopy(provenance)}
        archive["runs"].append(run)
    if name == "partial":
        archive["snapshot"]["complete"] = False
        archive["snapshot"]["gaps"] = [{"code": "source_sequence_gap", "source_id": "other-agent"}]
    elif name == "parallel":
        archive["runs"][-1]["started_at"] = archive["runs"][1]["started_at"]
        archive["runs"][-1]["trace_id"] = "parallel-audio-trace"
    require_archive(archive)
    archive_bytes = (dumps(archive, pretty=True) + "\n").encode()
    evidence = [{"kind": "archive", "id": f"e-node-{index}", "pointer": f"/graph/nodes/{index}",
                 "source_sha256": sha256(archive_bytes)} for index in range(len(nodes))]
    evidence += [{"kind": "event", "id": f"e-message-{index}", "artifact_id": "fixture-history", "line": index,
                  "event_id": f"event-{index}", "pointer": "/payload/content", "source_sha256": sha256(history_bytes)}
                 for index in range(1, 5)]
    groups = [
        ("step-plan", "intent", "确定创作目标", [0], claim("用户要求太空漫画与配乐。", ["e-message-1"], "user", "request")),
        ("step-character", "generation", "生成并修订角色图", [1, 2],
         claim("Agent 最初选择黑猫与水彩，用户随后保留黑猫并要求红色宇航服。", ["e-message-2", "e-message-3"])),
        ("step-panel", "generation", "生成月球分镜", [3], claim("用户要求使用新角色图绘制月球分镜。", ["e-message-3"], "user", "request")),
        ("step-audio", "generation", "生成配乐", [4], claim("用户要求为作品配乐。", ["e-message-1"], "user", "request")),
    ]
    steps = []
    for step_id, kind, label, members, purpose in groups:
        owned = [nodes[index]["id"] for index in members]
        steps.append({"id": step_id, "kind": kind, "label": label, "purpose": purpose, "requirements": [],
                      "source_node_ids": owned, "run_ids": [run["id"] for run in archive["runs"] if run["node_id"] in owned],
                      "artifact_ids": [item["id"] for item in archive["artifacts"] if item["producer_node_id"] in owned],
                      "evidence_ids": [f"e-node-{index}" for index in members] + purpose["evidence_ids"]})
    result = {"format": "aica-semantic-graph/0.1", "graph_id": "fixture-graph", "revision_id": "semantic-revision-1",
              "parent_revision_id": None, "repository_branch_id": "repo-main", "state": "draft", "executable": False,
              "source": {"import_id": "fixture-import", "archive_sha256": sha256(archive_bytes),
                         "archive_revision_id": "raw-revision-1", "snapshot_id": "fixture-snapshot",
                         "stream_id": "fixture-stream", "through_seq": 4},
              "parse": {"parser_version": "design-0.1", "prompt_version": "design-0.1", "model": None,
                        "mode": "human_annotated_fixture", "input_sha256": sha256(archive_bytes)},
              "nodes": steps,
              "edges": [{"kind": "data", "id": "edge-character-panel", "source": "step-character", "target": "step-panel",
                         "state": "verified", "archive_edge_id": "raw-character-panel", "binding": {
                             "source_node_id": "raw-character-revised", "source_output": "image",
                             "target_node_id": "raw-panel", "target_input": "reference_image"},
                         "explanation": claim("A 定义明确的 image → reference_image 绑定。", ["e-node-2", "e-node-3"])}],
              "variables": [{"id": "var-character-prompt", "step_id": "step-character", "label": "角色生成提示词",
                             "description": claim("可修改修订角色图的提示词。", ["e-node-2"]),
                             "locator": {"source_node_id": "raw-character-revised", "evidence_id": "e-node-2",
                                         "pointer": "/execution/inputs/arguments/value", "decode": "json_string",
                                         "value_pointer": "/prompt"}, "value_type": "string",
                             "current_value": "黑猫宇航员，红色宇航服，水彩风格", "allowed_modes": ["keep", "override"],
                             "separator": None, "review": "candidate"}],
              "evidence": evidence,
              "coverage": {"recording_complete": archive["snapshot"]["complete"], "selected_source_node_ids": [node["id"] for node in nodes],
                           "unassigned_source_node_ids": [], "omitted_history_artifact_ids": [], "issues": []}}
    if name == "partial":
        result["coverage"]["issues"].append({"id": "issue-gap", "code": "SOURCE_GAP", "severity": "warning",
                                              "message": "源录制存在序号缺口，不能宣称完整。", "node_ids": [], "evidence_ids": []})
    return {"name": name, "archive": archive, "archive_bytes": archive_bytes,
            "history": {"fixture-history": history_bytes}, "events": events, "graph": result}


def make_proposal(case):
    graph = copy.deepcopy(case["graph"])
    for node in graph["nodes"]:
        node["purpose"].update(origin="llm", review="candidate")
    groups = [{"key": node["id"], **{key: node[key] for key in (
        "kind", "label", "source_node_ids", "purpose", "requirements", "evidence_ids")}} for node in graph["nodes"]]
    edge = graph["edges"][0]
    edge["explanation"].update(origin="llm", review="candidate")
    variable = graph["variables"][0]
    variable["description"].update(origin="llm", review="candidate")
    return {"groups": groups, "edges": [{"source_key": edge["source"], "target_key": edge["target"], "kind": "data",
                                          "explanation": edge["explanation"]}],
            "variables": [{"group_key": variable["step_id"], "label": variable["label"], "locator": variable["locator"],
                           "description": variable["description"]}], "unresolved": []}
