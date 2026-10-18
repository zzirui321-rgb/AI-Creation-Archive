# AiCA D → C Graph 接口协作说明 v0.1

文档编号：`D-C-GRAPH-0.1-r3`。交付日期：2026-10-17，Asia/Shanghai。D 负责解析结果和服务端契约，C 负责应用、画布及编辑交互。

**D 侧已确定本文件对应的 JSON 字段、枚举和请求结构，作为 0.1 交付格式；C 接收确认待回传。解析、持久化取图和证据接口已在任务分支实现，实际请求/响应见 [运行接口](../../GRAPH_PARSER.md)。图编辑和布局接口仍未实现，尚未公网部署或完成 C 页面联调。** 本页合成样例继续可用于画布开发，不能把样例响应显示成真实在线结果。

格式标识固定为 `aica-semantic-graph/0.1`。本次整理不修改已生成的 Schema/TypeScript。新增字段、删除字段、改变枚举或语义均需 D/C 对齐版本，不能由一方在同一 format 下静默修改。

现有 `/api/projects/{id}/graph` 返回 A 的原始图；它与本文件的语义图是不同接口。A 的原始档案仍是可观察事实唯一来源，D 的语义图是可追溯的派生视图。

## 可以直接消费的文件

- [GraphDocument.schema.json](GraphDocument.schema.json)：前端消费的语义图格式。
- [graph.generated.ts](graph.generated.ts)：与 Schema 同源生成的 TypeScript 类型，C 直接引用，不另维护相似类型。
- [examples/multiturn.graph.json](examples/multiturn.graph.json)：4 个语义步骤、失败重试和数据依赖的完整样例。
- [examples/multiturn.aica.json](examples/multiturn.aica.json) / [事件记录](examples/multiturn.events.jsonl)：展开节点时的来源参考。
- [GraphEditRequest.schema.json](GraphEditRequest.schema.json) / [编辑请求](examples/edit-request.json)：语义图编辑。
- [LayoutUpdate.schema.json](LayoutUpdate.schema.json) / [布局请求](examples/layout-request.json)：位置保存。
- [并行样例](examples/parallel.graph.json)与[不完整记录样例](examples/partial.graph.json)：分别验证独立步骤和缺项展示。

`ModelProposal` 是 D 内部与 LLM 通信的中间结构，C 不直接消费它；C 只消费服务端组装、校验后的 `GraphDocument`。

## 1. 接入顺序与责任

1. C 引入生成类型，用完整样例替换固定节点、固定连线、固定变量列表。
2. C 实现节点选择、调用展开、证据、关系类型、待审核标记和范围缺口。
3. C 将取图、保存语义编辑、取证据、保存布局放到可替换的 transport 层。服务上线前使用明确标记的本地样例。
4. D 已实现解析、身份校验、持久化及解析生命周期；C 可按运行接口接入取图。语义编辑、布局及平台分支功能后续实现，再完成实际前端联调。

本文件不冻结登录、社区、仓库列表、Fork、新建 branch 或运行器接口。这些属于平台后端协作，不应因存在画布或本地演示按钮就视为已完成。

## 2. 通用 JSON 规则与身份

- 对象采用严格字段集合，不允许额外字段。所有下表字段均为必填；允许 null 的字段也必须显式传 null。数组为空时传 `[]`，不能传 null。
- ID 统一是服务端分配的不透明字符串，最长 200 字符，符合 `^[A-Za-z0-9][A-Za-z0-9_.:-]*$`。前端不按标题、数组下标或文件名生成业务身份。
- SHA256 为 64 位小写十六进制，计算收到的原始字节。不要先重新序列化 JSON 再比较。原始样例禁用 Git 换行转换，包中保留精确字节。
- 文本由前端按普通文字显示；日志、模型解释和工具输出不作为 HTML、命令或脚本执行。
- 图对象不携带 React Flow 的 position、selected、measured 等状态，这些分别属于布局和前端状态。

