# D 语义分析

## Graph 0.1 解析模块

直接输入自己的 A 档案测试：`python -m aica_runtime.graph preview ...` / `parse ...`，步骤见 [本地测试命令](GRAPH_CLI.md)，成功生成 graph.json 和浏览器拆解报告。

新版解析、异步状态、持久化取图与证据接口已由 `D-GRAPH-PARSER-20261017` 接入 Gateway，C 的实际调用见 [GRAPH_PARSER.md](GRAPH_PARSER.md)，消费格式见 [接口包](design/graph-v0.1/C_HANDOFF.md)。Schema/TS 保持原 0.1 格式。实现位于任务分支，尚未公网部署或与 C 实际页面联调；编辑/布局服务仍待实现。下文继续说明独立保留的 T018 报告接口。

## 现有报告实现

T018，分支 `feature/d-semantic-analysis`。在 A 的可追溯事实基础上，调用 DeepSeek 生成创作意图、步骤用途、候选依赖、可开放变量和有成功记录支持的路径建议。共享档案结构继续消费 `aica_schema`，不修改 A 的协议或采集器。

## 处理流程

1. 固定平台 `import_id`，读取并验证原档案；选择最多 50 个节点。
2. 读取节点、Run、相关产物元数据及有限的消息/说明事件。事实带档案 JSON Pointer 或事件附件 ID/行号，保留原始内容摘要。
3. 解包字面量输入和 JSON 字符串参数，整理模型输入；常见凭证字段、sk 格式密钥、Bearer 和签名 URL 脱敏。排除 system 消息和明确的私有推理字段。自由文本隐私仍需调用者审阅。
4. 返回待发送事实及 `input_sha256`；提交分析时必须携带该哈希并明确授权发送给 DeepSeek。
5. 模型通过 JSON Output 返回 [SemanticDraft](src/aica_runtime/contracts.py)。校验字段、已知节点、证据引用、变量路径和组合依赖 DAG。
6. 平台独立保存待确认的语义报告和输入。原始档案、历史、产物及项目 head 均保持原状。

事实输入格式为 `aica-semantic-input/0.1`；派生报告格式为 `aica-semantic-report/0.1`。它们是 D 服务的派生接口，不是另一套 `.aica` 协议。前端通过本模块契约及平台 API 消费，不复制归档类型。

## 推断边界

- 所有模型结论标记 `provenance: inferred`、`requires_review: true`、`executable: false`。证据引用校验只能确认引用存在及部分结构约束，不能证明模型解释必然正确。
- dependency 的 data/control/semantic 是待确认的语义建议。缺少端口及绑定时不能直接当作可执行 data edge。
- 模型给出变量名称和事实内的 JSON Pointer；服务端从事实取回实际值和类型，不接受模型编造的当前值。指针指向分析事实的 value，不是可直接写回 `.aica` 的路径。JSON 字符串解包等转换需由后续确认/编译层处理。
- allowed_modes 表示 A 协议按值类型允许的操作，不保证每项操作都适合文件路径等业务语义。数字、布尔值和对象没有 Append。
- Golden Path 仅接受当前修订、active 分支的最新成功 Run，按实际开始时间选取最新尝试；不能用数组顺序覆盖失败，不能用日志中的任意成功字样替代 Run。路径还需引用成功 Run 事实、依赖闭合且拓扑有序。
- 观察不足时保留问题、覆盖范围、未解释节点和录制缺口。最多 400 项事实、128 KiB 模型输入、16 个事件分段/累计 1 MiB 历史；超限显式拒绝或在覆盖范围记录未读取历史。选择子图可能缺少外部依赖，不声称完整复现。
- 本阶段没有语义报告确认写回、Fork、缓存、执行器、自动重跑或自然语言代码执行。模型输出文本应按普通文本显示。

## DeepSeek 本机配置

本次通过真实 `GET https://api.deepseek.com/models` 确认可用模型为 `deepseek-flash`、`deepseek-v4-pro`，选择 `deepseek-flash`。使用 [官方 Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)，关闭 thinking，不请求工具，不存储 reasoning_content。

默认配置位于本机用户目录：Windows 为 `%LOCALAPPDATA%/AiCA/secrets/semantic-provider.json`；其他系统为 `~/.config/AiCA/secrets/semantic-provider.json`。可通过 `AICA_SEMANTIC_CONFIG` 指向仓库外配置文件。此会话提供的凭证已写入 Windows 默认路径，权限限制为当前用户和 SYSTEM，不在 Git 中。

配置结构（占位符不是有效凭证）：

```json
{
  "provider": "deepseek",
  "endpoint": "https://api.deepseek.com/chat/completions",
  "model": "deepseek-flash",
  "api_key": "YOUR_PRIVATE_DEEPSEEK_KEY",
  "max_tokens": 4096,
  "timeout_seconds": 60
}
```

服务端固定 DeepSeek 官方 HTTPS 端点，校验证书，不跟随重定向或继承环境代理。单次输出预算最大 8192 tokens（当前配置 4096），响应最大 512 KiB，无自动重试。每个项目同时只执行一次分析。调用方需要单独审阅分享范围，不能把原有录制授权当成外发授权。

## 平台 API

使用原有平台 Bearer 认证。主体和项目隔离覆盖输入预览、分析列表和详情。

