# Codex 逐轮录制接入

当前 Codex 使用 `conversation`：命令 Hook 暂停原请求，用户在下一条聊天消息明确回复 Yes/No 后继续原任务；无倒计时、无默认授权。Yes 的正常 Stop 自动导出 `exports/aica.json`，No 继续任务但不记录。生成配置及协调器升级见 [聊天确认与结束导出](AGENT_SETUP.md#聊天确认暂停恢复结束导出)。以下严格门控章节适用于 `auto` / `browser` 模式。

六类客户端的共用门控、本机确认页后备、配置生成和当前覆盖见 [多客户端接入](AGENT_SETUP.md)。下文 Codex 启动别名与现有 Hook 配置仍适用；原生 UI 报不可用时可转本机页面，原生 No / 关闭不会触发第二次确认。

本分支已实现严格门控、本地 Hook 桥接和 MCP 原生确认请求，共 20 个工具。普通启动命令仍保留手动 Session 模式；必须使用 `--codex-turns` 并接入下列 Hook 才会逐轮询问。当前开发机已合并 Codex 接入配置并保留原有 Hook，尚未修改信任状态或切换当前会话，没有补录本轮开发。其他机器仍需各自配置。

## 1. 用户看到什么

进入项目后提示“已连接、等待每轮确认”。每次提交新请求，通过配置的原生确认界面或本机确认页显示原始请求预览和 Yes/No，默认 No。

1. Yes：保存本轮需求、已知 Agent 模型、支持的本地工具步骤与最终可见回复。
2. No、关闭窗口、超时，或原生和本机页面均不可用：不将本轮内容写入 AiCA。不会创建拒绝 Session、空节点或内容待传包。
3. 结束、中断、恢复会话或连接丢失后，下一轮重新确认。不能把聊天里的“yes”当作确认界面答案。
4. 图片等文件通过 `aica_select_turn_artifacts` 选择，再确认一次才复制到 `.aica/artifacts/`；此处 No 仅拒绝选中的附件，不停止已经同意的本轮录制。不会扫描输出目录或回扫旧聊天。

上传仍由 `.aicaconfig` 的独立授权决定。逐轮 Yes 不等于同意上传；No 不会撤回以前获准的上传包，也不会暂停其他会话。

历史 JSON 导入是独立授权：严格模式下 `aica_import_archive` 另行询问所选文件和新目标目录，不继承本轮录制权限。普通 CLI/RecorderClient 不能只传 `confirm_import=true` 绕过该确认，应使用原生 MCP 导入入口。

## 2. 一次性配置

沿用已经安装的 Conda 环境与本地包，不需要安装新依赖。

先在终端激活 `aica` 环境，生成可审阅配置：

```sh
conda activate aica
aica codex-config > /tmp/aica-hooks.json
```

Windows 请自行选择临时输出路径。生成器使用当前 Python 的绝对位置；换机器或移动环境后重新生成，不能把另一台机器生成的路径作为团队配置。配置中的 MCP 服务名为 `aica`，与当前已有服务名称一致。

将 MCP 服务参数改为：

```text
可执行文件：当前 Conda 环境中的 aica
参数：serve --project . --codex-turns
```

`--project .` 随客户端启动 MCP 时的工作目录确定项目。本版要求 Host 的 `cwd` 与该项目根目录一致，不能通过修改工具参数把另一个项目当成本项目。

审阅生成的 JSON，将其中各事件的 AiCA 条目合并到 Codex 使用的 `hooks.json`，例如 `~/.codex/hooks.json`；已有其他 Hook 必须保留。若已经存在 AiCA 条目，应替换对应条目，不能重复追加。配置包含：

| 事件 | 接入行为 |
| --- | --- |
| SessionStart | 提示接入状态；恢复会话时关闭旧授权，压缩续跑保持本轮状态 |
| UserPromptSubmit | 命令 Hook 通过私有控制通道提交新请求；MCP Hook 触发确认 UI |
| PreToolUse / PostToolUse | 对获准轮次记录本地工具边界、输入输出和节点，不赋予重新执行能力 |
| Stop | 保存最终可见回复并封存本轮 |
| Interrupt / SessionEnd | 关闭本轮，停止接纳新结果；不在回调中等待远程上传 |

`UserPromptSubmit` 的 MCP Hook 输入必须使用 Hook 输出模式：

```json
{"host_session_id":"${session_id}","turn_id":"${turn_id}","hook_output":true,"confirmation":"browser"}
```

该模式的文本与结构化输出只包含宿主接受的 `systemMessage`；AiCA 的 `status`、`recording` 和 `state` 放在 MCP `_meta.aica` 中。普通工具调用不传该选项时保留原有业务结果。将普通 MCP 业务字典直接交给 Hook，会导致 `invalid user prompt submit JSON output`。重新生成并合并旧配置后，需要重新连接 MCP，让运行中的服务加载新代码；变更过的 Hook 仍须在原生界面审阅信任。

当前开发机的 Codex 未展示原生确认框便返回未授权，因此采用 `confirmation: "browser"`。该模式仍由命令 Hook 提交待确认轮次，再由同一 MCP 连接打开本机页面，不依靠多个会话共有的父进程选择连接。没有选择前不会记录，不能把原生客户端未确认当作用户主动选择 No。实际拒绝原因保存在 `_meta.aica.confirmation_reason`，提示区分 No、关闭、超时和页面不可用。

其他机器若同样没有弹框，可生成此配置：

```sh
aica agent-config --client codex --confirmation browser --output /tmp/aica-codex-browser
```

合并生成的 Hook 与 MCP 配置。MCP 的 `tool_timeout_sec` 设为 150，Hook 超时为 130 秒，页面最多等待 120 秒，避免普通工具默认超时提前结束确认。`confirmation: "auto"` 仍保留原生优先路径；原生 No 或取消不会再弹第二个页面。文件选择和历史导入沿用当前 MCP 连接已选定的确认方式。

随后在 Codex `/hooks` 中审阅并信任具体定义，再重新连接 MCP 或新开会话。修改后的 Hook 需要重新审阅。客户端信任是原生要求，不使用绕过信任开关。[OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)

如果提示 `coordinator_upgrade_required`，先结束该项目其他录制会话，运行 `aica stop --project .`，再重新连接 MCP。不要在其他会话仍使用协调器时强行重启。

接入成功与否必须以新会话实际出现的确认界面和后续档案核对为准；仅安装配置、看到 20 个工具或 CLI 开关开启，都不表示原生链路验收完成。

## 3. 内部怎样保证不录

命令 Hook 通过独立的本机控制凭证交付 Host 事件，普通 MCP 工具不持有该凭证。`aica_confirm_turn` 只能确认已存在的待确认请求，不能凭模型传入的会话 ID、`record=true` 或任意文本创建授权。实际决定来自原生 elicitation 响应，或原生不可用时的本机确认页。控制挑战、页面凭证和 Session 令牌不会作为工具结果交给模型。

协调器将授权绑定到项目、Host 会话、轮次、MCP 桥接连接和 Agent。普通 RPC 与手动 Session 写入在严格模式下一律拒绝；即使持有以前的有效 Session 令牌，也不能写入。拒绝发生在持久化队列和产物复制之前。图片采集再次检查当前轮次，以免文件确认期间已经中断。

等待选择的正文只在进程内存中短暂保留；No 后释放，未答复最多保留 300 秒。已结束轮次只保留内存中的定位与状态，不保存 No 的内容摘要或 hash。桥接每两秒续租，超过十秒没有心跳即关闭；断连不继承新内容采集授权。已经落盘的 Yes 历史和授权上传包仍保留。

这些是 AiCA 工具与录制服务的权限边界，不是同一操作系统账户下的恶意程序隔离。能主动读取私有凭证、修改包源码或直接改数据库的本机程序不在此边界内。Codex 自己的聊天历史、模型处理和普通工作文件也不受 AiCA 的 No 控制。

| 内部文件 | 作用 |
| --- | --- |
| `.aica/recording-policy.json` | 共享契约定义的严格模式标记；不包含任何对话内容，不改动 `.aicaconfig` version 1 |
| `.aica/control.json` | 私有、临时的控制接口位置与凭证；协调器退出后删除，不得分享 |
| `.aica/endpoint.json` | 普通本机 RPC 接口；它的凭证不能调用控制接口 |

不要将新的控制凭证加入 Git。标准导出和后台上传仍只包含档案与登记的产物，不包含这些运行文件。

## 4. 已知边界

1. 原生 Codex 桌面 Hook 调用顺序、信任界面及确认 UI 仍需人工端到端验收。自动测试使用真实 stdio MCP 协议和模拟客户端确认，不冒充桌面 UI 测试。
2. Hosted 工具和部分特殊工具路径可能没有 Hook；中间回复未完整采集，只保存 Stop 提供的最终可见回复。自己的 AiCA 工具调用不再作为创作节点录入，避免记录内部凭证或重复采集。
3. 工具节点是观察记录，`execution.kind=manual`，没有可执行绑定。Hook 时刻只代表观察边界，原始执行计时、生图供应商和生图模型缺失时不猜测。Agent 模型单独记录。
4. 有开始、没有结果的步骤在关闭录制时标 `unknown`，表示未观察到执行结局，不表示工具被实际取消。归档 Run 本来就支持该状态，录制事件契约现在也支持。
5. 同一 Host `turn_id` 再次收到用户提交时，无法区分重试和中途转向。本版关闭该轮采集并提示边界不明确，不沿用旧 Yes。正常新轮次使用新 ID 后再询问。
6. 本版不授予子 Agent 继承权限。观察到 `Agent` / `spawn_agent` 时关闭父轮采集，避免共享 Host 标识的子任务混入；继续采集需新的用户轮次。其他隐式子任务仍属原生身份覆盖缺口，需要单独验收。独立 Host 会话的授权互不覆盖。
7. 严格模式开启后不会因换回普通启动参数自动降级。手动采集、日志提取和旧令牌都会被拒绝。若需要恢复手动模式，应先结束所有录制进程，再明确移除本项目的策略标记；不要在录制中途改动。
8. Hook 超时、未信任、服务未就绪或 UI 不支持时，业务可以继续，未确认轮次保持不录。已获准轮次如果后续 Hook 丢失，可能出现采集缺口；不能承诺完整审计。暂存在内存、尚未成功提交的 Yes 事件不会在崩溃后凭旧授权回扫补录。

## 5. 手动验收

在独立测试项目和新 Codex 会话验证，不使用真实私有内容作测试标记。

1. 先选 No，发送唯一标记并让 Agent 创建一份普通输出。正常创作应能继续，AiCA 的数据库、WAL、outbox、导出和上传中均不得出现该标记或自动复制该输出。
2. 下一轮选 Yes，执行一个本地工具并回复。检查消息、节点、Run、Agent 模型和 Harness。调用 `aica_select_turn_artifacts` 选择一张测试图，确认后检查副本及哈希。
3. 再依次执行 No、关闭确认框、Yes，确认授权不继承，也不回扫之前 No 的文件。
4. 测试中断、断开 MCP、恢复旧会话、两个并发会话和中途追加消息。旧 Session 令牌及直接普通 RPC 均不应恢复内容写入。
5. 在单独授权的模拟平台测试上传、暂停与恢复；No 不生成新内容包，Yes 在暂停时仍能本地保存。

自动化检查位于 `tests/test_turns.py`；整体方案与要求见 [录制设计](TURN_RECORDING_DESIGN.md)。
