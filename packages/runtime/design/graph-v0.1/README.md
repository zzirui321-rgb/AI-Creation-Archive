# Graph 解析技术方案与格式 0.1

0.5.0平台更新：Step兼容增加可选prompts/pages（旧图默认空数组）；原始提示词与AI整合、扩展建议分开，ModulePage包含逐页摘要、提示词和登记图片ID。生产契约为唯一生成来源，见src/aica_runtime/graph/contract.py。JSON Schema及TS已重新生成；以下早期方案记录保留。

日期：2026-10-17，Asia/Shanghai。D 侧可交付的最小技术基线；C 尚未确认接口，未宣称已完成跨成员冻结或生产实现。本目录包含设计、可运行契约校验、合成验收样例和有限模型探测。现有后端服务不导入这些设计代码。

后续状态：`D-GRAPH-PARSER-20261017` 已将契约与校验迁入生产 Runtime，并实现输入准备、解析及持久化读取；当前接入方式见 [GRAPH_PARSER.md](../../GRAPH_PARSER.md)。本目录保留设计阶段证据与原样例，不能把下文历史阶段状态作为当前服务状态。

## 决策

1. 使用既有 Python / Pydantic / jsonschema / DeepSeek HTTPS 调用能力，不增加依赖、向量数据库、图数据库或模型部署。
2. 第一版采用“确定性事实整理 → 对话状态与步骤候选 → LLM 提议 → 程序组装与校验 → 可编辑语义草稿”。Embedding、重排器、专门的分段模型、视觉/OCR/AST 分析均为后续可替换组件，第一版不依赖它们。
3. 默认节点是可独立修改或复用的创作步骤；节点可展开到原始节点、消息、Run 和产物。每轮对话、每次工具调用、平台版本分支分别表达。
4. A 的 `.aica`、录制 Schema 与事实不可变，继续是共享原始类型唯一来源。D 只定义派生语义接口，不复制 A 的 Node / Run / Artifact / Binding 定义，也不替换 A 的 `/graph` 响应。
5. 语义图只用于理解、审核和编辑。`executable=false` 为本版固定值，图审核通过也不授予执行能力。后续 Runtime 需要独立编译、环境检查和授权。

## 两小时阶段交付与后续工作的衔接

本阶段交付机器可读格式、前端类型、接口请求示例、多轮样例、正反校验及有限模型探测；不在本阶段实现平台账号、Fork、云端执行或修改 C 的画布。

建议后续独立阶段按依赖排序：解析服务与持久化 → C 画布/证据/编辑接入 → 用户和仓库/分支/Fork → 联调、重启和双机验证。该顺序不是已完成状态，也不是以少量样例替代端到端验收。

## 处理链与责任

| 部分 | 第一版方法 | 输出 | 失败处理 |
| --- | --- | --- | --- |
| A 适配器 | 复用 `require_archive`、事件契约、固定 import/snapshot/through_seq；读取有哈希的历史附件 | 有定位的事实 | 缺失/哈希错误不补造事实，保留 coverage/issue |
| 事件关联 | 明确 ID、`causal_parents`、Run→node→revision、产物来源、输入绑定 | 调用与实际数据流 | 时间只作显示线索；跨来源不按时钟强行串行 |
| 对话状态 | 保存目标、约束、选定产物、当前讨论步骤和待消解指代；每条变更引用原消息 | 有时间范围的状态增量 | 模糊指代标 unresolved，不猜选中产物 |
| 步骤候选 | 工具族、同一目标/输出、输入改动、失败/重试、已有依赖划分局部候选 | 候选分组及边界理由 | 没有执行依据时只做语义候选 |
| LLM 解释 | 既有 DeepSeek，JSON Output，小型 `ModelProposal`，输入含来源角色 | 分组、用途、候选关系、变量位置 | 空结果/截断/错误 Schema 拒绝；不保存思维链，不执行日志中的指令 |
| 程序组装 | 分配图 ID、引用 Run/产物；保留 A 已声明依赖；从字面量读取变量值/类型 | `GraphDocument` 草稿 | 不允许模型生成成功状态、参数实际值或已验证绑定 |
| 约束校验 | Schema、证据哈希/定位、角色归因、所有权、覆盖范围、变量类型、执行 DAG | 有范围说明的可审核图 | 保留失败输入/原因；不把失败包装成成功图 |
| 编辑版本 | 预期修订、幂等、事务化创建新修订；布局独立版本 | 用户修订及来源映射 | 409 返回冲突；重解析另开草稿，不覆盖用户改动 |