| 身份 | 含义 | 不能混用 |
| --- | --- | --- |
| API 路径中的 project_id | 平台项目/产品中的仓库身份 | 不用 A metadata.project_id 作为权限依据 |
| graph_id | 一个派生语义图的身份 | 不等于原始 Graph revision |
| revision_id | 当前不可变语义修订 | 不等于原始档案修订或录制提交序号 |
| parent_revision_id | 上一语义修订，首次为 null | 不能指向自己 |
| repository_branch_id | 平台版本分支 | 不等于 A 的创作试错 branch_id |
| source.archive_revision_id | A 原始工作流定义修订 | 用于绑定原始节点和 Run |
| source.snapshot_id / through_seq | 原始录制快照及提交边界 | 不等于 Git commit |

标题修改不改变步骤 ID。步骤拆分、合并或原始成员改变时，D 分配/确认新 ID 并创建新语义修订；C 不自行猜测新旧节点是否为同一个步骤。

## 3. GraphDocument 顶层

| 字段 | 类型 | 含义与 C 的处理 |
| --- | --- | --- |
| format | 固定字符串 | 必须为 `aica-semantic-graph/0.1`；未知版本提示不支持 |
| graph_id / revision_id | ID | 作为请求、缓存和画布数据身份 |
| parent_revision_id | ID 或 null | 修订链；首次为 null |
| repository_branch_id | ID | 所属平台分支 |
| state | draft / accepted | 图的审核/采纳状态，不能当运行状态 |
| executable | 固定 false | 本版语义图不能直接执行，accepted 也不授予执行权限 |
| source | Source | 精确原始档案及快照身份 |
| parse | ParseInfo | 解析来源、版本、模型和输入哈希 |
| nodes | Step[] | 创作步骤，最多 200 项 |
| edges | Edge[] | 分类型关系，最多 1000 项 |
| variables | Variable[] | 开放参数建议，最多 200 项 |
| evidence | Evidence[] | 证据定位，最多 2000 项；不内嵌完整私有日志 |
| coverage | Coverage | 解析范围、未归组内容和缺口 |

下例是完整且可校验的单步骤结构示例，仅选择完整合成档案中的 `raw-plan`，不代表已解析整个项目。完整多步骤数据见文件列表。

```json
{
  "format": "aica-semantic-graph/0.1",
  "graph_id": "fixture-graph",
  "revision_id": "semantic-revision-1",
  "parent_revision_id": null,
  "repository_branch_id": "repo-main",
  "state": "draft",
  "executable": false,
  "source": {
    "import_id": "fixture-import",
    "archive_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246",
    "archive_revision_id": "raw-revision-1",
    "snapshot_id": "fixture-snapshot",
    "stream_id": "fixture-stream",
    "through_seq": 4
  },
  "parse": {
    "parser_version": "design-0.1",
    "prompt_version": "design-0.1",
    "model": null,
    "mode": "human_annotated_fixture",
    "input_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
  },
  "nodes": [
    {
      "id": "step-plan",
      "kind": "intent",
      "label": "确定创作目标",
      "purpose": {
        "text": "用户要求太空漫画与配乐。",
        "attributed_to": "user",
        "actor_id": null,
        "speech_act": "request",
        "evidence_ids": [
          "e-message-1"
        ],
        "origin": "human",
        "review": "accepted"
      },
      "requirements": [],
      "source_node_ids": [
        "raw-plan"
      ],
      "run_ids": [],
      "artifact_ids": [],
      "evidence_ids": [
        "e-node-0",
        "e-message-1"
      ]
    }
  ],
  "edges": [],
  "variables": [],
  "evidence": [
    {
      "kind": "archive",
      "id": "e-node-0",
      "pointer": "/graph/nodes/0",
      "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
    },
    {
      "kind": "event",
      "id": "e-message-1",
      "artifact_id": "fixture-history",
      "line": 1,
      "event_id": "event-1",
      "pointer": "/payload/content",
      "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
    }
  ],
  "coverage": {
    "recording_complete": true,
    "selected_source_node_ids": [
      "raw-plan"
    ],
    "unassigned_source_node_ids": [],
    "omitted_history_artifact_ids": [],
    "issues": []
  }
}
```

