# AiCA v0.1 协议

本包是协议唯一真源。JSON Schema 为 Draft 2020-12，协议版本固定为 `0.1`；Python 语义校验补充跨对象引用、DAG 和 A/B/C 规则。

单文件分享通过保留扩展 `extensions["aica.embedded_artifacts"]` 携带按 SHA256 去重的 Base64 产物，仍是同一份 AiCA JSON。共享验证器核验编码、哈希和大小；平台完整导入另要求所有产物字节齐备。详见 [直接上传 JSON](../../docs/JSON-IMPORT.md)。

1. [档案及共享类型](src/aica_schema/aica.schema.json)
2. [录制事件载荷与 MCP 输入契约](src/aica_schema/recording.schema.json)
3. [带主干、废弃分支、变量及端口的合成样例](tests/fixtures/branching.aica.json)

安装与校验：

```sh
conda activate aica
pip install --no-build-isolation -e packages/schema -e packages/mcp-adapters
aica validate packages/schema/tests/fixtures/branching.aica.json
```

## 档案结构

| 字段 | 含义 |
| --- | --- |
| `aica_version` | 协议版本；未知版本拒绝处理 |
| `metadata` | 项目身份、作者、来源、时间及外部链接 |
| `variables` | 类型、默认值、当前值、允许操作、分隔符及节点输入绑定 |
| `interactive_questions` | 问题和变量的引用，以及允许的三态操作 |
| `graph` | 定义修订、节点、带端口的数据依赖、控制依赖、分支和 Golden Path |
| `variable_operations` | 当前档案已采用的有序变量操作 |
| `artifacts` | 产物实例 ID、原始 URI、可选替代地址、MIME、字节哈希、声明可用性及产生它的节点和 Run |
| `harnesses` | 不可变 Harness 配置快照及可观察性说明 |
| `agents` | Session、父 Agent、初始 Harness 及模型配置 |
| `sessions` | 授权决定、子 Agent 范围及录制状态；不含恢复凭证 |
| `runs` | 一次执行尝试；`trace_id` 关联同一次工作流执行，`id` 区分重试 |
| `history` | 旧定义及事件分段的产物引用 |
| `snapshot` | 录制流、快照 ID、已提交边界、完整性与缺口 |
| `extensions` | 扩展元数据，建议使用命名空间键 |

`execution` 声明工具、模型、版本、输入绑定和输出端口。脚本／命令声明 `runtime_ref` 和 `argv`，脚本另外引用脚本产物。`runtime_ref` 由平台解析到预置环境；声明本身不授予执行权限。没有执行描述的观察节点仍可归档和展示。

输入绑定类型为 `literal`、`variable`、`template`、`node_output` 或 `artifact`。`template` 使用 `{{slot}}` 占位符和显式的 slot → 变量 ID 映射，支持多个字符串变量组成同一提示词；不支持表达式或模板代码执行。`node_output` 必须有完全匹配的带端口 data edge；其余控制依赖使用 control edge。图与分支父子关系均不得形成环。

节点 `provenance` 区分 `recorded`、`extracted`、`inferred`，保留证据与缺项。节点定义与每次实际执行分开；`runs.cache` 只承载 Runtime 报告的缓存决定，不在本包实现缓存算法。

## A/B/C

1. Keep 保持操作序列开始时继承的当前值，后续 Keep 不撤销之前的 Override / Append；原始默认值单独保留。
2. Override 以同类型值替换当前值。
3. 字符串 Append 按变量的 `separator` 拼接，缺省为换行；任一侧为空字符串时不额外插入分隔符。
4. 列表 Append 按顺序追加，保留重复元素；`items_schema` 可约束元素，须为不含引用的自包含 Schema。
5. 数字、整数、布尔值、对象不允许 Append。布尔值不被当作数字处理。
6. 操作按列表顺序解析，支持 Override 后 Append。此处提供纯协议语义参考函数 `resolve_variable_operations`；平台 Fork 与执行调度由 Runtime 实现。

## 录制契约

公开事件至少包含 `event_id`、`source_id`、`source_seq`、`session_id`、`agent_id`、`type`、`timestamp`、`causal_parents`、`payload`。Actor 暂未知时 `agent_id` 可为 null，不能假造身份。