| 方法和路径 | 含义 |
| --- | --- |
| `GET /api/semantic-provider` | 是否配置、模型和预算；不返回凭证 |
| `GET /api/projects/{p}/analysis-input?import_id=...` | 返回实际将发送的脱敏事实和 input_sha256；可重复传 node_ids 选择节点 |
| `POST /api/projects/{p}/analyses` | 调用模型、校验并保存报告 |
| `GET /api/projects/{p}/analyses?offset=0&limit=50` | 分页列出当前主体的分析 |
| `GET /api/projects/{p}/analyses/{a}` | 分析状态、模型、报告和安全错误码 |

POST 请求：

```json
{
  "request_id": "stable-request-id",
  "import_id": "UUID_FROM_IMPORT_RECEIPT",
  "input_sha256": "HASH_FROM_ANALYSIS_INPUT",
  "node_ids": null,
  "allow_external_processing": true
}
```

必须先明确获得将选定事实发送给 DeepSeek 的授权；布尔参数本身不能证明调用者已取得人的授权。审阅与提交使用相同的 import_id/node_ids。首次完成返回 201；相同请求和内容返回已有记录，完成时为 200、运行中为 202。同 ID 不同内容或同项目另有运行中分析返回 409。

分析状态为 running/succeeded/failed/unknown；报告自身的 state 为 draft。网络中断或重启后的未确认尝试标 unknown，不自动再次请求模型。失败记录可查询；修正配置后如需新尝试须使用新的 request_id。同一请求重试不会重新计费调用模型。

403 表示未授权外发；409 表示审阅输入/请求身份冲突或项目繁忙；413 表示分析预算超限；502 表示模型拒绝请求或输出无效；503 表示配置、连接或存储不可用。失败不返回供应商原始错误体或凭证，也不会自动退化为规则分析后冒充模型成功。

## 验证

使用既有 `aica` 环境，不新增或升级第三方依赖。新增本地包后：

```sh
python -m pip install --no-build-isolation --no-deps -e packages/runtime -e apps/gateway
python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests
python -m ruff check packages/runtime apps/gateway
```

普通测试使用 HTTPX MockTransport，不调用真实模型。覆盖真实 A 日志提取器生成的事实、证据/值/环校验、最新尝试、Golden Path、凭证脱敏、平台权限、并发幂等、失败和重启。测试里的图及工具返回都是合成记录，不代表实际调用了生图工具。

仅明确授权合成事实外发时运行真实联调：

```sh
python packages/runtime/tests/run_live_deepseek.py --confirm-external --output /OUTSIDE_OR_REVIEWED_PATH/result.json
```

脚本拒绝覆盖现有输出，执行 A 提取 → 打包导入 → 审阅事实 → 真实 DeepSeek → 平台查询 → 幂等重试 → 原档案不变的完整验证。一次成功调用的 [结果快照](tests/evidence/deepseek-live.json) 已保存：3 个步骤、3 条候选依赖、6 个变量；4653 tokens。数据全部为合成事实，实际生图工具没有执行，未发送项目真实日志或私有附件。

最新专项、完整回归及已有 Windows 符号链接限制见 [STATUS](../../docs/STATUS.md)。

## 真实 MCP 生图入库实测（T019）

本次实际调用内置 image_gen.imagegen 生成了黑猫宇航员水彩图，使用 MCP SDK 通过 stdio 连接已安装的 `aica serve`，由 Host 显式转交本次任务输入、开始/结束和图片产物。题材由测试 Host 选定，用户授权的是实际链路测试。未安装聊天工具自动监听 Hook，也未修改客户端全局 MCP 配置。档案执行类型 manual 表示尚无可移植的重放工具绑定；本次图像确由真实生成工具产生。

随后通过真实本机 Uvicorn HTTPS 服务及 A 的 upload_bundle 上传档案/PNG，调用真实 DeepSeek 并保存结果，直接查询 SQLite 和重启 API 核验。图片为 3,369,178 字节；得到 1 个步骤、prompt 和 transparent_background 两个变量，以及有成功 Run 支持的单节点路径建议。单次模型调用 3957 tokens，未重放生图。

人工复核发现模型意图中将 Host 选择的黑猫题材写成“用户希望”，存在归因过度。保留原模型输出并在核验摘要标记 review_notes；未改写原档案或数据库来隐藏该问题。成功落库只说明链路及结构校验通过，报告仍需审核。

可重复使用的手动分阶段脚本：

```sh
python packages/runtime/tests/run_mcp_image_e2e.py begin --root /PRIVATE_NEW_RUN_DIR --confirm-record
# 读取 RUN_DIR/prompt.json，实际调用图像生成工具，并保留其真实 PNG 返回文件。
python packages/runtime/tests/run_mcp_image_e2e.py complete --root /PRIVATE_NEW_RUN_DIR --image /ACTUAL_GENERATED_IMAGE.png --confirm-record
python packages/runtime/tests/run_mcp_image_e2e.py platform --root /PRIVATE_NEW_RUN_DIR --confirm-external
```

RUN_DIR 须提前建立在仓库外并限制为当前用户/SYSTEM 可访问，所有参数使用该次实际路径。begin 只启动记录，不执行生图；complete 的图片必须来自本次实际工具调用，由 Host 对来源负责。platform 保留数据库和模型尝试文件，已有请求时拒绝再次付费调用；失败应检查已有结果后再决定恢复，不编造成功。

公开核验摘要见 [mcp-image-live.json](tests/evidence/mcp-image-live.json)。实际源图、档案、传输包、SQLite、逐次 MCP 回执、模型原始结果、独立数据库检查脚本和 REPORT.md 保存在本机本次运行目录；定位见 STATUS。私有状态和凭证不进入 Git。该测试仅证明本机显式转交链路，不代表自动捕获、跨机器同步或公网部署已验收。