### Source 与 ParseInfo

| 对象/字段 | 类型 | 说明 |
| --- | --- | --- |
| source.import_id | ID | 不可变平台导入快照 |
| source.archive_sha256 | SHA256 | 原始 aica.json 字节身份 |
| source.archive_revision_id | ID | A 的定义修订 |
| source.snapshot_id / stream_id | ID | A 的录制快照和流 |
| source.through_seq | 非负整数 | 只使用截至此边界的事件 |
| parse.parser_version / prompt_version | 非空字符串 | 解析程序与提示模板版本 |
| parse.model | 字符串或 null | 实际模型名称；无模型时为 null |
| parse.mode | rules_only / rules_llm / human_annotated_fixture | 区分规则、真实模型与人工合成样例 |
| parse.input_sha256 | SHA256 | 实际分析上下文身份，C 不自行重算或修改 |

## 4. Step：画布节点

一个节点代表可独立修改或复用的创作步骤，可包含多次原始调用；一次失败不是一个步骤被放弃的充分依据。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | ID | React Flow 节点 ID |
| kind | intent / decision / generation / transform / composition / other | 意图、决策、生成、变换、合成、其他 |
| label | 1–200 字符 | 画布标题，不再按 ID 写死名称 |
| purpose | Claim | 步骤用途与来源解释 |
| requirements | Claim[] | 带来源的要求，最多 50 项；不能自动拼成新的执行 Prompt |
| source_node_ids | ID[] | 该步骤拥有的 A 当前定义节点；同一原始节点最多归属一步 |
| run_ids | ID[] | 这些原始节点在所选原始修订中的实际执行尝试 |
| artifact_ids | ID[] | 有原始 producer 绑定时为产出实例；附件推断步骤为有证据的关联材料，需显示缺失执行来源；不是图片 URL |
| evidence_ids | 非空 ID[] | 引用本 GraphDocument.evidence 中的证据 |

纯消息意图步骤可以没有 source_node_ids，但必须有真实消息证据。展开时按引用读取 A 的工具、模型、实际参数、Run 状态和产物。原始 Node/Run/Artifact 类型继续以 A 的 [共享 Schema](../../../schema/src/aica_schema/aica.schema.json)为准，不由 C/D 复制一套类型。

兼容只有附件登记的有效 A 档案：source_node_ids/run_ids 可以为空，其他 kind 的候选步骤也可由登记附件的 archive 证据锚定；artifact_ids 关联这些附件，coverage 必须有 ARTIFACT_ONLY_SOURCE。C 应显示“材料推断，缺少执行记录”，不能把它画成有成功 Run 的步骤。附件关联不证明实际产出关系；无原始输入位置时不提供可执行变量。JSON 字段和枚举未改变，仅补明确此输入场景的来源语义。

```json
{
  "id": "step-character",
  "kind": "generation",
  "label": "生成并修订角色图",
  "purpose": {
    "text": "Agent 最初选择黑猫与水彩，用户随后保留黑猫并要求红色宇航服。",
    "attributed_to": "analyst",
    "actor_id": null,
    "speech_act": "interpretation",
    "evidence_ids": [
      "e-message-2",
      "e-message-3"
    ],
    "origin": "human",
    "review": "accepted"
  },
  "requirements": [],
  "source_node_ids": [
    "raw-character-first",
    "raw-character-revised"
  ],
  "run_ids": [
    "run-raw-character-first",
    "run-raw-character-revised"
  ],
  "artifact_ids": [
    "artifact-raw-character-revised"
  ],
  "evidence_ids": [
    "e-node-1",
    "e-node-2",
    "e-message-2",
    "e-message-3"
  ]
}
```

### Claim：字段级语义解释

