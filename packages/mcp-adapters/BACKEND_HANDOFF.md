# A → D 后端交接：AiCA 协议与 MCP

2026-10-17 补充交接：优先阅读 [.aicaconfig 与平台自动上传契约](PLATFORM_UPLOAD.md)。当前 A 分支已带回步骤采集、本地导入及便携 JSON 能力，并增加平台上传与逐轮确认工具，共 20 个工具。A 的自动上传以本地模拟服务验收，D 对接实际上传接口和认证后再进行真实服务联调。逐轮模式见 [Codex 接入说明](CODEX_TURN_SETUP.md)，本机控制接口不属于平台 API；`tool.finished.state` 现在可为 `unknown`，对应归档已有的未知执行结局，不能当成执行取消。下文早期基线和 PR 信息作为历史交接背景。

严格模式下本地档案导入也要经过原生确认；`aica_import_archive` 由 MCP UI 转交私有控制通道，普通 RecorderClient/CLI 不能只凭 `confirm_import=true` 导入。该本机授权规则不改变 D 的远程上传契约。

交接日期：2026-10-16。统一交接分支：`feature/a-protocol-mcp`，包含原协议、协作规范和远程传输客户端；整合实现基线：`2b5d24e`，初始样本基线：`550f38b`。当前通过 [PR #1](https://github.com/AiCA-Team/AiCA/pull/1) 提交到 `dev`，尚未合并，以 PR 最新提交为准。

**A 已提供创作档案的格式、校验、采集和本地持久化。D 接手后，需要让平台能够读取档案、修改工作流、判断哪些步骤需要重做，并在获准的环境中执行。**

本分支另接入 [agent_coop 规范](../../AGENTS.md)，不搬动业务文件。接手读取 [清单](../../collaboration.json)：A 对应 archive-contract / capture-reconstruction，资源为 archive-package / capture-package；D 对应 graph-runtime / platform-api，候选资源为 runtime-package / gateway-application，并排除 B 的 preview-service。D 负责人仍需接受候选绑定。

T002/T004 保存 A 的候选交付，T005/T006 是后续运行时/API 任务，状态以 tasks 资源为准。用户已授权将 T011/T012 一起归并到 PR #1，统一评审后再合入 dev，不再按旧叠加 PR 顺序接手。下列分支和路径是交接时事实，执行时重新解析清单并核对 HEAD。正常开发从已接受的集成基线新建任务分支，不在 A 的分支写 D 的实现。

## 1. 交付范围与目录边界

| 范围 | 当前状态 | 后续责任 |
| --- | --- | --- |
| `packages/schema/` | JSON Schema、跨引用 / DAG 校验、变量操作语义、合成分支样例 | A 维护唯一真源，D 直接消费 |
| `packages/mcp-adapters/` | 20 个 stdio MCP 工具、本地协调器、逐轮门控、命令采集、授权日志提取、档案导入与后台上传客户端 | A 维护接入与记录；D 对接执行结果和平台上传接口 |
| `packages/runtime/` | 仅目录占位 | D 实现缓存、Dirty Propagation、Golden Path、Fork 和执行调度 |
| `apps/gateway/` | 尚无 API 路由或可启动后端服务 | D 实现平台 REST API |
| `apps/gateway/src/preview/` | 仅目录占位 | B 负责，D 不修改此子目录 |
| 根 `aica.json`、`.aica/artifacts/` | 已授权的真实 Self-Archive 评估样本 | 团队读取评估，更新分享内容前重新检查 |

没有现成的生产认证、任务队列、平台运行环境目录、公开执行服务或端到端 Replay。FastAPI 等依赖已安装，不代表这些模块已经实现。

### 远程交付前提

用户已确认最终需要远程平台，见 [产品共识](../../INFO.md#远程交付目标)。D 接入时需与 A 对齐档案和产物的上传契约：本地先归档实际图片，经授权上传内容，平台校验并建立项目/产物身份到存储位置的映射，再由 API 向 C 提供可访问地址。只接收本地 uri 字符串不能取得用户电脑里的图片；也不能把 Git 仓库中的录制数据库和会话凭证当作生产同步接口。

第一轮远程验收应让机器 A 上传的真实 Graph 和图片能在机器 B 的浏览器中读取，不依赖原机器路径或私有录制状态。现有本地 stdio MCP 可以保留；HTTP MCP、上传方式、认证、产物存储和部署方案需要 A/D 确认，本指南未冻结 URL 或请求体，也不表示已接管 D 的任务。

T012 的 A 侧导出传输包、独立校验和 HTTPS 上传客户端现已归并到 `feature/a-protocol-mcp`。D 接手请先评审 [远程传输约定](REMOTE_TRANSFER.md)，复用 [共享传输 Schema](../schema/src/aica_schema/transfer.schema.json)，再实现接收和图片访问。HTTP 请求/回执是候选契约，未宣称 D 已接受或真实服务已联调；本机内部 RPC 继续不作为远程 API。

## 2. 先看哪些文件

1. [项目共识](../../INFO.md)：产品、Harness、并发、恢复和分工。
2. [协议说明](../schema/README.md)：字段语义与 A/B/C。
3. [档案 Schema](../schema/src/aica_schema/aica.schema.json)：共享字段唯一真源。
4. [事件及 MCP 输入契约](../schema/src/aica_schema/recording.schema.json)：载荷、工具名称与参数。
5. [MCP 使用说明](README.md)：安装、客户端接入、CLI、查询与恢复。
6. [真实样本字段字典](../../.aica/README.md)：每个内部文件、样本产物和嵌套字段。

Python 包名是 `aica-schema` / `aica-mcp`，导入名是 `aica_schema` / `aica_mcp`。协议版本为 `0.1`，包版本为 `0.1.0`。这是尚未冻结的开发契约，不能把包版本和工作流修订混为一谈。

## 3. 获取分支并完成首次检查

在已有工作区先保存自己的改动，再执行：

```sh
git fetch origin
git switch --track origin/feature/a-protocol-mcp
conda env create --file environment.yml
conda activate aica
pip install --no-build-isolation -e packages/schema -e packages/mcp-adapters
pip check
python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
aica validate aica.json
aica validate packages/schema/tests/fixtures/branching.aica.json
```

本地已存在该 Git 分支时使用 `git switch feature/a-protocol-mcp`；已存在 `aica` 环境时先激活，再按共享环境文件核对，不重复创建。CPython 要求 3.12.15，SQLite 至少 3.51.3。Conda 环境使用清华源，具体操作见 [SETUP](../../docs/SETUP.md)。

首次阅读样本只需执行校验，无需启动录制器。CLI `aica serve --project <目录>` 使用 stdio 传输，终端等待输入是正常现象；它不是 Uvicorn，也不会提供 Swagger 页面。

## 4. 后端可以直接复用的接口

### 4.1 纯协议接口：网关接入优先从这里开始

位置：[aica_schema.protocol](../schema/src/aica_schema/protocol.py)。

| 函数 | 用途 | 关键行为 |
| --- | --- | --- |
| `loads(text)` / `dumps(value, pretty=False)` | 严格解析 / 编码 JSON | 拒绝重复键、非有限数和不兼容的数值；失败抛 `ProtocolError` |
| `validate_archive(archive)` | 结构和跨对象语义校验 | 返回 `status`、`valid`、`errors`；前置 JSON 值检查仍可能抛异常 |
| `require_archive(archive)` | 必须有效的入口校验 | 不合法时抛 `ProtocolError` |
| `archive_schema()` | 获取完整共享 Schema | 供网关、前端及类型生成使用 |
| `empty_archive(title, project_id=None, stream_id=None)` | 构造有效空档案 | 生成项目、定义与快照身份；不代表已有录制授权 |
| `resolve_variable_operations(variables, operations)` | 解析有序变量操作 | 返回新的变量字典，不修改原输入；不完成 Fork、修订创建或脏节点计算 |
| `require_definition_snapshot(definition, artifacts, revision_id)` | 校验旧定义的身份及语义 | 不自行下载附件，也不校验传入字节的哈希 |

位置：[aica_schema.recording](../schema/src/aica_schema/recording.py)。`tool_contracts()` 提供带共享类型的工具输入 Schema；`require_arguments(name, arguments)` 和 `require_event(event)` 分别校验工具参数、公开事件及载荷。内部事件不能按公开事件提交。

上传入口应先限制大小，再用严格解析器读取原始 JSON，不能先用宽松解析丢掉重复键后才校验。`validate_archive` 通过也不代表附件存在、旧定义附件已读取、Golden Path 可执行或模型结果能复现。它对 Golden Path 只检查节点引用，不实现路径选择和执行有效性判断。

以下可直接在仓库根目录运行，验证变量语义：

```sh
python3 - <<'PY'
from pathlib import Path
from aica_schema.protocol import dumps, loads, require_archive, resolve_variable_operations

path = Path("packages/schema/tests/fixtures/branching.aica.json")
archive = loads(path.read_text(encoding="utf-8"))
require_archive(archive)
operations = [
    {"variable_id": "character", "mode": "override", "value": "astronaut cat"},
    {"variable_id": "character", "mode": "append", "value": "floating burger"},
    {"variable_id": "character", "mode": "keep"},
]
variables = resolve_variable_operations(archive["variables"], operations)
assert variables["character"]["value"] == "astronaut cat, floating burger"
assert archive["variables"]["character"]["value"] == "cyberpunk girl"
print(dumps({"status": "success", "character": variables["character"]["value"]}))
PY
```

### 4.2 本地录制接入

Agent 通过 stdio MCP 接入；同机 Python 适配器可以使用 [RecorderClient](src/aica_mcp/client.py) 的 `call(name, arguments)`。`RecordingSource` 提供来源序号、持久化队列和事件提交。两者目前都是同步调用，异步 API 路由中应避免直接阻塞事件循环。

`RecorderClient(project)` 会连接或启动该目录的本机协调器，并可能创建内部文件、导入已有档案或更新快照。不要对任意上传路径或不可信目录直接调用。后端先通过自己管理的项目身份映射到隔离目录。

同一项目只能由一个协调器持锁写入。API 的多个 worker 不应各自创建 [Recorder](src/aica_mcp/store.py) 覆盖同一目录；使用已有本地客户端路径。直接构造 Recorder 主要用于单进程测试、实现内部维护和离线验收。

协调器的 `127.0.0.1` HTTP 接口是本机内部传输，包含本机凭证和 Host / Origin 限制。它没有平台用户权限与公网认证，不能直接映射成公开 REST API，也不承诺内部数据库和 RPC 的长期兼容性。

### 4.3 现有 MCP 工具

| 工具 | D 应如何使用 |
| --- | --- |
| `aica_start_session` | 接收真实用户的录制决定；启动请求使用稳定 `request_id` |
| `aica_check_consent` | 内容进入采集或待确认队列之前检查授权 |
| `aica_submit_event` | 记录定义变化、执行观察和说明；不会执行工具 |
| `aica_capture_artifact` | 复制所选本地文件并生成哈希，单文件上限 64 MiB；不是上传接口 |
| `aica_record_step` | 观察适配器组合现有事件，记录一次完整调用及节点/Run/产物关系；不执行工具，凭稳定请求 ID 恢复 |
| `aica_get_archive` | 读取当前投影，包含提交边界和缺口 |
| `aica_get_revision` | 按 ID 读取旧定义，核验本地附件哈希、修订身份和语义；上限 16 MiB |
| `aica_get_items` | 分页读取节点、边、分支、Run、产物、Harness、Agent、Session 或历史 |
| `aica_get_events` | 按 `after_seq` 分页读取当前本地录制流已提交事件 |
| `aica_check_references` | 核验本地引用并返回用途；不下载外部链接、不完成执行前检查 |
| `aica_validate_archive` | 校验传入的档案对象 |
| `aica_export_archive` | 发布本地一致性快照和历史引用，不上传或公开 |

Schema 资源为 `aica://schema/0.1` 和 `aica://schema/recording/0.1`。完整工具参数从共享契约读取，不在网关复制另一套定义。

`aica_record_step` 位于 MCP / `RecorderClient` 层，不是协调器内部新增执行 RPC。详细流程与证据清单导入见 [采集指南](README.md)。导入后的 `graph.nodes[].provenance` 区分实时、提取与推断；`snapshot.complete` 只表示已提交事件无缺口。制作文件恢复的步骤可没有 Run，不能补造时间或将提取器身份当成原始作者。关联输出通过 `producer_node_id` / `producer_run_id` 查找，同一内容哈希可被多个产物实例引用。

`aica_get_items` / `aica_check_references` 每页最多 200 项，后续页必须携带该查询返回的 `snapshot.snapshot_id`；视图变化返回冲突后从头读取。该视图 ID 不应与另一份导出快照的 ID 交叉使用。限制的是项数，不是每项字节大小。`aica_get_events` 使用提交序号游标，是另一种分页方式。

## 5. Runtime 的执行结果怎样接回来

推荐先走完一次“获准执行 → 产生结果 → 正确归档”的联调，再实现缓存和复杂 Fork。

1. **确认录制范围。** Host 取得本次 Session 的 Y/N，调用 `aica_start_session`。`session_token` 仅保存在私有凭证存储，不进入事件、公开档案或前端日志。子 Agent 只有在 `include_children=true` 时才能纳入。
2. **登记上下文。** 依次记录产物附件（如需要）、`harness.registered`、`agent.registered`。Harness 中只能保存可观察且已授权的信息；变化时登记新快照，不改旧快照。
3. **确定定义。** 读取当前修订，提交 `definition.replaced` 或 `variables.changed`，携带 `branch_id` 和 `expected_revision`，使用返回的新修订。当前实现为整个工作流的修订比较，并非每个节点各有独立写锁。
4. **记录执行开始。** 生成新的 Run ID，提交 `tool.started`。payload 为共享 `run` 类型，初始 `state=running`、`outputs={}`、`artifact_ids=[]`；关联明确的节点、修订、Session、Agent、Harness。`trace_id` 可将同一次工作流的步骤关联起来。
5. **D 执行步骤。** 在另外确认执行授权、工具和环境后调用实际工具。录制授权与执行授权分别处理。MCP 的事件提交不会替 D 执行任何命令或模型。
6. **先登记结果产物。** 输出落盘后通过 `aica_capture_artifact` 或合规的 `artifact.recorded` 关联；`producer_run_id` / `producer_node_id` 必须与本次执行对应。相同字节可以共用存储，但保留不同执行的产物实例和来源。
7. **再记录执行结束。** `tool.finished` 使用同一 `run_id`，记录结束状态、时间、实际输出和产物 ID；由原执行者结束该尝试。失败也保留其尝试与证据。
8. **导出或继续录制。** Session 可以包含多个执行，不需每结束一步就关闭。会话结束记录 `session.closed`，需要交付时调用导出并检查边界、缺口和引用。

如果只接收静态档案，不必为了读取它创建一个已授权录制 Session。原档案中的 `consent.record=true` 也不能当作当前用户授权。

### 确认、重试与结果待核实

成功提交事件的回执包括 `event_id`、`stream_id`、`commit_seq`、`duplicate`、`projected`、`revision_id`、`snapshot_error`。

1. 确认成功表示事件事务已提交；`projected=false` 表示依赖或来源顺序未补齐，不能把它当作步骤已生效或去重失败。
2. 网络断连或确认丢失时重发同一事件，保留 `event_id`、`source_id`、`source_seq` 和原内容。同 ID 不同内容会冲突。录制去重不保证外部工具只执行一次。
3. 执行重试创建新 Run。不能因为重连或等待超时就自动重跑支付、发布、文件写入或其他有副作用的调用；先核实原调用结果。
4. 协调器恢复时，已开始但无结束记录的 Run 标为 unknown；可信的迟到结束事件可以补全原 Run。
5. `snapshot_error` 表示快照发布问题，需与事件事务失败分别处理。当前实现允许导出携带 `snapshot.gaps` 的有效部分档案；导出成功不代表所有事件均已投影，Runtime 必须单独检查。

## 6. D 开发时需要保持的语义

| 事项 | 约定及当前边界 |
| --- | --- |
| A/B/C | A=Keep、B=Override、C=Append，按有序操作解析；Keep 不撤销之前的修改 |
| Append | 仅字符串和数组；字符串使用变量分隔符，数组保留顺序和重复项；默认值单独保留 |
| Fork | 现有 `branch_id` 声明编辑上下文，不自动创建独立分支头、隔离变量或合并分支。平台 Fork 的档案身份、来源关系和持久化方式仍需 D 与 A 对齐 |
| Dirty Propagation | 从变量输入绑定和明确依赖出发。合成 fixture 中修改 character 会影响角色节点及其下游 panel，不应据时间先后推断其他节点也脏 |
| 数据 / 控制依赖 | 都属于 DAG，但不是同一种语义。控制依赖的失效和调度策略由 D 明确；没有 data edge 的 node_output 绑定会被协议拒绝 |
| Golden Path | 推荐复现路径；算法、成功证据、分支过滤、算法版本和结果依据由 D 实现并与 A 对齐，不能把 Schema 校验通过当成路径已验证 |
| 缓存 | `runs.cache` 只保存 Runtime 报告的决定。最终缓存键、输入规范化、工具 / 模型 / 环境 / Harness 的纳入方式尚未实现 |
| 内容哈希 | `artifacts.sha256` 是原始字节哈希；内部 `digest()` 使用 Python 稳定 JSON 编码，不是已约定的跨语言缓存键或 RFC 8785 实现 |
| 版本 | 工作流修订、Harness 快照、执行尝试、导出快照、内容哈希分别管理，不能互相替代 |
| 执行环境 | 脚本 / 命令声明平台预置的 `runtime_ref` 和参数；环境清单与解析机制待 D 实现。开发用 Conda 不等于工作流执行环境 |
| 本地命令采集 | `aica run` 直接在本机执行，不是沙箱；`local-observed@1` 不可直接当作平台认可的生产环境 |
| 外部产物 | URI 是地址，哈希是内容身份。新增 `locations` 不改变原 URI 和来源，不代表新地址已经核验 |

文件版本继续由 Git 管理。`snapshot.commit_seq` 是录制流序号，不是 Git commit。多台机器不能直接共享同一个 SQLite WAL 目录并同时写入；云端汇聚和多用户存储需要单独实现。

## 7. 后端需要实现及对齐的部分

下表是建议的实施顺序，不是已经存在的 REST 接口或团队已冻结的端点命名。

| 顺序 | D 的交付 | 需与 A / 其他角色对齐 |
| --- | --- | --- |
| 1 | 档案导入、校验、读取及历史展示 API | 引用文件接收方式、缺项报告、项目 / 用户归属；禁止信任用户提供的本机路径 |
| 2 | Fork 与变量修改 | 新档案 / 分支身份、来源修订、并发冲突响应、生成新定义的持久化路径 |
| 3 | 输入解析与脏节点计算 | 变量 / 模板 / node_output / artifact 的解析，data 与 control 的调度语义 |
| 4 | 预置环境与一次真实步骤执行 | 工具绑定、授权、执行隔离、Run 生命周期及输出回写 |
| 5 | 缓存复用和局部 Replay | 缓存键规范、引用产物访问核验、旧 Harness / 环境变化判定 |
| 6 | Golden Path 与预览编排 | 成功历史与算法依据，和 B 对接 draft / final，和 C 对齐前端状态 |

公开平台上线前还需确定认证、权限、生产存储、外部 URI 访问策略及上传脚本的执行隔离。当前本机 Session 令牌不是平台用户登录机制，现有 `execution.permissions` 也只是声明，尚无平台强制执行。

协议字段或枚举需要增加时，先在 `packages/schema/` 协调更新，并补充可被其他模块消费的样例；不要只在网关创建一套同名但含义不同的模型。目前没有生成的全组 TypeScript 类型或 API OpenAPI 契约可直接接入。

## 8. 错误如何转成平台响应

工具成功 / 失败均返回 `status`；MCP 失败同时设置 `isError`。常见错误对象为 `status`、`code`、`message`、`details`。`aica_validate_archive` 的正常无效结果使用 `valid=false` 和 `errors`，不要要求它一定有 `code`。

| 错误码 | 意义 | 调用方处理 |
| --- | --- | --- |
| `invalid_contract` | JSON、字段或语义不合法 | 显示输入错误，修正后再提交 |
| `not_authorized` / `session_state` | 凭证或授权缺失，或会话状态不允许 | 取得正确授权 / 状态；不能静默开启录制 |
| `revision_conflict` | 预期定义已过时 | 重新读取，向用户展示冲突后重提或创建明确分支 |
| `id_conflict` / `source_conflict` / `identity_conflict` | ID 或来源序号被不同内容占用 | 保留证据并修复来源状态，不随意覆盖已提交事实 |
| `pending_definition` | 定义修改的依赖尚未解析 | 先解决前置记录，不能把未生效修改当成功 |
| `snapshot_conflict` | 文件被外部改动，或分页视图已变化 | 根据调用场景停止覆盖并处理导入，或重新分页 |
| `revision_not_found` / `artifact_unavailable` | 修订不存在，或无可读取的核验附件 | 显示缺失范围，不以当前定义或错误副本替代 |
| `artifact_conflict` / `execution_conflict` | 内容身份或执行结束状态冲突 | 核查原记录，不覆盖或重复结束 |
| `project_busy` / `coordinator_unavailable` | 项目被占用或协调器不可用 | 检查已有进程、锁和诊断；不靠删除锁文件抢占 |
| `storage_error` | 文件、数据库等底层问题的包装 | 明确失败；不能表示已经保存 |

Python 客户端的参数校验和连接错误可能直接抛异常，不能只检查返回字典。内部协调器通常以 HTTP 200 承载结构化业务结果，身份 / 来源拒绝可直接返回 HTTP 错误；平台 HTTP 状态映射由 D 定义，不应原样照搬内部传输。

## 9. 联调样本、检查与验收

| 材料 | 适合验证 | 不能据此声称 |
| --- | --- | --- |
| 根 `aica.json` 和 10 个引用文件 | 真实 MCP 输入输出、Harness、旧定义、事件分段和可移植引用 | 全部开发过程自动采集、复杂图已执行或云端 Replay 可用 |
| `branching.aica.json` | 分支、废弃尝试、变量、端口和 DAG 的结构与语义 | 存在真实图片产物、供应商已确定、对应步骤已经运行 |
| `test_recorder.py` | 授权、事件顺序、修订冲突、附件、恢复和快照故障 | 多机数据库共享或生产隔离已完成 |
| `test_integration.py` | 真实 stdio、多个客户端、实际命令、提取和进程中断 | 所有客户端历史格式或 Windows / Linux 运行已验证 |

本地完整检查：

```sh
python3 -m pytest -q
python3 -m ruff check packages/schema packages/mcp-adapters
pip check
```

基线为 macOS Apple Silicon、Python 3.12.15、MCP SDK 2.3.0、SQLite 3.54.0 上 71 项测试通过；Ruff、依赖检查和两个包的 wheel 构建通过。源码有跨平台分支，Windows / Linux 需团队实机验证。目前没有 `.github/` CI，也没有 pnpm workspace 或 `pnpm verify`，不要假定推送后有这些自动检查。

第一轮后端验收建议：

1. 两份档案都能校验；非法 JSON、循环依赖或错误端口可明确拒绝。
2. 按根档案的 `artifact.uri` 从项目根定位十个文件，核对哈希及大小；旧修订能读取。不要修改已经提交的哈希文件内容。
3. 仅复制分享文件到独立临时目录，不复制原数据库和凭证；能导入并建立新录制流，不能继承旧会话权限。
4. 对合成 fixture 的 character 做 Override 后 Append，得到 `astronaut cat, floating burger`；原档案和原默认值保留。
5. 平台能表达“可读取但附件缺失 / 环境未就绪”，不把 JSON 有效或附件核验成功当作执行许可。
6. D 的第一个实际执行能按开始 → 产物 → 结束回写，失败和未知结果如实保留，不自动重跑副作用。
7. 修订竞争、分页竞争、断连重发能返回明确结果，不丢事件、不混入不同视图。

如需测试真实本机命令，使用另外创建的项目目录，再按 [MCP README](README.md) 运行 `aica run`；不要用根目录的已发布样本做可写沙箱。其他成员的用户目录、Conda 路径、会话令牌和端点不随仓库共享，需在自己的机器上配置。

## 10. 合并与后续协作

1. 在 PR #1 评审共享 Schema、真实样本和上述接入边界；确认后合入 `dev`。
2. D 从合入后的最新 `dev` 建立自己的功能分支，开发 `packages/runtime/` 和网关 API；不直接推送 `main`，不修改 B 的 preview 子目录。
3. 优先确认 Fork 身份、预置环境清单、缓存键和 Runtime 回写契约；本交接文档没有替团队冻结这些待定设计。
4. 共享协议问题回到 A 的目录协调；平台实现与数据模型避免依赖本地 SQLite 表结构。需求和联调状态可在团队工作台 `<private-team-tracker>` 更新，本次交接没有自动修改工作台状态。