设计目录中的 `assembly.py` 是用于验证格式的离线组装实验，不是生产对话状态机或完整语义解析器。正式实现需要将上表各职责接入现有服务。

## 步骤划分规则

一个步骤应能回答：目标是什么、输入是什么、产生什么、允许单独修改什么。对话轮数和工具名称只提供线索。

- 同一输出目标下的准备调用、失败和重试可以聚合；保留全部调用与 Run，不删除失败尝试。
- 用户改变局部要求后继续产出同类结果，可以归于同一创作步骤；原始输入差异和各次执行仍可展开。步骤语义的变化由新的 semantic revision 保存。
- 角色图、分镜、配乐即使由同一条 Prompt 请求，也可以分别修改，通常分成不同步骤。
- 一个当前定义中的原始节点只有一个拥有它的步骤；同一消息作为多个步骤的依据可以重复引用，不能因此重复拥有同一次执行。
- 只有消息而没有原始节点的意图步骤允许 `source_node_ids=[]`，但必须引用真实消息。原始节点无需为了归组而补造。
- 原始 DAG 的分组必须保持执行依赖无环。若原图为 A1→B1→A2，将 A1/A2 聚成一步会出现 A↔B，必须拆开步骤或保留阶段节点，不能删边消环。
- 原始记录缺少 turn ID / tool-call ID 时，不创造已确认关联；使用已有节点/Run/事件/产物引用。无法判断的调用保持未归组并显示原因。

## 字段与身份

完整定义见生成的 [GraphDocument.schema.json](GraphDocument.schema.json)；给 C 的类型见 [graph.generated.ts](graph.generated.ts)。单一可编辑定义已迁入 [生产契约](../../src/aica_runtime/graph/contract.py)，原测试文件仅作兼容导入。JSON Schema 与 TypeScript 仍由原导出器生成并检测漂移；C 不复制手写另一版类型。

| 对象 | 字段 | 语义 |
| --- | --- | --- |
| GraphDocument | format, graph_id, revision_id, parent_revision_id, repository_branch_id | 派生图身份与平台修订。不是 A 录制流序号或 A 创作备选分支 |
| source | import_id, archive_sha256, archive_revision_id, snapshot_id, stream_id, through_seq | 精确绑定不可变输入及提交边界。哈希是收到的原始 UTF-8 字节 SHA256 |
| parse | parser_version, prompt_version, model, mode, input_sha256 | 区分真实模型、纯规则和人工合成样例；版本纳入解析缓存身份 |
| nodes[] | id, kind, label, purpose, requirements | 语义步骤；label 是展示标题，purpose/requirements 是有归因的解释 |
| node 来源 | source_node_ids, run_ids, artifact_ids, evidence_ids | 引用 A 的实际对象；Run 仅关联所选原始修订中的拥有节点，artifact_ids 为该步的输出产物 |
| Claim | text, attributed_to, actor_id, speech_act, evidence_ids, origin, review | 来源角色/主体、请求/提议/观察/解释、规则/模型/人工、候选/接受/拒绝分别表达 |
| edges[] | data / control / semantic 判别联合 | 执行依赖和语义关系分别有类型；候选关系不参与调度 |
| variables[] | locator, current_value, value_type, allowed_modes, review | 模型只提议位置和用途；值、类型及允许操作由程序取得 |
| evidence[] | archive pointer 或 artifact+line+event_id+pointer，source_sha256 | 精确来源定位；大日志和附件内容通过受控接口按需读取 |
| coverage | selected/unassigned、recording_complete、omitted_history、issues | 范围与缺失明确展示；未分组不等于没有发生 |