| 字段 | 值/类型 | 含义 |
| --- | --- | --- |
| text | 1–3000 字符 | 可读解释 |
| attributed_to | user / agent / tool / analyst / unknown | 谁表达或产生该内容；analyst 表示分析解释，不表示另有一个实际用户 |
| actor_id | ID 或 null | 原记录中已知主体，未知为 null；不能用当前登录者补写历史用户 |
| speech_act | request / proposal / observation / interpretation | 要求、提议、观察或解释 |
| evidence_ids | 非空 ID[] | 依据 |
| origin | rule / llm / human | 这段解释如何得到 |
| review | candidate / accepted / rejected | 待审核、已接受或已驳回 |

LLM 初始解释固定 llm/candidate。`review=accepted` 仅表示表达经过审核，不证明历史真实性或可重放。C 对 candidate 显示“待审核”；rejected 保留来源，可折叠显示“解释已驳回”。不要用 origin 或 attributed_to 代替运行成功状态。

## 5. Edge：连线必须按 kind 分型

所有边包含 id、source、target、kind、explanation。source/target 都引用语义步骤 ID。内部原始调用之间的关系在展开视图展示，不生成步骤自环。

| 类型 | 专有字段 | 显示建议 | 是否参与后续执行依赖计算 |
| --- | --- | --- | --- |
| data | state、archive_edge_id、binding | verified 实线，candidate 虚线并标“候选数据依赖” | 仅 verified；本版仍不执行 |
| control | state、archive_edge_id | verified 实线，candidate 虚线并标“候选控制依赖” | 仅 verified；本版仍不执行 |
| semantic | relation | 独立颜色/虚线，可单独筛选 | 永远不参与 |

data/control 的 state 是 candidate 或 verified。verified 必须匹配 A 原始依赖；data 还需完整匹配端口。`archive_edge_id` 在候选时可为 null。data 的 binding 在候选时可为 null，verified 时必须存在。

data.binding 的 source_node_id/source_output/target_node_id/target_input 指向原始节点和端口。**source/target 是语义步骤，binding 的节点是 A 原始节点，不能相互替代。**

```json
{
  "kind": "data",
  "id": "edge-character-panel",
  "source": "step-character",
  "target": "step-panel",
  "state": "verified",
  "archive_edge_id": "raw-character-panel",
  "binding": {
    "source_node_id": "raw-character-revised",
    "source_output": "image",
    "target_node_id": "raw-panel",
    "target_input": "reference_image"
  },
  "explanation": {
    "text": "A 定义明确的 image → reference_image 绑定。",
    "attributed_to": "analyst",
    "actor_id": null,
    "speech_act": "interpretation",
    "evidence_ids": [
      "e-node-2",
      "e-node-3"
    ],
    "origin": "human",
    "review": "accepted"
  }
}
```

control 不允许出现 binding 或 relation 字段：

```json
{
  "kind": "control",
  "id": "edge-plan-character-candidate",
  "source": "step-plan",
  "target": "step-character",
  "state": "candidate",
  "archive_edge_id": null,
  "explanation": {
    "text": "创作意图可能约束角色图步骤，尚无原始控制依赖绑定。",
    "attributed_to": "analyst",
    "actor_id": null,
    "speech_act": "interpretation",
    "evidence_ids": [
      "e-node-0",
      "e-node-2"
    ],
    "origin": "llm",
    "review": "candidate"
  }
}
```

semantic 不允许出现 state、archive_edge_id 或 binding。relation 为 refines、alternative_to、continues、inspired_by、supports；方向统一读作“source 对 target 具有此关系”，例如 source refines target。语义关系允许有环，不能用于推导重算范围。

```json
{
  "kind": "semantic",
  "id": "semantic-plan-panel",
  "source": "step-plan",
  "target": "step-panel",
  "relation": "supports",
  "explanation": {
    "text": "创作目标为分镜内容提供语义依据。",
    "attributed_to": "analyst",
    "actor_id": null,
    "speech_act": "interpretation",
    "evidence_ids": [
      "e-message-1",
      "e-message-3"
    ],
    "origin": "llm",
    "review": "candidate"
  }
}
```

verified 的含义是“与原始定义绑定一致”，不是“工具已成功”或“产物已下载”。按时间相邻、同名文件或文字相似发现的关系只能作为候选。

