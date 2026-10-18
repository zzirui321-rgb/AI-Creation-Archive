# 最小解析器的实现规格

这是后续业务实现的任务边界，本阶段只验证契约和离线组装。第一版使用现有依赖，一次有界解析请求最多调用一次 LLM；不引入专门训练、向量索引、第二个审核模型或自动重试链。

## 1. 读取与事实索引

输入固定为主体、project_id、import_id、selected_source_node_ids 和对应的不可变档案。先验证 A Schema 与跨引用约束，再读取已声明、有哈希的历史附件。每项事实包含原始定位、内容角色、实际 actor、关联 node/run/artifact、原始时间及提交边界。

旧 evidence.py 会把事件压成 type+payload。新输入准备器必须另外保留 event_id、source_id/source_seq、session_id/agent_id、timestamp/causal_parents 和附件定位，以支持归因、跨来源关系及迟到消息。不能把旧报告的一条自由文本意图当作新的事实。

程序建立 node_by_id、run_by_id、artifact_by_id、event_by_id、producer/consumer 和显式 DAG 索引。归一化参数时保留变换路径：JSON 字符串解包只产生“如何从原文取得值”的 locator，不把解包后的路径误当作可写回原始 JSON 的路径。

输入外发前继续沿用现有脱敏和显式授权/哈希绑定。System 消息、私有推理及恢复凭证不进入输入。模型必须把日志和工具输出当资料处理。

## 2. 对话状态与候选步骤

程序维护一个可追溯的状态容器：当前已确认目标、已确认约束、原始消息时间线、明确引用的产物、当前讨论的候选步骤、待消解指代。语义增量分“已有审核结果”和“模型待确认提议”，后者不能直接覆盖已确认状态。

第一版在一次有界上下文中分析全部选定消息和调用，不为每一轮另发一次模型请求。程序把明确引用、Run 重试、相同原始 node 下的执行尝试和已有端口绑定整理给模型；跨原始节点的“同一目标”和自然语言指代由模型提议，仍需审核。

| 观察 | 程序能决定的内容 | 模型可提议的内容 |
| --- | --- | --- |
| 同 node/revision 有多个 Run | 属于同一定义的不同尝试 | 失败与重试在创作过程中的作用 |
| 不同 node 使用相同工具 | 工具相同；不能据此直接合并 | 是否服务同一可独立修改的输出 |
| 参数修改后再次调用 | 两份实际输入及时间/因果关系 | 是局部修订、新阶段还是独立备选 |
| node_output + data edge | 明确依赖和端口 | 给依赖添加可读解释 |
| 相同文件名或路径 | 值相同 | 潜在关联；不能证明同一内容或已验证依赖 |
| “改成红色”“继续第二张” | 存在真实用户消息 | 指代候选及其证据；无法消歧则 unresolved |
| 两个来源同时调用 | 两个独立来源及各自顺序 | 语义协作关系；不能按时间强行推断执行顺序 |

第一版不执行自然语言“有效约束合并”来生成新 Prompt。requirements 保留带来源的要求解释；真正开放哪个参数，由审核具体 locator 决定。遇到互相覆盖或矛盾的要求，保留原消息并输出 issue，不能把综合解释包装成用户原话。

## 3. LLM 输入与输出

输入包含带角色/身份/定位的 facts、选定 source_node_ids、已声明依赖、Run 事实、coverage，以及 ModelProposal 的 Schema。生产代码需要控制事实项数和字节；超限即报告，不能悄悄丢弃中间对话。

ModelProposal 只允许：

- groups：临时 key、成员、类型、展示标题、用途、要求和证据。
- edges：依赖候选或带 relation 的语义关系。执行关系与语义关系采用不同对象形状。
- variables：分组 key、标签、解释、原始输入 locator。
- unresolved：无法判断的问题。

模型不生成 graph/revision/source 身份、Run 状态、产物实际可用性、变量当前值/类型/操作模式或 verified 依赖。所有模型 Claim 固定 llm/candidate。禁止思维链字段，响应超限、截断、空 content、未知引用或不符合 Schema 时终止该次解析。

## 4. 程序组装

```text
validate_A(archive)
facts = prepare_bounded_redacted_facts(archive, history, scope)
verify_request_input_hash_and_authorization(facts)
proposal = validate_ModelProposal(call_model_once(facts))
assign_stable_step_ids(proposal.groups)
check_single_ownership_and_coverage()
attach_actual_current_revision_runs_and_output_artifact_refs()
preserve_every_declared_cross_step_A_edge()
keep_other_model_edges_as_candidates_or_semantic_relations()
hydrate_variable_values_from_source_locators()
validate_evidence_roles_pointers_hashes_and_types()
validate_verified_execution_DAG_after_grouping()
save_new_draft_without_replacing_user_revision()
```

语义候选和执行候选不参加 DAG/Dirty/Cache。A 显式边无论模型是否提到都保留；不允许用户编辑语义图时把它从事实视图删除。需要改变实际工作流时，另走新原始定义的版本操作。

步骤聚合导致执行环时返回 invalid_semantic_graph 与涉及的成员，不自动删边修复，也不自动付费请求第二遍。已有图仍可读，用户可拆分步骤后重新提交。

## 5. ID、重新解析和人工编辑

同一原始成员集合与 kind 在同一图内可稳定识别，标题不是身份。重解析时建立新草稿与 parent_revision_id；已审核的旧修订保持不变。

第一版显式比较新旧内容，用户选择采纳；不做语义相似度驱动的自动合并。拆分/合并通过 replace_content 原子提交，ID 变化作为新修订差异记录。后续可增加谱系映射，但不以猜测替代步骤身份。

编辑事务按 expected_revision_id 检查分支头；先验证整个候选图，再写不可变修订并更新 head。幂等请求在主体/项目范围内唯一，同 ID 不同内容拒绝。review 的操作者信息由认证层单独记审计，不能信任客户端 origin 声明。

## 6. 缓存与扩展条件

解析缓存至少绑定主体/权限范围、项目、import/源哈希、所选节点、实际读取的历史哈希、脱敏后 input_sha256、parser/prompt/model/settings，以及使用到的工具目录版本。它与 Runtime 的执行缓存是两种东西，不共享键，也不因解析缓存命中就宣称工具执行成功。

仅当评估暴露对应问题时再引入组件：

| 组件 | 引入条件 | 能提供的帮助 | 不能证明 |
| --- | --- | --- | --- |
| Embedding | 历史过长且精确引用/关键词找不到相关片段 | 召回相似历史、目标和人工纠错案例 | 因果、作者身份、实际使用了某产物 |
| 专用分段/指代模型 | 多轮边界或指代错误占主要比例，已有足够标注集 | 降低该类局部错误 | 全图执行正确 |
| 视觉/OCR | 产品需要解释附件内容，日志不足且已获得访问/处理授权 | 补充图像、文档可观察内容 | 用户意图或图像确由某次调用产生 |
| AST/静态数据流 | 录制中含脚本且仅参数记录无法说明依赖 | 解析语法与部分输入输出线索 | 运行时全部行为；不能执行脚本来猜依赖 |
| 第二模型审核 | 已有量化收益，成本/延迟允许 | 提供额外语义检查信号 | 独立真实性保证 |

本阶段真实探测用于验证格式可落地，不能据两个调用推导长期准确率，也不能证明某个外部模型没有价值。
