from collections import defaultdict
from html import escape
from pathlib import Path
from zipfile import is_zipfile

from aica_mcp.files import artifact_file, inspect_artifact
from aica_schema.protocol import dumps


def render_report(graph, prepared):
    def text(value):
        return escape(str(value), quote=True)

    def code(value):
        return "<pre>" + text(dumps(value, pretty=True)) + "</pre>"

    levels = {node["id"]: 0 for node in graph["nodes"]}
    verified = [edge for edge in graph["edges"] if edge["kind"] != "semantic" and edge["state"] == "verified"]
    for _ in graph["nodes"]:
        changed = False
        for edge in verified:
            level = levels[edge["source"]] + 1
            if levels[edge["target"]] < level:
                levels[edge["target"]], changed = level, True
        if not changed:
            break
    rows, positions = defaultdict(int), {}
    for node in graph["nodes"]:
        level = levels[node["id"]]
        positions[node["id"]] = (40 + level * 340, 35 + rows[level] * 130)
        rows[level] += 1
    width = max((x + 300 for x, _ in positions.values()), default=600)
    height = max((y + 100 for _, y in positions.values()), default=120)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="语义图" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#64748b"/></marker></defs>']
    for edge in graph["edges"]:
        x1, y1 = positions[edge["source"]]
        x2, y2 = positions[edge["target"]]
        solid = edge["kind"] != "semantic" and edge["state"] == "verified"
        color = "#2563eb" if solid else "#d97706" if edge["kind"] != "semantic" else "#64748b"
        dash = "" if solid else ' stroke-dasharray="7 5"'
        svg.append(f'<path d="M{x1 + 250},{y1 + 35} C{x1 + 305},{y1 + 35} {x2 - 40},{y2 + 35} {x2},{y2 + 35}" '
                   f'fill="none" stroke="{color}" stroke-width="2"{dash} marker-end="url(#arrow)"><title>{text(edge)}</title></path>')
    for index, node in enumerate(graph["nodes"], 1):
        x, y = positions[node["id"]]
        svg.append(f'<a href="#step-{index}"><rect x="{x}" y="{y}" width="250" height="76" rx="10" fill="#eff6ff" stroke="#93c5fd"/>'
                   f'<text x="{x + 12}" y="{y + 28}" font-size="14">{index}. {text(node["label"][:18])}</text>'
                   f'<text x="{x + 12}" y="{y + 53}" font-size="12" fill="#475569">{text(node["kind"])} · {len(node["source_node_ids"])} 调用 · {len(node["artifact_ids"])} 附件</text></a>')
    svg.append('</svg>')
    facts = {fact["id"]: fact for fact in prepared["facts"]}
    sections = []
    for index, node in enumerate(graph["nodes"], 1):
        purpose = node["purpose"]
        identifiers = set(node["evidence_ids"]) | set(purpose["evidence_ids"])
        for claim in node["requirements"]:
            identifiers.update(claim["evidence_ids"])
        related = [fact for key, fact in facts.items() if key in identifiers or set(fact["node_ids"]) & set(node["source_node_ids"])]
        variables = [variable for variable in graph["variables"] if variable["step_id"] == node["id"]]
        sections.append(f'<section id="step-{index}"><h2>{index}. {text(node["label"])}</h2>'
                        f'<p>{text(purpose["text"])}</p><p class="muted">归因：{text(purpose["attributed_to"])} · '
                        f'{text(purpose["origin"])} / {text(purpose["review"])}</p>'
                        '<details><summary>步骤身份、原始调用、Run 和产物</summary>' + code(node) + '</details>'
                        '<details><summary>变量与当前值</summary>' + code(variables) + '</details>'
                        '<details><summary>原始事实与消息证据（脱敏）</summary>' + code(related) + '</details></section>')
    messages = [fact for fact in prepared["facts"] if "event" in fact]
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>AiCA Graph 拆解报告</title><style>body{font:16px system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#172033;background:#f8fafc}'
            'section{background:white;padding:20px;margin:18px 0;border:1px solid #e2e8f0;border-radius:12px}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f1f5f9;padding:12px}'
            'summary{cursor:pointer;padding:10px 0}svg{display:block;min-width:600px} .canvas{overflow:auto}.muted{color:#64748b}</style>'
            '<h1>AiCA Graph 拆解报告</h1><p>此页为本地解析结果，模型解释待审核；未改写 A 原始档案，也不代表已执行工作流。</p>'
            f'<p>{len(graph["nodes"])} 个创作步骤 · {len(graph["edges"])} 条关系 · {len(graph["variables"])} 个变量</p>'
            '<p>蓝色实线：已验证执行依赖；橙色虚线：候选执行关系；灰色虚线：语义关系。箭头方向遵循 relation 定义，不等同于时间顺序。</p>'
            '<div class="canvas">' + ''.join(svg) + '</div><section><h2>覆盖范围与缺口</h2>' + code(graph["coverage"]) + '</section>'
            + ''.join(sections) + '<section><h2>全部关系</h2>' + code(graph["edges"]) + '</section>'
            '<section><h2>消息时间线（提交顺序不是因果顺序）</h2>' + code(messages) + '</section>'
            '<section><h2>解析身份与版本</h2>' + code({"source": graph["source"], "parse": graph["parse"]}) + '</section></html>')


