# aica-mcp

推荐使用[项目首次授权](USAGE.md#项目首次授权登录一次后续自动处理)：`aica connect --server https://your-platform.example` 打开本机登录页，选择项目并确认后自动录制、登记任务产物和上传。`aica pause`、`aica resume`、`aica disconnect` 管理项目授权。凭证保存在工作区外，无需复制令牌或重启 MCP；首次升级仍需重启旧协调器。未启用项目授权时保留逐轮模式。

连接页支持选择作品并启用自动双向同步，平台保存后可备份并更新本地档案和已登记源文件；冲突暂停并保留两端。用 `aica sync status|pause|resume|resolve` 或 `aica_sync_control` 管理，见 [双向同步说明](USAGE.md#自动双向同步)。

逐轮模式的上传确认、图片关联与覆盖拉取见 [完整传输指南](USAGE.md#逐轮录制与完整传输)。`aica_pull_platform` 已纳入 MCP 工具；新版协调器会检查确认接口兼容性。

提供本地 stdio MCP 服务、单项目录制协调器、持久化待确认队列、命令采集和显式日志提取。多个 MCP 进程自动连接同一个本地协调器，协议类型来自 `aica-schema`。

日常使用请先看 [中文使用说明](USAGE.md)：包含录制话术、图片归档检查、导出上传、平台 JSON 导入、继续创作和常见问题。本页保留安装、开发接入和接口细节。

后端 D 接手此分支请先读 [后端交接文档](BACKEND_HANDOFF.md)，包含可复用接口、执行回写流程、职责边界与验收清单。

平台上传见 [.aicaconfig 使用与上传接口契约](PLATFORM_UPLOAD.md)：逐轮模式每次确认固定快照，手动 Session 模式可显式授权后台上传。图片字节与完整归档一起传输，失败保留原包重试。真实平台验证结果见 [T032](../../tasks/T032.json)。

逐轮询问提供六类客户端适配、严格门控与本机确认页。使用 `serve --project . --per-turn --client <客户端标识>`，接入、覆盖差异及验收见 [多客户端接入](AGENT_SETUP.md)。Codex 原有 `--codex-turns` 参数仍可用；客户端原生验收状态不能用配置生成替代。

清单中本包为 capture-package，属于 capture-reconstruction；协议依赖为 archive-contract。范围从 [collaboration.json](../../collaboration.json) 解析，T004 记录既有候选交付，D 的后续任务为 T005/T006；导入记录不表示这些任务已合入或被新执行者认领。

## 安装

在 AiCA 仓库根目录执行：

```sh
conda activate aica
pip install --requirement requirements.txt
pip install --no-build-isolation -e packages/schema -e packages/mcp-adapters
pip check
```

录制器需要 SQLite >=3.51.3，Conda 环境文件包含相应约束。检查实际链接版本：

```sh
python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
```

## 启动与客户端接入

```sh
aica serve --project /absolute/path/to/user-project
```

这是 stdio MCP 服务，标准输出仅用于 MCP 消息。项目必须已存在。后台协调器绑定随机的 `127.0.0.1` 端口，使用本机私有凭证和项目独占锁；它是本地内部接口，不作为平台公网 API。

Codex：将以下可执行文件路径替换为当前 Conda 环境内 `aica` 的绝对路径。

```sh
codex mcp add aica -- /absolute/path/to/aica serve --project /absolute/path/to/user-project
codex mcp list
```

配置方式核对自 [OpenAI 官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) 和本机 CLI 帮助。

Claude Code：

```sh
claude mcp add --transport stdio aica -- /absolute/path/to/aica serve --project /absolute/path/to/user-project
```

配置方式核对自 [Claude Code 官方说明](https://code.claude.com/docs/en/mcp)。Windows 使用 Conda 环境内 `Scripts/aica.exe` 的实际绝对路径。当前仓库不自动修改任何客户端配置。

其他支持 stdio 的 MCP Host 可以使用相同可执行文件和参数。服务可以接收这些客户端显式提交的事件；自动观察客户端原生工具需要对应的 Hook / Gateway 集成，本次未安装这些集成。

## MCP 工具

远程交付提供 `aica bundle`、`aica verify-bundle` 和 `aica upload`，见 [远程传输契约](REMOTE_TRANSFER.md)。单个 JSON 使用 `aica export --project /path/to/project --portable` 导出，详见 [使用说明](USAGE.md#5-导出并上传平台)。自动上传复用 ZIP 传输契约；HTTP MCP 仍待实现。

| 工具 | 行为 |
| --- | --- |
| `aica_confirm_turn` | 对可信 Hook 已提交的待确认轮次发起客户端 Yes/No；参数或普通聊天文本不能直接授权 |
| `aica_select_turn_artifacts` | 在当前获准轮次，通过原生确认界面选择并复制附件；轮次结束后拒绝 |
| `aica_import_archive` | 将显式选择的自包含 JSON 恢复到新子目录，校验并解出附件，保留原档案身份；不覆盖当前项目或开始录制 |
| `aica_start_session` | 接收用户明确的 Y/N 决定；`request_id` 使启动重试幂等 |
| `aica_check_consent` | 在采集或排队前核对 Session 凭证、状态和事件类型 |
| `aica_submit_event` | 提交协议事件，成功确认表示已持久化，`projected` 表示是否已投影 |
| `aica_capture_artifact` | 复制并哈希显式选择的项目内文件，返回产物记录 |
| `aica_record_step` | 将一次已观察到的完整调用记为节点、Run、依赖与产物；不执行工具 |
| `aica_get_archive` | 读取最新有效投影及未解析缺口 |
| `aica_get_revision` | 按 ID 读取当前或历史定义，历史附件必须通过哈希、身份及语义校验 |
| `aica_get_items` | 分页读取节点、边、分支、Run、产物等元数据，每页最多 200 项 |
| `aica_check_references` | 分页核验本地产物哈希及大小，报告缺失、损坏和受影响的引用；不访问外网 |
| `aica_get_events` | 分页读取已提交事件，每页最多 200 条 |
| `aica_validate_archive` | 校验传入档案，仅读取输入 |
| `aica_export_archive` | 发布一致性快照；`portable=true` 导出包含全部产物的单文件 `exports/aica.json`，后台同步独立运行 |
| `aica_bind_platform` | 用户明确授权后保存 `.aicaconfig`，启用当前档案及后续录制更新的后台上传 |
| `aica_platform_status` | 查看同步状态、当前/已上传序号及远程回执 |
| `aica_pause_upload` | 暂停新上传请求，继续本地录制 |
| `aica_resume_upload` | 用户再次确认后恢复上传，保留原包进行幂等恢复 |
| `aica_sync_platform` | 排队重试或同步最新记录；完成情况通过状态工具查看 |

资源 `aica://schema/0.1` 返回共享 JSON Schema。所有工具返回结构化 `status: success/error`；错误同时设置 MCP 的 `isError`。

## 将导出档案恢复为本地项目

从平台下载或由 `aica export --portable` 生成的完整 JSON，可放在当前 MCP 工作区内，再调用：

```json
{
  "request_id": "restore-comic-001",
  "path": "exports/aica.json",
  "target": "restored-comic",
  "confirm_import": true
}
```

工具名为 `aica_import_archive`。`path` 和 `target` 都相对当前 MCP 工作区；目标必须是尚不存在的新子目录，其父目录须已存在，不允许路径逃逸、符号链接或 `.aica/`、`.git/` 内部目标。已有目录即使为空也不会被接管。`confirm_import` 表示调用方已获得用户对所选文件和目标的授权，不代替真实的授权询问。

手动模式下同一流程也提供 CLI，默认根据源/目标路径产生稳定请求 ID。严格逐轮模式需走 MCP 的独立导入确认，普通 CLI/RecorderClient 参数不能代替原生确认：

```sh
aica import --project . --path exports/aica.json --target restored-comic --confirm-import
```

恢复结果：

```text
restored-comic/
├── aica.json
└── .aica/
    ├── artifacts/<sha256>
    └── imports/local-import.json
```

导入复用共享协议和 portable JSON 校验器：整体上限 128 MiB、元数据上限 16 MiB、单附件上限 64 MiB。缺失附件、哈希不符、非法图或字段会在创建目标前拒绝；不下载原 URI 指向的远程文件。原始 ID、URI、模型、证据、历史和产物关联保留，为每项产物补充可核验的本地 `locations`；根 JSON 移除重复内嵌字节，图片等内容按哈希保存并去重。

`local-import.json` 保存准备/完成状态和请求指纹。写入中断后，用完全相同的源文件、目标和 `request_id` 重试，已写附件须哈希一致；不会覆盖损坏附件或导入期间被修改的目标档案。完成后的重试不覆盖后续创作。初始化收据前若进程被强制终止、留下没有收据的目录，工具会拒绝接管，需另选新目录或先人工核对该目录。

导入不启动目标协调器，不创建新 Session、不执行脚本或模型。将 **`restored-comic` 打开为 Agent 工作目录并重新连接 MCP**，录制器会以该 JSON 为基线创建新的本地数据库与录制流，再通过 `aica_get_archive`、`aica_get_items` 和 `aica_check_references` 查看。旧 Session 只保留为历史，旧令牌不能授权新录制；用户重新授权后可使用 `aica_record_step` 继续创作并再次完整导出。当前 MCP 连接仍绑定原工作区，不会因导入自动切换。

## 一步记录工具调用

仅开启 Session 或登记图片不会产生创作步骤。对于已经完成、确实观察到的调用，Host 可提交 `aica_record_step`：

```json
{
  "request_id": "observed-call-001",
  "session_id": "使用已授权会话的 ID",
  "session_token": "仅通过工具参数传递私有凭证",
  "step": {
    "label": "生成一张图片",
    "client": "Codex",
    "execution": {
      "kind": "model", "tool": "实际观察到的工具名称", "version": null, "model": null,
      "inputs": {}, "params": {"prompt": "实际提交的提示词"}, "outputs": ["image"]
    },
    "started_at": "2026-10-17T01:00:00Z",
    "ended_at": "2026-10-17T01:00:30Z",
    "state": "succeeded",
    "outputs": {"image_path": "output/result.png"},
    "artifacts": [{"path": "output/result.png", "mime_type": "image/png"}]
  }
}
```

示例时间只用于说明格式，真实采集必须传原始观察时间。未知模型保持 null；`model` 对象可单独记录 Agent 模型，`execution.model` 记录该工具所用模型。可提供实际 Harness 快照；缺省 Harness 仅标识 AiCA 观察适配器，并标明原始 Harness 缺失。`depends_on` 只填写有实际依赖证据的节点 ID，不能按时间相近自动连边。

适配器复用现有授权、事件、不可变附件及待确认队列，不新增执行权限。稳定 `request_id` 的私有恢复计划保存在 `.aica/outboxes/steps/`，失败后用相同参数重试；已成功的阶段不会重复建节点，也不重跑外部工具。定义冲突需要重新读取后另建记录请求，不能覆盖其他 Agent 的图。分阶段写入尚未完成时会明确报错，不能将它描述为一次原子完整提交。

该入口通过 MCP 工具调用使用，也可由 `RecorderClient.call()` 调用；协调器内部 RPC 仍使用低层事件。手动模式下它不自动截获客户端原生调用。严格逐轮模式改由 [Codex Hook 桥接](CODEX_TURN_SETUP.md) 采集，普通 Session、手动步骤与旧令牌不能绕过本轮门控。

## 从制作文件恢复步骤

```sh
aica extract --project . --record yes --format steps --path production/aica-steps.json
aica export --project . --portable --output exports/aica-with-steps.json
```

清单结构由 `recording.schema.json` 的 `step_manifest` 定义：`nodes` 和 `edges` 复用协议类型；`files` 显式列出项目内的 `path`、`mime_type`，输出文件另填 `producer_node_id`。每个节点必须标记 `extracted` 或 `inferred`，并通过 `provenance.evidence[].source` 指向清单选中的真实文件。提取器采集证据字节、补齐证据产物 ID、合并新节点，再登记关联输出。

已有附件身份保持不变；绑定步骤的输出使用新的产物实例 ID，可共享同一内容哈希和存储字节。平台按内容去重展示，仍保留实例的产生关系。重复导入相同清单不会重复建立节点；相同节点 ID 对应不同内容时拒绝覆盖。清单中的关系由制作者基于证据明确填写，提取器只校验结构、引用和 DAG，不独立证明因果关系。

制作文件只能证明其中保留下来的内容。此模式不创建原始执行 Run，也不把提取器的 Agent 冒充原作者；缺少原始模型、时间、环境及工具输入时必须保留缺项。节点可展示与能重执行分别判断，图片已登记不表示生图执行器已接入。

录制流程为：先让用户明确选择 Y/N → 启动 Session → 登记可观察的 Harness 和 Agent → 提交定义及执行事件 → 关联产物 → 关闭 Session → 导出。服务信任 Host 转交的用户决定，不能从布尔值本身验证用户是否真的看到了授权界面；Host 必须履行询问流程。

拒绝时只保存最小授权审计，不接受内容事件。`session_token` 是私有恢复凭证，只用于工具调用，不放入事件、Harness 或公开档案。独立 Session 各自授权；子 Agent 需要 `include_children=true`。Pause / Resume 通过显式事件进行，暂停期间不采集新内容。

所有公开事件载荷和完整样例见 [协议说明](../schema/README.md)、`aica://schema/0.1` 与 `aica://schema/recording/0.1`。`definition.replaced` 和 `variables.changed` 携带 `branch_id` 和当前 `expected_revision`；调用后使用返回的新修订。此服务不暴露执行任意脚本的 MCP 工具，脚本执行仍由 D 的 Runtime 对接预置环境。

## 按需读取与附件迁移

以下 JSON 是 MCP 工具参数：

```json
{"collection": "runs", "limit": 50}
```

使用 `aica_get_items` 返回的 `next_offset` 和 `snapshot.snapshot_id` 读取下一页。提交新事件或发布新的历史分段后，旧视图可能返回 `snapshot_conflict`，调用方从第一页重读。分页限制元数据项数，不表示单项字节大小限制；不读取附件内容。`aica_get_revision` 使用 `{"revision_id": "实际修订ID"}`，读取历史附件上限为 16 MiB，不切换当前工作流。

运行 `aica_check_references`，以相同分页规则核验产物；该操作会流式读取所选本地文件。结果区分档案声明的 `declared_availability` 与本次观察的 `state`，并列出每个地址的结果和 `uses`。本地路径必须位于项目内，不跟随逃出项目的符号链接，也不读取协调器凭证、数据库及待确认队列。带 URI scheme 的地址只标记为 unverified，不自动下载；本地路径按项目相对路径字面值解释。

`archive_readable=true` 表示档案投影仍可读取；`execution_checked=false` 表示未作 Runtime 执行检查。`all_references_verified` 仅在一次响应覆盖全部引用时给出布尔值，否则为 null。即使所有附件均通过核验，仍需核对工具、环境、授权及上游依赖。

产物移动后，通过已授权 Session 的 `aica_submit_event` 提交 `artifact.location_added`，载荷为：

```json
{"artifact_id": "实际产物ID", "sha256": "原产物的64位sha256", "uri": "assets/moved-output.png"}
```

保留正常事件信封和稳定事件 ID。此操作只追加地址，保留原 URI、内容哈希和产生关系；它不复制文件、不上传，也不确认新地址内容。后续检查或历史定义读取可使用经过验证的新副本。修改实际内容时创建新的产物实例。

## 真实命令采集

在已激活的环境中，可以对用户明确指定的一条本地命令进行采集：

```sh
aica run --project /absolute/path/to/user-project --record yes -- python3 -c 'print("hello AiCA")'
aica validate /absolute/path/to/user-project/aica.json
aica export --project /absolute/path/to/user-project
```

该命令在用户当前本地环境中执行，不提供沙箱。`local-observed@1` 标识本地观察环境，节点明确标记缺少可移植运行环境绑定；平台不能将它直接当作已获准的云端运行环境。

`--record no` 仍执行用户明确请求的命令，输出直接交给原终端；不保存该命令的参数、输出、节点或产物。该开关是录制决定，不是执行审批。

## 授权后事后提取

```sh
aica extract --project /absolute/path/to/user-project --record yes --format git --limit 50
aica extract --project /absolute/path/to/user-project --record yes --format codex --path /path/to/authorized-export.jsonl
aica extract --project /absolute/path/to/user-project --record yes --format claude_code --path /path/to/authorized-export.jsonl
aica extract --project /absolute/path/to/user-project --record yes --format normalized --path /path/to/authorized-export.jsonl
```

只读取用户显式指定的日志文件，不扫描其他会话或用户目录。当前支持 Codex `response_item` 中的 function call / output / message、Claude Code 消息中的 tool_use / tool_result / text，以及本包 normalized JSONL。格式变更或未知记录会报告警告，原始授权导出作为产物保留；不声称兼容所有客户端历史版本。

提取节点保留文件行号与源文件哈希。Git 提取仅使用真实提交及其父提交关系，不能据此恢复原提示词。提取出的工具调用默认只具备观察语义，补齐环境和工具绑定后才能由 Runtime 判断是否可执行。

## 持久化、恢复与关闭

```text
user-project/
├── aica.json
└── .aica/
    ├── recording.sqlite3
    ├── recorder.lock
    ├── endpoint.json
    ├── coordinator.log
    ├── artifacts/
    ├── outboxes/
    └── imports/
```

1. 先检查授权，再持久化待确认请求；协调器在 SQLite WAL / FULL 事务提交后确认。事件重发按稳定 ID 去重，同 ID 不同内容拒绝。
2. 同一来源按序投影，并检查显式因果引用。缺失前置记录时先保留事件和缺口，其他来源继续录制。
3. 默认每秒合并刷新快照，结束或显式导出主动刷新；事件历史按增量分段保存，正常录制不删除历史。
4. 原子发布前保存候选快照哈希，遇到发布中断可由已提交日志重建。检测到外部编辑时停止覆盖，并报告 `snapshot_conflict`。
5. 协调器退出后，由下次客户端连接重新启动。未结束执行标记为 `unknown`，收到可信结束事件后补全；恢复过程中不重跑工具。
6. MCP 传输待确认队列在下次 `serve` 启动时重发；命令采集器的来源队列可以用 `aica retry` 重发。协议拒绝的请求会明确报错，需要调用方修正，不能假装成功或盲目重跑实际操作。
7. 历史 Session 的授权记录不授予新录制权限。项目已有 `aica.json` 且没有内部数据库时，将其作为导入基线并创建新的录制流；已有数据库时不能通过编辑文件偷偷替换基线。

查看和停止：

```sh
aica status --project /absolute/path/to/user-project
aica stop --project /absolute/path/to/user-project
```

停止前会导出快照。多个 stdio 客户端断开不会分别关闭共享协调器，需要全部结束使用后执行 `aica stop`。内部 `.aica/` 含原始私有记录和恢复凭证，默认不进入 Git；分享只使用经用户确认的快照和它声明的必要资源。

## 验证

在 AiCA 仓库根目录执行：

```sh
python3 -m pytest
python3 -m ruff check packages/schema packages/mcp-adapters
pip check
```

覆盖真实 stdio MCP 往返、多客户端并发、实际命令与产物、拒绝／暂停录制、丢失确认后的重发、进程强制中断恢复、快照发布失败、定义冲突、Git 与日志提取。跨平台分支已提供，实际验证平台以仓库交付记录为准。

测试也覆盖历史定义追溯与篡改检查、地址迁移后的恢复读取、附件缺失时的部分可读性、路径边界，以及分页时的并发视图冲突。实际验证平台为 macOS Apple Silicon；跨平台分支仍需其他系统验证。

2026-10-16 在 Python 3.12.15 / MCP SDK 2.3.0 / SQLite 3.54.0 上通过 71 项测试、Ruff、依赖检查及两个包的 wheel 构建。
