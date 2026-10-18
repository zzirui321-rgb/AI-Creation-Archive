from aica_schema.protocol import dumps, require

from ..evidence import INPUT_LIMIT


def preview(value, text_limit, depth=0):
    if isinstance(value, str) and len(value) > text_limit:
        third = text_limit // 3
        return value[:third] + "\n[中间内容省略；完整原文保存在源档案]\n" + value[len(value) // 2:len(value) // 2 + third] + "\n…\n" + value[-third:]
    if isinstance(value, dict):
        if depth > 12:
            return {"selection": "partial_nested_content", "keys": list(value)[:20]}
        return {key: preview(item, text_limit, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 32:
            return {"selection": "partial_array_head_middle_tail", "original_items": len(value),
                    "items": [preview(item, text_limit, depth + 1) for item in value[:12] + value[len(value) // 2:len(value) // 2 + 8] + value[-12:]]}
        return [preview(item, text_limit, depth + 1) for item in value]
    return value


def fit_model_input(prepared):
    original_bytes = len(dumps(prepared).encode())
    if original_bytes <= INPUT_LIMIT:
        return
    originals = [(fact.get("value"), fact.get("content")) for fact in prepared["facts"]]
    for limit in (4096, 1024, 256):
        for fact, (value, content) in zip(prepared["facts"], originals, strict=True):
            fact["value"] = preview(value, limit)
            if content is not None:
                fact["content"] = preview(content, limit)
                fact["content_preview"] = fact["content"] != content
        if len(dumps(prepared).encode()) <= INPUT_LIMIT:
            break
    prepared["coverage"]["issues"].append({
        "id": "issue-model-input-preview", "code": "MODEL_INPUT_PREVIEW", "severity": "warning",
        "message": "大档案使用有界内容预览发送给AI；保留全部事实引用、原始提示词和分页图片。省略内容不能视作AI已读取。",
        "node_ids": [], "evidence_ids": []})
    prepared["input_selection"] = {"mode": "bounded_previews_all_fact_references", "original_bytes": original_bytes,
                                   "fact_count": len(prepared["facts"])}
    require(len(dumps(prepared).encode()) <= 2 * 1024 * 1024,
            "材料引用目录超过2 MiB，请按创作项目拆分；原档案已保存", "analysis_limit")