ID 由服务端生成。首次组装实验按“排序后的原始成员 ID（消息步骤用证据锚点）+kind”生成稳定摘要，标题修改不改变 ID。相同输入/配置/分组重试复用 ID；成员变化或拆分合并产生新步骤 ID，通过新修订的内容差异保留关系。第一版不自动把近似分组合并到已编辑图，避免错误继承身份。

第一版的定义范围固定为 source.archive_revision_id。其他定义修订的 Run 保留在 A 原始档案，并必须报告 HISTORICAL_RUN_SCOPE，不冒充当前定义的执行或悄悄丢失。旧定义缺失时不能用当前定义补位；跨定义的完整时间线和步骤自动合并留待后续阶段，前端可通过原始历史接口查看这些记录。

`review=accepted` 表示有人审核了表达，不等于历史真实性认证；`state=verified` 表示执行边与 A 的显式定义一致，不等于成功执行、产物可访问或具备重放授权。原始记录仍可能不完整或由上游声明，平台不把录制事实称为第三方认证。

## 归因与证据

模型输出固定 `origin=llm, review=candidate`，不能自称人工审核或规则结论。服务端规则生成的显式依赖可标 `origin=rule`；审核后的解释由服务端记录为人工修订，并另存操作者审计。

当 attributed_to 是 user/agent/tool 时，引用中必须存在同角色的原始消息依据。已知 Agent 还需匹配原始 agent_id；原始用户没有身份 ID 时保持 null，不用当前登录者补写历史身份。多来源综合解释使用 analyst 或 unknown。

角色检查只排除明显不成立的归因，不能证明自然语言结论的每个细节正确。模型即使引用了一条真实用户消息，也可能曲解内容；人工验收、对照样例和低证据时的保留未知仍有必要。

历史修订、消息时态和因果顺序要保留：用户后来采纳 Agent 提议，不改写成最初就由用户提出。消息正文中的外部命令、工具输出和 Prompt 注入都是待分析数据；不执行、不提升为解析器指令。

## 关系与执行图

`data`：source/target 是语义步骤，binding 指向 A 的真实原始节点和端口。第一版只有完全匹配 A edge+node_output 绑定才为 verified；仅路径相同、文件名相同、时间相邻或描述相似都只够形成候选。

`control`：只有 A 已声明的控制边可为 verified；LLM 建议保持 candidate。原始边跨步骤时必须保留，用户不能通过删掉展示边改变已发生的事实；可在语义层标注问题或在后续工作流编辑中创建新原始定义。

`semantic`：refines / alternative_to / continues / inspired_by / supports，表达方向为 source 对 target 的该种关系。它可以有环，永远不参加执行 DAG、Dirty 或缓存失效。候选 data/control 边同样不参与这些计算。

聚合内部的原始边不生成步骤自环，仍可在展开视图显示。子图之外的依赖必须报告为边界问题，不宣称完整工作流。

Golden Path 继续由独立 Runtime 接口计算，本版不输出一个 LLM 猜测的 golden_path 字段。后续至少校验同一工作流执行 trace、原始定义/分支、输入产物绑定、每步最新有效尝试、成功状态及依赖闭合。不能把不同 trace 的独立成功片段拼成已验证整条路径。

## 变量与编辑

locator.pointer 相对于 A 原始节点。普通输入为 `/execution/inputs/<name>/value`，嵌套 JSON 字符串参数先 `decode=json_string`，再按 value_pointer 取值，例如 `/prompt`。JSON Pointer 使用 RFC 6901 转义；禁止执行表达式或模板代码。

第一版只开放 literal 输入叶子或 params 中的实际值；variable/template/node_output/artifact 绑定需要后续专门编译，不当成可直接改写的字面量。current_value 保留来源值，模型和前端不能在解析结果里把它换成编造值。

