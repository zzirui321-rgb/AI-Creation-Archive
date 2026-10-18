import hashlib

from aica_mcp.extraction import parse_agent_log
from aica_schema.protocol import dumps, empty_archive


def sample_archive():
    records = [
        {"type": "message", "content": "制作一页漫画：先生成黑猫宇航员角色图，再以这张图为参考绘制月球分镜，保持角色一致。"},
        {"type": "tool_call", "id": "character-call", "name": "image.generate",
         "arguments": {"prompt": "黑猫宇航员，水彩风格", "seed": 42, "output": "character.png"}},
        {"type": "tool_result", "call_id": "character-call", "output": {"path": "character.png"}},
        {"type": "tool_call", "id": "panel-call", "name": "comic.panel",
         "arguments": {"reference_image": "character.png", "prompt": "同一只黑猫在月球行走", "output": "panel.png"}},
        {"type": "tool_result", "call_id": "panel-call", "output": {"path": "panel.png"}},
    ]
    raw = "".join(dumps(record) + "\n" for record in records).encode("utf-8")
    archive = empty_archive("Synthetic semantic-analysis fixture")
    archive["artifacts"] = [{"id": "source-log", "uri": "trace.jsonl", "mime_type": "application/x-ndjson",
                             "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw), "availability": "available",
                             "producer_node_id": None, "producer_run_id": None}]
    archive["graph"]["nodes"] = parse_agent_log(raw.decode(), "normalized", "trace.jsonl", "source-log")["nodes"]
    archive["metadata"]["source_mode"] = "extracted"
    return archive, raw


def sample_draft(prepared):
    nodes = {fact["value"]["label"]: fact for fact in prepared["facts"] if fact["id"].startswith("node:")}
    character, panel = nodes["image.generate"], nodes["comic.panel"]
    source = nodes["Conversation message"]
    return {
        "intent": {"text": "创作角色一致的黑猫宇航员月球漫画。", "evidence_ids": [source["id"]]},
        "steps": [{"node_id": fact["node_ids"][0], "title": title, "text": purpose, "evidence_ids": [fact["id"]]}
                  for fact, title, purpose in ((source, "设定创作目标", "给出漫画主题和一致性要求。"),
                                               (character, "生成角色参考", "建立可供分镜引用的角色图。"),
                                               (panel, "绘制月球分镜", "以角色图为参考保持角色一致。"))],
        "dependencies": [{"source": character["node_ids"][0], "target": panel["node_ids"][0], "kind": "data",
                          "text": "后一步 reference_image 与前一步观测返回路径相同。",
                          "evidence_ids": [character["id"], panel["id"]], "confidence": "high"}],
        "variables": [{"name": "character_prompt", "fact_id": character["id"],
                       "pointer": "/execution/inputs/arguments/prompt", "text": "允许修改角色外观和画风。",
                       "evidence_ids": [character["id"]]}],
        "golden_path": {"node_ids": [], "text": "这里只有日志提取结果，没有当前修订的成功 Run 证据。",
                        "evidence_ids": ["snapshot"]},
        "questions": [{"text": "需要确认角色图和分镜产物是否实际存在，以及执行环境。",
                       "evidence_ids": [character["id"], panel["id"]]}],
    }


def attach_runs(archive, attempts):
    archive["sessions"] = [{"id": "test-session", "state": "closed", "created_at": "2026-10-17T00:00:00Z",
                             "consent": {"record": True, "include_children": False,
                                         "decided_at": "2026-10-17T00:00:00Z"}}]
    archive["harnesses"] = [{"id": "test-harness", "name": "Synthetic fixture", "version": None,
                              "config": {}, "state": {}, "availability": {}}]
    archive["agents"] = [{"id": "test-agent", "session_id": "test-session", "parent_agent_id": None,
                           "harness_id": "test-harness", "client": "synthetic", "model": {}}]
    archive["runs"] = [{"id": f"test-run-{index}", "trace_id": "test-trace", "node_id": node,
                         "revision_id": archive["graph"]["revision_id"], "session_id": "test-session",
                         "agent_id": "test-agent", "harness_id": "test-harness", "state": state,
                         "started_at": timestamp, "ended_at": "2026-10-17T23:59:00Z",
                         "inputs": {}, "outputs": {}, "artifact_ids": [],
                         "provenance": {"mode": "recorded", "evidence": [{"source": "synthetic-test"}]}}
                        for index, (node, state, timestamp) in enumerate(attempts)]
    return archive
