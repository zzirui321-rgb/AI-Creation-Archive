# Graph 0.1 解析模块与 C 联调

用自己的档案直接测试，无需启动 API：见 [本地命令与拆解报告](GRAPH_CLI.md)。

任务 `D-GRAPH-PARSER-20261017`，分支 `feature/d-graph-parser`。本阶段实现 A 标准导入 → 有界事实/对话整理 → 一次 DeepSeek 提议 → 程序组装/验证 → 持久化语义图 → C 取图与证据。消费格式保持 [C 协作文档](design/graph-v0.1/C_HANDOFF.md) 的 `aica-semantic-graph/0.1`，Schema、TS 和原三套样例未改变。

实现已在任务分支完成并本机验证，未公网部署，未修改 C 页面，未完成 C 实际前端联调。图编辑、布局保存、账号登录、平台 Fork/多分支管理和工作流执行不属于本阶段。

## 输入与生产模块

- 输入是 A 上传的 `aica-transfer/0.1`，包含原始 `aica.json` 和带哈希的附件，沿用现有项目/导入 API。不读取录制数据库、不启动用户录制器、不下载档案中的外部地址、不执行工具或脚本。
- 实时 MCP 录制导出与 A 日志事后提取的通用档案都走同一入口。提取数据缺少可信角色、Run 或依赖时保留未知/缺口，不能把提取到的工具文本当成功执行。
- `src/aica_runtime/graph/contract.py` 是唯一契约定义；原设计测试文件转为兼容导入。生成 Schema/TS 的字节保持不变。
- `context.py` 使用 A 校验器，保存原始消息角色、主体、时间、来源序号、因果引用和提交边界；关联当前修订执行尝试、原始调用、产物及参数 locator。对话容器记录消息和同节点的执行尝试，初始不擅自生成“已确认约束”；跨轮指代和目标归组由模型提出候选。
- 支持只有 artifact.recorded 的材料型输入：索引未绑定原始节点的创作附件，校验哈希后读取有界文本/JSON 正文。材料型步骤不捏造 source_node_ids/Run，以附件证据关联 artifact_ids，始终报告 ARTIFACT_ONLY_SOURCE；没有真实消息角色时保持 analyst/unknown，没有真实调用时不生成执行变量或 verified 依赖。
- `provider.py` 沿用原有仓库外 DeepSeek 配置与 HTTPS 实现；每次已接受的新请求最多发一次模型请求，无自动重试。准备输入不调用模型。排队前固定模型配置快照。
- `assembly.py` 只接收 ModelProposal；服务端分配身份，自动补齐原始节点证据、当前修订 Run 和输出产物，保留所有跨步骤显式 data/control 边及端口。候选关系与 semantic 不参与执行 DAG。变量值/类型直接从原位置读取，拒绝敏感值和编造的位置。
- `validation.py` 检查结构、角色/主体引用、所有权、范围、哈希、变量定位和聚合后的执行 DAG。失败不发布图；检查引用存在不能证明解释的自然语言含义必然正确。

最多 200 个选定节点、400 项事实、128 KiB 脱敏上下文、16 份/累计 1 MiB 事件附件。事实超限直接 413，不静默截断对话；附件缺失/无效则逐份报告 omitted_history 与 issue。完整性由原始录制声明和派生覆盖范围分别表达。若有效 A 数据的某些端口或集合超出已定 Graph Schema 表达约束，解析明确失败，不能删依赖或改写端口来凑格式。

同一原始节点的多个执行尝试始终保留；不同原始节点的失败/修订可以由模型聚合为同一创作步骤。源档案字节、导入 head、失败记录和产物均不改写。所有 LLM Claim 为 llm/candidate，结果始终 draft/executable=false。

## 实际接口

继续使用原有 Bearer 平台认证；开发令牌不是账号登录系统。数据目录、单 worker 启动及显式 CORS 来源沿用 [Gateway 说明](../../apps/gateway/README.md)。使用现有 `aica` 环境和现有 editable 安装，不新增依赖。Swagger/OpenAPI 的取图响应包含 GraphDocument。

以下前缀为 `/api/projects/{project_id}`。

| 方法/路径 | 请求 | 成功响应 |
| --- | --- | --- |
| POST `/semantic-parse-input` | `{import_id, node_ids?: string[]或null}` | 200 `{status:"success", input: PreparedInput}` |
| POST `/semantic-parses` | `{request_id, import_id, input_sha256, node_ids?: string[]或null, allow_external_processing:true}` | 首次 202 `{status:"success", parse: ParseJob}` |
| GET `/semantic-parses?offset=0&limit=50` | 无 | 200 `{status:"success", parses: ParseJob[]}`，新记录在前 |
| GET `/semantic-parses/{parse_id}` | 无 | 200 `{status:"success", parse: ParseJob}` |
| GET `/semantic-graphs/{graph_id}?revision_id={revision_id}` | 必须指定精确修订 | 200 `{status:"success", graph: GraphDocument}` |
| GET `/semantic-graphs/{graph_id}/evidence/{evidence_id}?revision_id={revision_id}` | 必须指定精确修订 | 200 `{status:"success", source_ref, role, actor_id, value, availability}` |

另有 `GET /api/graph-parser` 返回 configured/provider/model/parser_version/prompt_version/预算，不包含密钥。

PreparedInput 是实际外发的脱敏输入，包括 source、facts、evidence_catalog、parameter_locations、declared_dependencies、conversation_state、coverage 与 input_sha256。C 先展示范围和事实供用户审阅；明确取得外发授权后再提交。对同一个请求保留原 request_id 与相同载荷；不要因网络错误生成新 ID 自动再次付费。