def local_image_links(source, archive):
    source = Path(source)
    if is_zipfile(source):
        return {}
    links = {}
    for artifact in archive["artifacts"]:
        if artifact["mime_type"] not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            continue
        report = inspect_artifact(source.parent, artifact, 16 * 1024 * 1024)
        for location in report["locations"]:
            if location["state"] == "verified":
                links[artifact["id"]] = artifact_file(source.parent, location["uri"]).as_uri()
                break
    return links


def render_input_report(prepared, image_links=None):
    def code(value):
        return '<pre>' + escape(dumps(value, pretty=True)) + '</pre>'

    materials = [fact for fact in prepared["facts"] if fact.get("artifact_id")]
    bodies = sum(fact.get("content_state") == "available" for fact in materials)
    cards = []
    for fact in sorted(prepared["facts"], key=lambda item: item.get("content_state") != "available"):
        content = fact.get("content")
        title = fact.get("artifact_id") or fact["id"]
        if fact.get("content_state") == "available":
            if isinstance(content, dict):
                title = "JSON 正文 · " + str(content.get("title") or content.get("record_kind") or title)
            elif isinstance(content, list):
                title = f"JSON 列表 · {len(content)} 项 · " + title
            elif isinstance(content, str):
                title = "文本正文 · " + content.splitlines()[0][:80] if content.splitlines() else "空文本"
        image = ""
        url = (image_links or {}).get(fact.get("artifact_id"))
        if url and url.startswith("file:///"):
            href = escape(url, quote=True)
            image = (f'<p><a href="{href}" target="_blank" rel="noopener">打开原图</a></p>'
                     f'<a href="{href}" target="_blank" rel="noopener"><img src="{href}" '
                     f'alt="{escape(title, quote=True)}" loading="lazy"></a>')
        cards.append('<details><summary>' + escape(title) + '</summary>' + image + code(fact) + '</details>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src file:; style-src \'unsafe-inline\'">'
            '<title>AiCA 输入检查报告</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:0 20px}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:16px}summary{cursor:pointer;padding:12px}'
            'details{border:1px solid #cbd5e1;margin:10px 0}img{max-width:100%;max-height:420px;object-fit:contain}'
            '</style><h1>AiCA 输入检查报告</h1>'
            '<p>本页是输入准备阶段的快照（该阶段尚未调用 DeepSeek），不包含模型生成的创作步骤；后续解析尝试状态以 attempt.json 为准。</p>'
            '<p>展开图片附件可查看缩略图并打开原图。链接指向本机经哈希核对的文件，移动原项目后可能失效；图片未因此发送给模型。</p>'
            f'<p>原始节点 {len(prepared["source_node_ids"])} 个 · 事实 {len(prepared["facts"])} 项 · 创作附件 {len(materials)} 个 · 已读文本/JSON {bodies} 份</p>'
            '<h2>覆盖范围与缺失来源</h2>' + code(prepared["coverage"]) + '<h2>已读取的材料与证据</h2>' + ''.join(cards) + '</html>')
