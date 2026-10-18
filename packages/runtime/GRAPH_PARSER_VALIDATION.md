# Graph 解析模块验证记录

任务 `D-GRAPH-PARSER-20261017`，2026-10-17，Asia/Shanghai。单阶段范围：生产解析、异步持久化、C 取图/证据接口；格式 `aica-semantic-graph/0.1` 不变。未改 A、C、B、依赖或运行环境，未公网部署。

所有 Python 命令使用已有 `conda run --no-capture-output -n aica`。

| 检查 | 实际结果 |
| --- | --- |
| `python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests`，最终解析版本 | **134 passed in 23.20s**；其中新增 33 项，全部为离线/MockTransport |
| `python -m ruff check packages/runtime apps/gateway`，最终解析版本 | All checks passed |
| `python -m pytest -q -c apps/gateway/pytest.ini packages/schema/tests packages/mcp-adapters/tests packages/runtime/tests apps/gateway/tests`，首次实现检查点 | 235 passed、2 failed in 113.59s；两个失败均为 A 原有 symlink 测试在 Windows 创建链接时 WinError 1314；没有规避/跳过/修改测试 |
| 一次真实模型链路（prompt 0.1.0） | succeeded，DeepSeek flash，9906 tokens；4 步、6 变量、1 条 verified 依赖；重启读回、同请求去重、原档案不变 |
| 冻结格式 | 4 个消费契约文件与 `ad8a1ce` 字节一致；既有生成物漂移测试通过 |

全量回归运行后仅调整了关系方向提示（prompt 0.1.1）、对象字面量参数定位，增加两个边界检查并完善交付说明；最终 134 项专项及 Ruff 已覆盖这些改动。没有把此前全量结果声称为最终提交所有测试全绿。协作/差异/凭证与文件链接检查在任务交付记录中登记。

## 离线验证覆盖

- 同一创作步骤聚合多次原始调用、失败和修订，完整保留当前修订 Run；独立音频与图片任务保持可分开处理。
- 消息 role/actor/source_seq/causal_parents/commit_seq 与来源定位保留；系统消息和内部会话凭证不进入输入。
- 直接调用 A Recorder 并导出、打包、导入，经新 API 读取消息；不依赖该 Recorder 的数据库。
- A 日志事后提取与空档案通过相同准备/解析流程；字典参数和 JSON 字符串参数分别提供真实 locator。
- 显式依赖与端口由程序生成 verified 边；候选执行边与语义环不会变成已验证执行依赖；聚合引入执行环时拒绝。
- 模型越界归组、双重拥有、假证据、错误角色/主体、自审、编造变量位置及混合边对象被拒绝。
- 子图外部依赖、未归组内容、缺失/无效历史和录制缺口明确报告；预算超限拒绝，原字节哈希参与验证。
- 认证与项目隔离覆盖准备、解析、列表、取图和证据；并发重试只发一次请求，繁忙时不发起新的模型调用。
- 解析失败/连接不明不产生可取图结果；任务与成功图原子保存，重启后中断任务置 unknown，同请求不重发。
- C 取图经过生产 Pydantic 和冻结 JSON Schema 校验，旧 T018 报告/原始档案 API 回归保持通过。

## 真实模型证据

命令：`python packages/runtime/tests/run_graph_parser_e2e.py --confirm-external --root OUTSIDE_NEW_RUN_DIR`。脚本拒绝复用已有输出目录，计数 transport 严格限制为一次供应商请求。

实际运行目录位于 checkout 同级 `AiCA-graph-parser-live-20261017T050300Z`，保留合成源档案/事件、传输包、SQLite 和 result.json；该名称是本次事实，不是团队配置。可共享结果逐字节保存为 [graph-parser-live.json](tests/evidence/graph-parser-live.json)。没有提交数据库、凭证或真实用户录制。

- API transport 为进程内 TestClient，模型 transport 为真实 HTTPS；生图/音频执行次数为 0，外部模型调用 1 次，自动重试 0。
- prompt_tokens=7290，completion_tokens=2616，total_tokens=9906。实际模型和 prompt 版本写在图与任务元数据中。
- 16 项事实，预期四类创作步骤均出现；初次角色图失败与修订归入同一步，保留两个 Run；配乐没有被错误建立 verified 执行依赖。
- 语义关系和自然语言归因仍是候选；复核记录的一条 continues 方向和一条混合主体解释问题见 [运行说明](GRAPH_PARSER.md)。0.1.1 明确方向，但没有再次调用模型，不承诺已解决或给出准确率。

模型用量及原结果不因后续提示改进而改写。单样例证明的是链路可运行，不是通用语义正确率。

## 本轮修正与后续

初次静态检查的两处 import 排序已修正。合成测试最初误写保留事件名 session.started，按 A 实际导出的 session.created 修正；真实 Recorder 导出测试通过。A 只读目录快照不含 .git，未将其当作分支；核对实际远端 A 为 `57b02f0`，本任务基线的 A 源码与之无差异。

下一步由 C 使用精确版本的取图/证据接口完成页面联调；编辑/布局服务和平台登录/Fork/多分支需独立阶段实现。没有执行部署、合并、环境升级或系统权限修改。
