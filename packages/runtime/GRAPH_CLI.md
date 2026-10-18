# 用自己的 A 档案测试 Graph 拆解

无需启动网站、上传平台或等待 C 的画布。命令复用生产准备器、DeepSeek 和图校验，输出同一个 `aica-semantic-graph/0.1`；本地命令不向平台登记项目、分支或修订。

使用现有 Conda `aica` 环境及已安装的 A/D 本地包，无新依赖。本机已配置的 DeepSeek 沿用仓库外配置；换机器时按 [配置说明](README.md#deepseek-本机配置)设置，不把密钥写入脚本或命令行。

## 输入是什么

可以传 `aica.json`、另名保存的 A 档案 JSON、包含 aica.json 的项目目录，或 A 的标准传输 ZIP。传 `.aica` 目录时读取其上级的 aica.json，不读取该目录的数据库/会话凭证。A 原始 JSON 与附件继续按原相对路径放置；单独拷走 JSON 可能缺少逐轮消息附件，缺项会显示在 coverage 中。

标准 ZIP 必须是 A 的 `aica bundle` 生成的 `aica-transfer/0.1`，普通压缩项目目录不能直接冒充标准包。原始档案、录制状态与附件全程不改写；解析只读取已声明且校验通过的本地历史，不访问外部链接。

## Windows PowerShell 命令

把下面输入路径替换成自己的档案路径。输出选一个新目录，必须在源项目和 Git checkout 外；现有目录不会被覆盖。

```powershell
conda activate aica
$runDir = Join-Path $env:LOCALAPPDATA 'AiCA\graph-tests\case-01'

python -m aica_runtime.graph preview "D:\你的项目\aica.json" --output "$runDir"
notepad (Join-Path $runDir 'prepared-input.json')
```

preview 不调用模型，只验证、整理并保存实际待发送的脱敏事实。检查选定范围、消息角色、参数和 coverage.issues；源文件和审阅输入在正式解析前发生变化时必须重新 preview。

preview 现在还会生成 `input-report.html`，可在 CMD 中执行 `start "" "实际输出目录\input-report.html"` 查看已读材料、正文和缺口。它是输入检查报告，不是模型拆解结果；真正空输入返回 analysis_empty，不会显示 prepared 成功或发送给模型。

只有附件、没有 graph.nodes/Run 的 A 档案也受支持：登记的创作附件会成为事实，校验哈希后读取有界 text/plain、Markdown 或 JSON 正文，图片/PDF 只读元数据。输入报告显示附件数量及实际读取正文数，并标注 ARTIFACT_ONLY_SOURCE。没有 recorded 消息时，附件里的“原始用户要求”仍作为角色未知的事后材料，不能冒充 message.recorded。

确认允许把这份输入发给 DeepSeek 后执行：

```powershell
python -m aica_runtime.graph parse "$runDir" --confirm-external
Start-Process (Join-Path $runDir 'report.html')
```

parse 是同步本地命令，等待一次模型响应。成功后目录包含：

| 文件 | 用途 |
| --- | --- |
| prepared-input.json | 实际外发的脱敏输入与哈希 |
| input-report.html | 第一步就可打开的本地输入检查报告，无模型调用 |
| request.json | 本次本地来源路径与临时图身份；不应提交 Git |
| provider-result.json | 模型提议、实际模型和用量，不含 API key 或 reasoning_content |
| graph.json | 已组装/校验的 C 消费格式 |
| report.html | 离线浏览器报告，含关系图、可展开步骤、调用/Run、变量及证据，无外部资源或脚本 |
| attempt.json | running/succeeded/failed/unknown、版本、用量及错误码 |

这是本地试验命名空间：graph.json 的 import/branch 身份用于绑定本次原始字节，不表示已经上库。正式网站使用 [平台解析 API](GRAPH_PARSER.md)取得实际平台身份。

## 怎么看拆解效果

打开 report.html：上方是步骤关系图，点击节点可跳到该步；展开详情查看它包含哪些原始节点和执行尝试。蓝色实线是 A 显式验证的依赖，橙色虚线是候选执行关系，灰色虚线是语义关系；候选解释仍需审核。

重点检查：

1. 同一输出的失败、修订和重试是否合理归组；独立产物是否被错误合并。
2. 用途/要求究竟来自用户、Agent 提议还是分析综合，有没有倒写来源。
3. 参数 locator、current_value 是否对应真实调用，原始 Prompt 和实际 Run 是否完整保留。
4. 数据依赖是否保留正确端口；没有证据的时间相邻调用是否被强行串联；coverage 是否有未归组或缺失历史。

画布布局仅为本地报告展示，不写入 GraphDocument，也不保存到平台。可将 graph.json 交给 C 的 transport/画布做格式联调；不把本地试验身份当平台在线资源。

## 较大档案和失败

选子图时重复给出 A 原始节点 ID：

```powershell
python -m aica_runtime.graph preview "D:\你的项目\aica.json" --output "$env:LOCALAPPDATA\AiCA\graph-tests\case-02" --node-id raw-node-1 --node-id raw-node-2
```

沿用生产输入预算；超限明确报错，不静默截断。一次 run 目录最多一次尝试；再次 parse 会被拒绝，以防重复计费。失败先读 attempt.json；invalid_semantic_graph 表示模型提议未通过校验，provider_connection/unknown 代表结果不明。若决定另试，创建新的输出目录并重新审阅，不删除已有 attempt.json 绕过保护。进程意外结束而仍为 running 时也按待核实处理。

解析会产生模型费用，只有 parse 加 --confirm-external 才外发。报告与参数仍可能包含作品私有内容，留在本地按需要分享。

模型失败现在在 `attempt.json.error_details` 中区分 `output_truncated`、`proposal_json`、`proposal_schema`、`graph_validation` 等阶段。收到可解析的响应封装但提议不合规时，另存本地 `provider-response.json`（脱敏的回答正文、finish_reason、用量和字段错误，不含请求凭证或 reasoning_content）。不要上传这些可能含作品内容的文件。旧版没有保存的回复无法事后恢复。

若确认是输出截断，可在**新准备目录**显式提高单次输出预算，不修改密钥配置，不自动重试：`python -m aica_runtime.graph parse "%RUN_DIR%" --confirm-external --max-output-tokens 8192`。省略此选项仍沿用配置，范围为 512..8192；提高预算可能增加费用。

附件型结果允许 source_node_ids/run_ids 为空，以 evidence_ids 关联登记附件，artifact_ids 表示有依据的关联材料；用途和步骤都仍是候选，不证明工具调用发生、执行成功或真实产出关系。没有原始执行参数时不开放变量。需要精确复现调用链时，A 必须提供原始节点/Run/消息记录；解析器不会根据成品编造历史。

原始正文读取累计预算为 96 KiB，最终输入仍受 128 KiB 上限约束；未读取正文用 issue 标记，绝不把图片 base64 或整个 PDF 塞入提示。大型内嵌图片的 exports/aica.json 可能超出 16 MiB 档案限制，优先使用项目根 aica.json 与其声明附件。修复/版本升级后应重新 preview，旧准备哈希不再适用。
## 本机图片关联

目录/JSON 输入生成的 `input-report.html` 会在图片材料中提供缩略图和“打开原图”链接，复用 A 的本地路径与哈希校验，仅接受 PNG/JPEG/WebP/GIF（单文件不超过 16 MiB）。链接指向原项目，不复制图片、不将图片加入模型输入；移动或删除原文件会使链接失效。ZIP 内图片暂不展开为本地链接。报告是输入准备快照，后续模型尝试结果仍以 `attempt.json` 为准。
