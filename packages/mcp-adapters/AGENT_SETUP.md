# 六类 Agent 的逐轮录制接入

## 项目首次授权

已有 MCP 和 Host Hook 配置可继续使用。升级代码并重连后，运行 `aica connect --server https://your-platform.example`，或由 Agent 调用 `aica_connect_platform`，在浏览器完成登录和项目授权。之后相同工作区、录制流及平台目标不再逐轮确认；不应额外让用户聊天回复 Yes/No。`aica pause`、`aica resume`、`aica disconnect` 分别暂停、恢复和撤销本机项目授权。首次升级需要重启旧协调器，授权后的凭证更新无需重启。

无项目授权时继续采用下面的客户端确认配置。完整范围及凭证有效期见[使用说明](USAGE.md#项目首次授权登录一次后续自动处理)。


## 聊天确认、暂停恢复、结束导出

Codex 使用 `conversation`。命令 Hook 返回 `UserPromptSubmit` 的 `decision: block`，暂停原需求并结束本次提交，用户可以在底部正常聊天输入框发送下一条消息：

> 本次对话是否使用 AiCA 记录并导出？
>
> Yes：记录本次对话及已采集产物，并导出为 exports/aica.json。
> No：本次对话不记录、不导出。
>
> 原任务已暂停。请在聊天输入框发送单独一条 Yes 或 No；回复后继续原任务，无回复不会自动授权。

1. 下一条单独发送 `Yes` / `No`，大小写与首尾空格均可。认证 Host Hook 将原需求交回模型继续执行，选择消息不作为新的创作需求记录。不阻塞一个 MCP 请求等输入，不设置回复倒计时。
2. Yes：开始记录原需求及后续可关联工具、可见回复。No：继续原任务，但原需求和执行结果不写入 AiCA，不更新已有导出。
3. 无回复始终不授权。等待请求仅保存在协调器内存中；MCP 断连、会话结束或中断使其失效。发送其他新需求取消旧请求，并为新需求重新询问。断连后重新提交原需求，不从历史日志补录。
4. 命令 Hook 按宿主进程链定位唯一 MCP 连接。身份不明、多个连接、服务不可用或版本不兼容时阻止原提交并报告问题，不猜测授权，不打开浏览器。
5. Yes 的正常 Stop 自动生成自包含 `exports/aica.json`。提示包含整个档案的附件数量和图片登记情况。No 保留已有历史和导出。
6. 生成图片等文件先保存在工作目录，再在当前获准轮次结束前调用 `aica_select_turn_artifacts`，成功确认后由 AiCA 建立记录并复制到内容寻址存储。检查 `selected`、`artifacts`、`confirmation_reason`，核对副本及 SHA-256。直接复制到 `.aica/artifacts/` 不构成登记。
7. 文件仍需独立确认。聊天模式不为附件自动授权，原生附件确认能力不可用时回退本机确认页；取消、超时或两种能力均不可用会返回错误与具体原因，不冒充用户选择 No；明确拒绝返回 `selected: false`。附件授权不跨轮继承。已绑定平台时，正常结束导出后另开本机页面确认本轮快照上传；拒绝不发送。

```sh
conda activate aica
aica agent-config --client codex --confirmation conversation --output /tmp/aica-conversation
```

合并生成目录内 `mcp.json` 和 `hooks.json` 的 AiCA 项，保留其他设置及 Hook。MCP 使用 `serve --project . --per-turn --client codex`，不需要 `--default-yes`。UserPromptSubmit 只有一个命令 Hook，不安装等待输入的 MCP Hook；删除之前 AiCA 的重复确认条目。原生默认 Yes 模式是另一种独立选项。

协调器需要 `conversation_recording_version: 2`。先结束项目中仍使用旧协调器的会话，再执行 `aica stop --project .` 并重连 MCP，在 `/hooks` 审阅更新后的定义。不会强制停止其他会话或自动信任 Hook。

测试覆盖命令 Hook 的阻止/恢复、延迟回复、无回复、No 不持久化、断连与会话隔离，以及真实 stdio MCP 的正式附件选择和便携导出。真实客户端的输入框、Hook 信任和模型恢复仍须在新会话验收；这些协议测试不能替代原生验收。[Codex Hooks 输出约定](https://learn.chatgpt.com/docs/hooks)


## 原生对话框、默认 Yes

`native-default-yes` 是可选的 MCP 原生表单模式，与上述普通对话询问分别配置。下面严格确认章节适用于 `auto` / `browser` 模式。

1. 每轮请求通过 MCP 向客户端发起原生 Yes/No，默认项为 Yes；不打开浏览器。
2. 明确选择 No：整轮不写入 AiCA。Yes：记录支持的需求、工具和可见回复。
3. 10 秒未选择、关闭或客户端无法提供原生确认界面：按照已配置的默认 Yes 记录，并说明使用了默认值。MCP 不能强制客户端绘制对话框；无界面时不能声称已显示。
4. 默认值只用于本轮录制。文件选择及历史导入仍需单独明确确认，无法确认则不操作；上传仍受独立绑定权限约束。
5. No 必须在本轮确认期间选择；不支持事后发送聊天文字 No 撤回已保存或上传的内容。

此模式当前提供 Codex、Claude Code 的 MCP Hook 配置；其他四类客户端尚未适配本模式，生成器会明确拒绝，不会偷偷打开浏览器。

在实际测试机器已有的 AiCA 环境中生成配置，不要复制另一台机器的可执行文件路径：

```sh
aica agent-config --client codex --confirmation native-default-yes --output /tmp/aica-native-default-yes
```

将输出中 MCP 和 Hook 的 AiCA 条目合并到本机配置，保留其他设置。MCP 启动参数必须包含 `serve --project . --per-turn --client codex --default-yes`，`UserPromptSubmit` 的 MCP Hook 输入使用 `"confirmation":"native-default-yes"` 及 `"hook_output":true`。缺少启动开关时，工具调用不能自行启用默认 Yes。重新连接 MCP，并在 `/hooks` 审阅变更后的具体定义。

自动化测试覆盖协议往返和记录结果；每台机器仍需验证原生界面是否实际出现。另见 [OpenAI Hooks 文档](https://learn.chatgpt.com/docs/hooks)。

AiCA 由客户端 Hook / 插件在每轮开始时触发确认，不依赖模型主动询问。Codex、Claude Code 优先使用 MCP 原生确认；原生确认报不可用时回退到本机确认页。其余客户端由本机确认页接入。原生选择 No 或关闭不会再打开第二个确认页。

只有 Yes 才建立本轮录制 Session。No、未选择、关闭、超时、客户端身份不明确均不新增本轮内容；不影响正常创作。这里的 No 约束 AiCA 档案，不改变客户端自身的聊天历史。上传权限仍单独由 `.aicaconfig` 管理。

| 客户端标识 | 自动触发 | 本版覆盖 | 确认 |
| --- | --- | --- | --- |
| `codex` | UserPromptSubmit / 本地工具 / Stop | 本轮需求、支持的工具、最终可见回复 | 原生优先，本机页面后备 |
| `claude-code` | UserPromptSubmit / 本地工具 / Stop | 有 `prompt_id` 的本轮需求、工具和最终可见回复 | 原生优先；无 prompt_id 降为需求采集并用本机页面 |
| `cursor` | beforeSubmitPrompt / pre/postToolUse / afterAgentResponse | 单工作区中有 generation_id 的本轮需求、工具和可见回复 | 本机页面 |
| `opencode` | chat.message / message.updated / message.part.updated | 有 user message ID 和 parentID 关联的需求、工具和可见文本 | 本机页面 |
| `gemini-cli` | BeforeAgent / AfterAgent | **仅本次需求**；未假定存在稳定轮次或工具 ID | 本机页面 |
| `github-copilot` | CLI UserPromptSubmitted 或 VS Code UserPromptSubmit | **仅本次需求**；未把无可靠关联的回复和工具挂到最新需求 | 本机页面 |

`local_hooks` 表示可关联的本地事件，不承诺所有 hosted 工具或中间回复都能观察到。`prompt_only` 的缺口会写入获准档案的 Harness 和记录说明。Agent 模型仅按 Host 明确提供的字段记录，不能推测生图模型。所有客户端均不回扫 transcript、不采集 reasoning / thought，不扫描输出目录。文件通过 `aica_select_turn_artifacts` 再次确认；严格模式的历史导入另行确认。

## 生成配置

沿用已安装的 Conda 环境，不安装其他客户端或依赖。在实际使用的机器生成配置，输出目录必须尚不存在：

```sh
conda activate aica
aica agent-config --client claude-code --output /tmp/aica-claude
```

将 `--client` 换成表中的标识即可生成其他客户端配置。Windows 自选临时路径。生成器只写新目录中的 `mcp.json`、`hooks.json`；OpenCode 另外生成 `aica.js`。不修改原生配置、批准 Hook 信任或开启当前项目录制。生成文件使用本机当前 Python 位置，移动环境后重新生成，不提交这些机器配置。

通用 MCP 启动参数：

```sh
aica serve --project . --per-turn --client claude-code
```

`--client` 必须与 Hook / 插件一致。`--project .` 依赖客户端启动服务时的工作目录；应通过 `aica_get_archive` 核对项目。若客户端不以项目根目录启动，将本机配置中的 `--project` 改为实际项目路径。事件 cwd 不匹配会拒绝采集。`--codex-turns` 仍是 `--per-turn` 的别名，原有 Codex 启动配置可继续使用。

| 客户端 | MCP 配置去向 | Hook / 插件去向 |
| --- | --- | --- |
| Codex | 将生成的 `mcp_servers.aica` 合并到 `~/.codex/config.toml`，使用 TOML 格式 | 合并至 `~/.codex/hooks.json`；原生 `/hooks` 审阅并信任 |
| Claude Code | 用 `claude mcp add --transport stdio --scope user aica -- <生成的 command> <args...>`；已有同名服务先核对 | 将 `hooks.json` 的 hooks 合并至 `~/.claude/settings.json` |
| Cursor | 合并至 `~/.cursor/mcp.json` | 合并至 `~/.cursor/hooks.json` |
| Gemini CLI | 合并至 `~/.gemini/settings.json` 的 mcpServers | 将 hooks 合并至同一 settings.json |
| OpenCode | 将 mcp 合并至 `~/.config/opencode/opencode.json` | 将 `aica.js` 放到 `~/.config/opencode/plugins/aica.js`，只保留一份 |
| Copilot CLI | 合并至 `~/.copilot/mcp-config.json` | `~/.copilot/hooks/aica.json`，保留 version 1 |
| VS Code 本地 Copilot | 用下方 `--surface vscode` 生成的 servers 合并至工作区 `.vscode/mcp.json` | 工作区 `.github/hooks/aica.json`，使用该 surface 的 PascalCase 配置 |

VS Code 使用：

```sh
aica agent-config --client github-copilot --surface vscode --output /tmp/aica-copilot-vscode
```

不同 VS Code Agent 提供程序有各自的 Hook 体系：Claude / Codex 扩展采用各自客户端适配，不应把 Copilot 配置复制给它们。当前实现面向本机交互工作区，云端 / SSH 无本机浏览器的环境未接入远程确认。

Codex / Claude 的 `mcp_tool` 确认输入需包含 `hook_output: true`，生成器已提供。这样才会返回宿主专用 JSON；普通 MCP 状态保存在 `_meta.aica`。旧配置应合并这一选项并重连 MCP，不能把普通业务返回值当作 Hook 输出。此修正不增加工具数量，也不更改录制授权条件。

如果原生客户端没有显示确认框就返回 No / 取消，可用 `--confirmation browser` 重新生成配置。对于 Codex / Claude，该选项由当前 MCP 连接直接打开本机页面，不使用父进程猜测连接；其他客户端沿用命令 Hook 本机页面。当前开发机 Codex 已选浏览器方式，其他客户端仍保留各自配置。Codex 的生成配置将普通 MCP 工具超时设为 150 秒，覆盖 120 秒页面等待期。业务结果的 `confirmation_reason` 记录实际返回原因，不把所有失败都显示为用户选择 No。

保留已有服务和 Hook，仅替换 AiCA 对应条目，避免重复订阅。遵守客户端自己的信任审阅，不开启自动批准录制。重新打开客户端或连接 MCP 后验证。若出现 `coordinator_upgrade_required`，在该项目其他录制会话结束后，执行 `aica stop --project .` 再重连；不会自动中断正在使用旧协调器的会话。

## 本机确认页

页面只监听 `127.0.0.1`，展示当前项目和请求预览。打开后最多等待 120 秒。点击 Yes 才确认；No、关闭或超时不记录。浏览器打不开或服务无法核对 Host 进程时，保持不录，不以聊天里的 yes 替代。

页面使用一次性随机片段凭证，不把凭证写入 HTTP URL、页面源码、日志或导出。页面请求检查 Host、Origin 和随机凭证，旧页面不能授权新轮次。待确认正文仅暂存在进程内存，拒绝后释放；原生待确认最多保留 300 秒。断开 MCP 或轮次结束会失效。

浏览器适配还要求 Hook 的父进程链能唯一对应当前客户端的 MCP 连接。多个候选连接、不可查询的进程链、多根工作区都不猜测。出现此情况先检查配置和项目根目录；不能为了完成采集而选择任意连接。

本地同账户进程可以读取本机私有凭证，录制门控不提供操作系统沙箱。禁止分享 `.aica/control.json`、endpoint 和原生配置中的密钥；标准导出不包含它们。

## 验证与限制

自动测试覆盖六类事件适配、真实 stdio MCP、客户端隔离、Yes / No / 超时 / 旧页面、浏览器操作和页面来源限制。真实 MCP 测试使用测试客户端，不等于六个产品的原生 UI 验收。浏览器测试仅复用已有 Playwright / Chromium，由 `AICA_PLAYWRIGHT_MODULE`、`AICA_CHROMIUM_PATH` 指定，不下载安装。

本机已安装 Codex、Claude Code、OpenCode；缺失的 Cursor、Gemini CLI、Copilot 只交付配置与适配测试。各客户端仍需新会话的原生验收，不能把“配置存在”或“20 个工具可见”当作完成录制。

本次开发机已合并 Claude Code 用户级 MCP / Hook 配置及 OpenCode 全局 MCP / 本地插件，保留原有设置。OpenCode 2.0.20 原生配置发现能识别二者，但常驻后台服务仍未刷新；请在其他会话结束后按该版本的 `opencode reload` 更新，再开新会话验收。没有强制重启后台服务。Codex 仍需原生 `/hooks` 审阅，以及在旧协调器使用结束后重连；当前对话未被切换或补录。

1. 在独立测试项目发唯一标记，选 No，正常执行一个工具；数据库、WAL、outbox、导出及上传包均不能出现标记。
2. 下一轮选 Yes，检查需求、实际可观察字段及覆盖缺口；支持完整本地事件的客户端再检查工具节点和可见回复。
3. 选择一份测试文件并再次确认，检查产物副本和哈希。工具节点是观察记录，未获得可执行绑定；未观察到结果标为 unknown。
4. 验证 Yes → No → Yes、关闭窗口、断连、并发会话、旧页面重放和中途追加输入；授权不能继承。不用真实私人内容做测试标记。

Claude 子 Agent 不继承父轮授权；Cursor subagentStart 关闭父轮采集；OpenCode 只接受当前用户 message ID 的明确 parentID。未携带子 Agent 身份或稳定边界的隐式执行仍有覆盖缺口。Gemini / Copilot 暂不自动采集回复与工具，需要后续获得可靠的原生事件关联契约后扩展，不能补猜节点。

## 官方契约依据

按 2026-10-17 查询的官方接口分别适配，事件能力可能随版本变化：

- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude Code Hooks](https://code.claude.com/docs/en/hooks)
- [Cursor Hooks](https://cursor.com/docs/hooks)
- [Gemini CLI Hooks reference](https://geminicli.com/docs/hooks/reference/)
- [OpenCode Plugins](https://opencode.ai/docs/plugins/) 与 [MCP servers](https://opencode.ai/docs/mcp-servers/)
- [Copilot Hooks](https://docs.github.com/en/copilot/reference/hooks-reference) 与 [VS Code Hooks](https://code.visualstudio.com/docs/agents/reference/hooks-reference)