## 6. Variable：可开放参数建议

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id / step_id | ID | 变量身份与所属语义步骤 |
| label | 1–200 字符 | 展示名 |
| description | Claim | 用途与依据 |
| locator | ValueLocator | 原始值的位置和解码方式 |
| value_type | string / array / number / integer / boolean / object | 服务端从原值计算 |
| current_value | JSON 值 | 该 locator 对应的观察值，不代表整个聚合步骤的唯一最终值 |
| allowed_modes | keep / override / append 的非空数组 | C 只展示返回的操作，不固定显示三项 |
| separator | 字符串或 null | 仅开放字符串 append 时必须提供 |
| review | candidate / accepted / rejected | 只有 accepted 的参数进入后续正式修改流程 |

locator.source_node_id 是 A 节点；evidence_id 引用该节点证据。pointer 相对于该原始节点；decode 是 identity 或 json_string；value_pointer 指向解码后的值，空字符串表示根值。不要把解码后的指针当作原始档案的直接写入路径。

```json
{
  "id": "var-character-prompt",
  "step_id": "step-character",
  "label": "角色生成提示词",
  "description": {
    "text": "可修改修订角色图的提示词。",
    "attributed_to": "analyst",
    "actor_id": null,
    "speech_act": "interpretation",
    "evidence_ids": [
      "e-node-2"
    ],
    "origin": "human",
    "review": "accepted"
  },
  "locator": {
    "source_node_id": "raw-character-revised",
    "evidence_id": "e-node-2",
    "pointer": "/execution/inputs/arguments/value",
    "decode": "json_string",
    "value_pointer": "/prompt"
  },
  "value_type": "string",
  "current_value": "黑猫宇航员，红色宇航服，水彩风格",
  "allowed_modes": [
    "keep",
    "override"
  ],
  "separator": null,
  "review": "candidate"
}
```

第一版只开放 literal 输入或 params 中的实际值。数字、整数、布尔、对象不支持 append。相同步骤内初次/修订调用的参数保留不同 locator，不能按同名合并。C 可以保存本地“希望修改的值”，但不能改写 GraphDocument.current_value 来假装历史已经改变；实际变量操作和新工作流定义属于后续 Runtime/平台 API。

## 7. Evidence 与 Coverage：来源和缺项

Evidence 是两个严格对象类型，均包含 id、kind、source_sha256。

| kind | 其他必填字段 | 定位含义 |
| --- | --- | --- |
| archive | pointer | 固定 source 导入档案内的 JSON Pointer |
| event | artifact_id、line、event_id、pointer | 事件附件，第几行（从 1 开始），对应事件内的 JSON Pointer |

```json
{
  "kind": "archive",
  "id": "e-node-0",
  "pointer": "/graph/nodes/0",
  "source_sha256": "13ab667b9d19b4a9b145af072e141166c03c77d3fc5b792df36a2c46af203246"
}
```

```json
{
  "kind": "event",
  "id": "e-message-3",
  "artifact_id": "fixture-history",
  "line": 3,
  "event_id": "event-3",
  "pointer": "/payload/content",
  "source_sha256": "22e4bd6fd8a6b0c73a76db2d04dbb5a171dae2f1d5bcb86743c5116566ef3305"
}
```

节点点击后，通过 evidence_ids 查本图证据定位，再经证据接口取已授权、脱敏的内容。C 不直接访问用户电脑路径、数据库或录制 Session 凭证。产物预览同样经 D 的访问接口；artifact_ids 不能当作 URL。

| Coverage 字段 | 类型 | 前端含义 |
| --- | --- | --- |
| recording_complete | boolean | A 声明的录制完整性，不等于本次解析覆盖整个项目 |
| selected_source_node_ids | ID[] | 本次解析选择范围 |
| unassigned_source_node_ids | ID[] | 已选但尚未归组的原始节点，必须保留可见入口 |
| omitted_history_artifact_ids | ID[] | 未读取的历史附件 |
| issues | Issue[] | 结构化问题，最多 1000 项 |