ParseJob 字段：id、parse_id（同一个身份）、import_id、state、error_code、created_at、updated_at、source、engine、graph_id、revision_id、usage。state 为 queued/running/succeeded/failed/unknown。只有 succeeded 的 graph_id/revision_id 非 null；这时再取图。外部连接结果不明或重启中断的任务为 unknown，不自动重发。任务完成不表示图已审核。

每个项目最多一个进行中的新版解析，整个实例最多两个。SQLite 事务在调用模型前登记请求，主体+项目+request_id 唯一；同请求重试，进行中为 202，终态为 200；同 ID 不同内容为 409。保存结果与成功状态在同一事务完成，重启后仍可读取。

目前每次新的 request_id 都创建独立图草稿，parent_revision_id=null，不自动覆盖旧图，也不自动命中跨请求解析缓存。首次解析为项目登记持久化的默认语义分支身份，repository_branch_id 引用它；它不是 A 的试错 branch_id，也不表示已实现平台建分支/Fork/发布 API。解析不会推进已发布分支 head。

原始节点成员确定的步骤继续按成员和 kind 生成身份；无原始节点的材料/消息步骤以证据及 group.key 区分，允许同一剧本支持多个不同步骤，不自动声称重解析时能识别同一语义身份。

Evidence 接口按图中固定 import/原始字节定位，返回脱敏内容；附件后来不可读取时 availability=unavailable、value=null。role/actor_id 取原记录，不能由当前登录者补写。原始产物仍通过现有 `/artifacts/{artifact_id}?import_id=...` 接口取得。

对于指向登记文本/JSON 附件的 archive 证据，value 保留附件元数据，可另返回 content 和 content_state（available/metadata_only/unavailable），其中正文也按声明哈希读取与脱敏。附件 ID 不是原始节点 ID；C 对 source_node_ids=[] 且有关联附件的步骤应显示材料推断/执行来源缺失，不能显示成功 Run。正文是待分析资料，不作为指令执行。

## C 最小接入步骤

1. 保留现有 A 上传流程，取得平台 project_id 和 import_id。
2. POST semantic-parse-input，审阅 input 与解析范围；选定子图时准备与提交必须使用相同 node_ids。
3. 用户同意外发后提交 semantic-parses，保存 request_id/parse_id。
4. 定时 GET parse 状态。succeeded 时按返回的两个身份取图；failed/unknown 显示 error_code，停止轮询，不自动重发模型。
5. 将 graph.nodes/edges/variables 映射到既有画布；点击节点时读取原始调用、Run/产物及证据。模型候选显示待审核，verified 依赖与 semantic 关系分型显示。
6. 暂时只在 C 本地保留编辑/位置草稿，不能显示“已保存服务器”。GraphEditRequest、LayoutUpdate 已有格式，但相应服务尚未实现。

取图 transport 示例，类型直接引用交付的 `graph.generated.ts`：

```ts
import type { GraphDocument } from "./graph.generated";

async function readGraph(
  apiBase: string, token: string, projectId: string,
  graphId: string, revisionId: string,
): Promise<GraphDocument> {
  const url = new URL(
    `/api/projects/${encodeURIComponent(projectId)}/semantic-graphs/${encodeURIComponent(graphId)}`,
    apiBase,
  );
  url.searchParams.set("revision_id", revisionId);
  const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  const result = await response.json();
  if (!response.ok) throw new Error(result.code ?? "graph_request_failed");
  return result.graph;
}
```

401 是缺少认证；越权资源返回 404；403 是未授权外发；409 是输入哈希/请求身份冲突或解析繁忙；413 是上下文预算超限；422 是请求/范围问题。模型拒绝、输出不合法或依赖校验失败发生在异步任务中，POST 仍可返回 202，随后 ParseJob 为 failed；连接不明为 unknown。C 必须检查 job.state，不能仅看 HTTP 成功。

## 验证与限制

普通专项测试使用 HTTPX MockTransport，不调用外部模型。新增测试覆盖多轮角色、聚合失败/重试、原始端口保留、子图缺口、参数解码/脱敏、模型错误拦截、权限、异步去重、失败/重启和真实 A Recorder 导出的标准事件格式。

一次真实 DeepSeek 合成数据联调结果见 [graph-parser-live.json](tests/evidence/graph-parser-live.json)：4 步、6 变量、1 条 verified 数据依赖，9906 tokens；取图、保存、重启、幂等及原档案不变通过。API 使用进程内 TestClient，供应商调用是真实 HTTPS；没有生图、没有公网部署，也没有发送真实用户日志。

该次运行的 prompt_version 为 0.1.0；复核发现一条 semantic.continues 按时间流向排列，另有解释混合用户目标与 Agent 提议而归因 Agent。原候选结果完整保留。最终 prompt 0.1.1 明确了每种 relation 的 source→target 语义方向；没有追加模型调用，不将这一措辞调整宣称为已证明解决。C 应按契约显示关系与待审核状态；自然语言归因和关系含义仍需要人工确认与更广的评估。

对应命令沿用 collaboration.json 的 semantic-tests、semantic-style、semantic-regression、collaboration-validation、diff-check；完整结果及 Windows 符号链接限制见 STATUS。本阶段未增加 embedding/外部辅助模型；确定性关联、参数解析、证据验证和状态容器已实际实现。