默认只建议 keep/override。字符串或数组只有通过业务检查后才开放 append，字符串需 separator。数字、整数、布尔、对象不得 append。类型操作语义继续服从 A 的定义，不因为 Schema 支持某操作就认为它适合每个文件路径或参数。

同一步骤包含多个原始节点时，变量必须保留具体 source_node_id/locator，例如“初次角色图提示词”和“修订角色图提示词”分别列出。current_value 表示该定位的观察值，不代表整个聚合步骤的唯一最终值。第一版不按时间擅自替用户选择历史方案；审核者明确选择要开放的定位，后续执行阶段再绑定被选择的定义与产物。

语义编辑包括标题、用途、关系、变量审核，以及原子 replace_content 支持步骤拆分/合并。每次更新校验完整最终图并产生新 revision。把实际 Prompt 改为新值属于后续工作流版本/变量操作接口：该阶段必须生成新的 A 定义、保留旧来源和执行尝试，不能通过篡改本图 current_value 实现。

前端布局使用单独 LayoutUpdate：graph_revision_id + expected_layout_version + positions。拖动不改变语义修订，也不导致模型重解析。

## 限额、失败和降级

第一版解析请求沿用每批最多 50 个原始节点、400 项事实、128 KiB 模型输入的约束，GraphDocument 结构上限为 200 个语义步骤。超限返回 analysis_limit 或要求明确选择子图；不能静默截断后宣称全项目完成。原始大历史按需读取，未读部分写入 coverage。

规则层即使没有模型仍可展示原始事实，但必须标 mode=rules_only，不能声称已有语义分组。模型失败、输出截断、归因/绑定无效或组装成环时，解析任务记录失败及问题；不自动重复付费调用、不改旧图。用户显式重新发起时使用新 request_id。

生产状态机拟为 queued→running→succeeded/failed/unknown。网络结果不确定或重启时标 unknown，不自动重发。唯一请求身份包括主体、项目、request_id、输入哈希及解析配置；同 ID 不同内容冲突。

## 样例与验证

- [多轮修改与失败重试](examples/multiturn.graph.json)：5 个原始节点聚合为 4 步；失败/成功两次角色调用保留；Agent 先选题、用户后修改分别归因。
- [并行独立配乐](examples/parallel.graph.json)：配乐与角色调用同时开始，没有虚构的相邻时间依赖。
- [不完整录制](examples/partial.graph.json)：明确 recording_complete=false 和 issue，不把缺口改为完整。
- 每个 graph 均附同名 `.aica.json` 和 `.events.jsonl`，全部为合成数据；未执行生图/音频工具，输出附件只声明 unverified，没有伪造真实图片。
- 原始样例的 `.gitattributes` 禁止换行转换，保持声明的字节哈希；离线检查同时从磁盘实际字节核验，不能仅比较 JSON 语义相同。
- [ModelProposal](ModelProposal.schema.json)、[编辑请求](examples/edit-request.json)、[布局请求](examples/layout-request.json)供 D/C 按同一格式讨论。

正反验证代码位于 `../../tests/graph_contract_design`，由已有 semantic-tests / semantic-style 命令覆盖。具体执行结果和真实模型探测限制见 [VALIDATION.md](VALIDATION.md)。本阶段不据少量样例承诺语义准确率。

## 依据

- 已有 A 共享协议、BACKEND_HANDOFF、D T018/T019、C 固定版本 README/GraphExplorer/Studio；本阶段以实际源码为准，不按旧交接文档中的占位状态推断当前实现。
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)保证 JSON 字符串形式并提示可能空输出，不能代替服务端的结构、证据和语义校验。
- [Pydantic JSON Schema](https://github.com/pydantic/pydantic/blob/main/docs/concepts/json_schema.md)用于从契约模型导出 JSON Schema；本阶段使用本地已有版本实际验证导出结果。