Issue 包含 id、code、severity（warning/error）、message、node_ids（语义步骤）、evidence_ids。C 保留 code 原值，未知 code 也能显示 message。`EXTERNAL_DEPENDENCY` 表示子图边界；`HISTORICAL_RUN_SCOPE` 表示其他原始定义修订的执行未纳入当前步骤；`UNASSIGNED_SOURCE` 表示尚未归组。不能因 recording_complete=true 就隐藏其余问题。

本版 source_node_ids/run_ids 绑定 source.archive_revision_id。其他定义的 Run 仍保留在原始档案，显示范围提示；旧定义缺失时不能用当前定义补位。

## 8. GraphEditRequest：语义图编辑

请求格式已定，本节保存行为是后续接口约定，尚未实现。

请求必须包含 request_id、expected_revision_id、operations（1–100 项）。请求顺序有意义，服务端原子验证最终结果并生成新修订。首次成功返回 201；同一请求重试返回原结果；同 ID 不同内容或预期修订落后返回 409。

```json
{
  "request_id": "edit-request-1",
  "expected_revision_id": "semantic-revision-1",
  "operations": [
    {
      "op": "set_label",
      "node_id": "step-character",
      "value": "角色参考图"
    }
  ]
}
```

| op | 必填载荷 | 用途 |
| --- | --- | --- |
| set_label | node_id、value | 修改标题 |
| replace_purpose | node_id、value:Claim | 修订用途解释 |
| upsert_edge | value:Edge | 新增/修改关系 |
| delete_edge | edge_id | 删除可编辑关系 |
| review_variable | variable_id、review:accepted/rejected | 审核参数建议 |
| replace_content | 完整 nodes、edges、variables | 原子拆分/合并步骤或批量调整；要求和变量描述等也经此操作调整 |

source、evidence、parse、当前观察值及原始引用事实由 D 管理，不是普通客户端可写字段。已有 A 显式依赖不能通过删展示边被抹去；改变实际工作流需创建新原始定义。客户端不能凭 origin=human/rule 自行获得审核权限，服务端根据认证操作者记录审核和审计。

保存成功后用返回的新 GraphDocument 替换当前数据；保留选中 ID 若它仍存在。收到 409 时保留本地草稿，读取新版本并比较，不自动覆盖或强行重发。新的解析结果另成草稿，不覆盖用户已经修改的图。

## 9. LayoutUpdate：布局独立保存

请求格式已定，本节保存行为是后续接口约定，尚未实现。

请求包含 request_id、graph_revision_id、expected_layout_version（非负整数）、positions。每项为 node_id、x、y，坐标为有限数值；最多 200 项。坐标是画布坐标，不是页面 CSS 像素位置。

```json
{
  "request_id": "layout-request-1",
  "graph_revision_id": "semantic-revision-1",
  "expected_layout_version": 0,
  "positions": [
    {
      "node_id": "step-character",
      "x": 200.0,
      "y": 80.0
    }
  ]
}
```

位置更新不改变 semantic revision、不调用模型、不改变原始档案。位置列表作为该请求的完整布局快照，未包含节点由 C 使用自动布局/默认位置；不得把遗漏当删除步骤。服务端返回新的 layout_version 和实际 positions。切换图修订后用对应 graph_revision_id 的布局，不能把旧异步响应覆盖新画布。

## 10. 对现有 C 画布的字段映射

前端可将 GraphDocument.nodes 映射到 React Flow，但不把 React Flow Node/Edge 作为持久化协议。