| 事件 | 载荷 |
| --- | --- |
| `harness.registered` | 不可变 Harness 快照 |
| `agent.registered` | Agent 身份及上下文引用 |
| `definition.replaced` | 完整 Graph、变量与问题定义 |
| `variables.changed` | 有序 `operations` |
| `tool.started` | 状态为 running、无最终输出的 Run |
| `tool.finished` | Run ID、结束状态、时间、输出及产物 ID |
| `artifact.recorded` | 不可变产物记录 |
| `artifact.location_added` | 产物 ID、原内容 `sha256`、新增 `uri`；不更改原始记录或证明新地址可用 |
| `message.recorded` | 角色、文本及可选节点引用 |
| `session.paused/resumed/closed` | 空对象 |
| `note.recorded` | 说明及证据来源 |

`session.created` 和 `recorder.recovered` 为内部审计事件，客户端不得提交。所有来源都从 `source_seq=1` 开始；重连保留来源身份和序号，状态丢失时创建新来源。提交序号只在一个录制流内有意义。

定义操作必须携带 `branch_id` 和 `expected_revision`。分支标识声明编辑上下文，v0.1 使用整个工作流修订进行并发控制，不承诺不同分支的编辑自动合并。录制器分配新修订，并将旧定义保存为可引用产物；迟到的执行结果仍能引用其原定义。修改冲突不自动覆盖或重试修改内容。未知依赖保留为未投影事件，并在 `snapshot.gaps` 中说明。

JSON 使用 UTF-8，拒绝重复键、NaN、Infinity 和超过 64 层的嵌套；整数限制在 ±(2^53−1)，更大整数使用字符串。产物哈希为字节内容的 SHA-256。内部事件去重使用本实现稳定 JSON 编码的摘要，不宣称实现了 RFC 8785，也不替代 D 的跨语言缓存键契约。

## 修订、内容身份与派生视图

1. 工作流修订、执行尝试、产物实例与内容哈希分别标识。相同内容可共用字节存储，不合并不同 Run 的产物实例和来源。
2. Run 引用具体 `revision_id` 和不可变 `harness_id`。更改定义或 Harness 不改写旧执行；历史定义使用共享 `definition_snapshot` 类型，读取时校验内容哈希、修订身份和定义语义。历史定义缺失时保留引用与错误，不以当前定义代替。
3. `history` 的每个 revision ID 只能映射到一份带内容哈希的定义，当前修订不得同时作为历史修订；当前父修订必须有历史引用。JSON 结构校验不读取历史附件，也不据此证明历史定义中的节点存在；本地录制器接收引用旧修订的 `tool.started` 时进一步读取校验。
4. `artifact.uri` 保留录制时的地址；`locations` 保存通过独立事件追加的替代地址。新增地址必须声明与原产物相同的哈希，不改变实例 ID、内容身份、大小和产生关系。地址内容仍需独立核验；没有已知哈希的产物不能声明替代地址。
5. `availability` 表示档案中的声明，不是实时探测结果。附件检查返回 `verified`、`missing`、`mismatch`、`unverified` 或 `blocked`；只要存在一份哈希和已声明大小均匹配的本地副本，该产物即可报告 verified。每个地址仍保留自己的检查结果。
6. 事件事实、工作流修订历史和步骤依赖图分别表达。`aica.json` 与查询结果是有提交边界的派生视图；分页读取需携带首个响应的视图 `snapshot_id`，视图变化时重新分页，不能拼接不同边界的数据。未来 Golden Path 的算法版本与计算依据由 Runtime 定义。

## 可读取与可执行

声明过的附件丢失或损坏，不使有效 JSON 自动变成非法协议。读取者应展示现存 Graph 和明确的缺项；读取历史定义不能用错误哈希的副本或当前定义补位。附件检查通过 `uses` 指明当前节点输入、脚本、Harness、执行产物及历史引用，便于 Runtime 判断影响范围。

`all_references_verified=true` 只说明完整检查页中的引用均找到匹配的本地副本；检查尚未遍历全部页面时为 null。检查不下载外部地址，不确认工具、环境、权限、上游重执行结果或模型的可复现性；`execution_checked=false`，最终执行判定由 Runtime 完成。未声明为 artifact 引用的任意 JSON 值不进行资源探测。

这些是尚未冻结的 v0.1 开发契约增补。无新增字段的原有标准档案继续可读；较旧消费者可能拒绝 `locations` 或新事件，团队应同步使用本目录 Schema，不能假定旧读取器天然向前兼容。

## 对接边界

Python 可调用 `validate_archive`，成功／失败结果使用 `status: success/error`。Web、Runtime 和渲染器应消费这两份 Schema，不能复制另一套共享类型定义。

本包校验结构和已声明引用关系，不会下载 URI、执行命令、调用模型或证明上传者提供的历史真实。产物是否仍可访问、环境是否可用及调用是否被授权，需要在执行入口另行检查。