| C 当前字段或行为 | 正式数据来源 | 处理 |
| --- | --- | --- |
| Step.id / title | node.id / node.label | 标题直接显示，不再维护按 ID 写死的 nodeLabels |
| subtitle / 描述 | node.purpose.text | 同时展示 origin/review，模型候选不标为已确认事实 |
| prompt | variable.locator + 证据接口 | 分清观察到的 Prompt 与用户修改草稿，不从标题反推 Prompt |
| 固定 links | graph.edges | verified data/control 实线；候选执行关系与 semantic 分别用虚线/筛选显示 |
| dead / 失败节点 | A 原始 branch + Run | 保留失败尝试；某次 Run 失败不代表整个语义步骤失败或被用户放弃 |
| 展开节点 | source_node_ids / run_ids / artifact_ids / evidence_ids | 展示原始调用、时间、状态和产物；运行状态继续读取 A/D Run，不让 LLM 填写 |
| 拖动位置 | LayoutUpdate | 与语义编辑分别保存，不触发解析 |
| Golden Path | 后续 Runtime 独立接口 | 当前语义结果缺少该字段时隐藏或显示尚未计算，不能把 edges 拓扑排序冒充推荐成功路径 |
| 变量 A/B/C | allowed_modes + review | 只有 accepted 的开放参数进入修改流程；不要对所有类型固定显示 append |

默认一次展示创作步骤，点击后展开真实调用。聚合步骤的执行状态不能仅取“最后一个 Run”：后端后续应提供按原始节点/输入版本计算的状态汇总。第一版直接在详情列出 Run，避免前端猜测成功。

## 11. 接口路径与实现状态

输入准备、发起/查询解析、取图和证据已实现，精确响应包装与状态见 [运行接口](../../GRAPH_PARSER.md)；下表保留原规划，标注的 revisions/layout 仍未实现，不能调用其保存行为。

统一继续使用现有 project_id；产品层可称“仓库”，本阶段不另建与现有 project 重复的后端身份。

| 方法/路径，前缀 `/api/projects/{project_id}` | 请求/响应 | 语义 |
| --- | --- | --- |
| POST `/semantic-parse-input` | request: import_id, node_ids（可选）；200: `{status:"success", input:{input_sha256, source, coverage, facts, ...}}` | 已实现；准备并预览实际外发输入，不调用模型 |
| POST `/semantic-parses` | request: request_id, import_id, node_ids（与预览一致）, input_sha256, allow_external_processing；202: `{status:"success", parse:ParseJob}` | 已实现；发起异步解析，同请求重试幂等 |
| GET `/semantic-parses/{parse_id}` | `{status:"success", parse:ParseJob}`；含 parse_id/state/source/graph_id/revision_id/error_code/usage | 已实现；轮询状态；unknown 需要显式处理，不能自动重新付费调用 |
| GET `/semantic-graphs/{graph_id}?revision_id=...` | `{status:"success", graph:GraphDocument}` | 读取精确修订；不存在即 404，不悄悄返回最新版本 |
| POST `/semantic-graphs/{graph_id}/revisions` | GraphEditRequest；201: 新 GraphDocument | **未实现**；规划为按顺序应用操作，事务校验最终图；expected_revision_id 不匹配返回 409 |
| GET `/semantic-graphs/{graph_id}/evidence/{evidence_id}?revision_id=...` | 脱敏 source_ref、role、actor_id、value、availability | 按需获取有权访问的原始事实；不返回恢复凭证/系统消息/私有推理 |
| GET `/semantic-graphs/{graph_id}/layout?graph_revision_id=...` | graph_revision_id, layout_version, positions | **未实现**；前端暂用本地布局 |
| POST `/semantic-graphs/{graph_id}/layout` | LayoutUpdate；200: 新 layout_version 与 positions | **未实现**；规划独立乐观锁，不修改 semantic revision |

解析成功不自动更新平台分支 head。以下修订提交行为仍是后续实现要求：提交编辑的事务需要同时检查目标 branch 的 head 仍等于 expected_revision_id，新增不可变修订后原子更新 head。新 parse 先是草稿，用户选择采纳时再经同一提交校验进入分支历史。

GraphEditRequest 支持 set_label、replace_purpose、upsert_edge、delete_edge、review_variable、replace_content。replace_content 用于步骤拆分/合并和批量调整，必须提供完整 nodes/edges/variables，所有来源仍受服务端校验；不能删除应保留的 A 显式依赖。

服务端根据已认证操作者记录人工审核，拒绝客户端伪装成规则或模型输出。客户端不能提交 source/evidence/parse 元数据修改。对同一主体+项目+request_id，相同请求返回原结果，不同内容 409；失败事务不产生半成品修订。

`state=accepted` 的发布/审核入口归后续平台版本 API，本阶段不设计客户端直接改变 state 的操作。图草稿的字段可以逐项被审核，仍不表示它已经入库发布或能执行。

## 12. 错误与前端状态

沿用 `{status:"error", code, message}`，冲突可附 details.current_revision_id 或 current_layout_version。

| HTTP/code | C 行为 |
| --- | --- |
| 401 / 403 | 处理会话失效或权限不足，不把本地演示身份当平台凭证 |
| 404 / not_found | 该修订或引用不可访问，不替换成其他版本 |
| 409 / graph_revision_conflict | 保留本地编辑，加载新版本比较，不静默覆盖 |
| 409 / layout_version_conflict | 保留拖动草稿，显式协调布局版本 |
| 413 / analysis_limit | 选择明确子图，展示解析范围 |
| 422 / invalid_semantic_graph | 将服务端校验问题对应到字段/关系 |
| failed / unknown parse | 显示失败或待核实；不自行重发模型调用 |

GET 精确修订和布局响应均应包含对应身份，前端切换项目/分支后丢弃旧请求的迟到结果。请求取消只停止等待；能否取消已经发送的模型请求由后端另行报告。

## 13. C 接收与联调验收清单

- [ ] 确认采用 format `aica-semantic-graph/0.1` 和本包生成类型；不直接消费 ModelProposal。
- [ ] 完整样例显示 4 个步骤；角色图节点展开保留失败与再次调用；配乐不被错误串到分镜后。
- [ ] verified data/control、候选执行关系、semantic 关系在画布上可辨认；未知 relation/code 不使页面崩溃。
- [ ] 详情面板显示用途、归因、审核状态、原始调用/Run/产物和证据入口。
- [ ] 参数列表由 variables 动态生成，keep/override/append 遵循 allowed_modes，候选参数不会被当作已开放执行参数。
- [ ] partial 样例展示缺口；子图与旧修订范围有提示；没有 Golden Path 时不伪造高亮。
- [ ] 修改标题、关系、拆分/合并可生成 GraphEditRequest；409 保留草稿。
- [ ] 拖动只生成 LayoutUpdate，图修订与布局版本分开；迟到响应不会覆盖另一个项目/修订。
- [ ] 服务未上线时明确处于本地样例模式，不能显示“已保存服务器”或“真实执行成功”。

C 需要反馈的产品点：默认步骤视图及展开方式、各类候选标记、动态变量入口、布局共享/个人偏好、现有页面缺少的业务字段。收到反馈后用明确变更记录调整版本；本文件不代替 C 的接受记录。

## 14. 冻结文件指纹与交付状态

以下指纹用于确认 D/C 拿到的是同一组接口文件；交付 ZIP 的 MANIFEST.json 另列每个文件的原始字节 SHA256。先解压后查看文档，目录关系必须保留。

| 文件 | SHA256 |
| --- | --- |
| GraphDocument.schema.json | `4268cc41a1c3996a52d823d4e89c10280ee2cda4e5cfbaa4a823c80dfab7df3e` |
| GraphEditRequest.schema.json | `232c325483d788f5e1b770d695a104437a743bd054fe85e13ac66ae9f7cd749a` |
| LayoutUpdate.schema.json | `74f376c854ee02b3e21939a0777e0ef11665f73866e5f14fd990b1c2909f0106` |
| graph.generated.ts | `0631a1031a21985358988944f52f46dbee2f4b822180ccb72c34022c1f4ba850` |

当前完成状态：通用输入准备、对话/执行索引、模型候选解析、图组装验证、异步状态、持久化及取图/证据 API 已实现；运行方式和验证见 GRAPH_PARSER.md。语义编辑/布局、平台账号/Fork/多分支和正式前端联调仍待实现。代码上传不表示公网部署或 C 已接受。
