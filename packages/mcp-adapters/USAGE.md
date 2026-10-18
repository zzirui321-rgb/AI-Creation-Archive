# aica-mcp 中文使用说明

## 项目首次授权：登录一次，后续自动处理

客户端继续使用现有 MCP 与 Host Hook 配置。在实际创作目录运行：

```sh
aica connect --server https://your-platform.example
```

也可以告诉 Agent：“连接这个项目到平台”，由它调用 `aica_connect_platform` 打开本机连接页。浏览器中登录平台账号，选择或新建项目，勾选授权后完成连接。无需复制令牌、设置环境变量或填写项目 UUID；连接本身不需要重启 MCP。首次升级到本功能时，需先停止该项目的旧协调器并用当前代码重连 MCP。

首次授权包含本地已有档案和后续需求、可关联工具调用、可见回复、任务生成或你选定的附件，以及向页面显示的目标自动上传。此授权跨会话保留，后续不再逐轮询问 Yes/No，也不再为这些附件和上传重复弹窗。图片/文件生成工具返回明确的项目内输出路径时会自动登记；其他产物由 Agent 调用 `aica_select_turn_artifacts` 登记，不扫描工作目录。工具输出不含路径、文件在项目外或无法读取时，Agent 仍需将实际输出保存到项目内并登记。

```sh
aica status
aica platform status
aica pause
aica resume
aica disconnect
```

`pause` 同时暂停后续录制和新上传；`resume` 恢复已有项目授权；`disconnect` 删除本机凭证及项目授权，并尝试撤销平台上传令牌。已经发出的请求无法撤回，平台令牌撤销是否成功以 `remote_token_revoked` 为准。这些命令默认使用当前目录，也可指定 `--project`。Agent 可用 `aica_project_access` 查询状态及执行用户要求的暂停、恢复、断开。

登录凭证和上传令牌保存在工作区外的本机私有目录 `~/.aica/credentials`，不进入档案；测试可通过 `AICA_CREDENTIALS_DIR` 指定工作区外的隔离目录。密码不持久化。现有后端没有登录续期接口：凭证过期后运行 `aica connect` 重新登录，无需重新复制配置。历史导入、覆盖当前项目的拉取和公开发布仍单独确认。没有项目授权的工作区继续使用下述逐轮确认流程。

内网 HTTP 开发服务需显式增加 `--allow-loopback-http`，地址校验沿用已有规则；正式平台使用 HTTPS。


模型字段来自实际提交的元数据：手动 `aica_record_step` 需提供 `step.model`，Host Hook 需提供 `model` 或 `agent_model`；没有观察值时保留 `{}`。`client: Codex` 只表示客户端，不能当成模型。界面或配置的当前模型也不等于历史每一步的实际模型，不用于回填旧记录。

Codex 共享后台进程时，聊天确认优先通过本机 app-server 的线程 ID 查询 `aica_host_status`，核对项目和实际 MCP 连接后路由。它只返回连接标识，不授权采集、不发送需求正文。旧式独立进程仍支持唯一父进程匹配；宿主身份不能验证时继续停止采集。此路由使用当前环境已有的 WebSocket 支持，不自动安装依赖。

## 自动双向同步

连接页勾选“启用自动双向同步”，选择已有作品或新建同步作品。平台需提供归档导出和 `expected_head` 版本提交接口；仅上传绑定不会自动升级为双向同步。已有连接需要重新运行 `aica connect` 并确认该选项。

同步每 5 秒检查一次，录制中的任务结束后再同步。平台保存的新版本会备份本地档案与对应文件，再更新本地。只处理已登记的项目内文件，不扫描整个目录；旧附件没有原始路径时只能同步档案。采集事件中的源路径 `source_path` 与不可变附件位置分开保存，源文件修改不会替代附件的哈希校验。

两端同时修改时，暂停同步并保留两端。先查看当前冲突，再明确选择版本：

```sh
aica sync status --project .
aica sync resolve --project . --prefer local --conflict-id <当前冲突ID>
# 或明确选择平台版本：
aica sync resolve --project . --prefer remote --conflict-id <当前冲突ID>
```

版本在选择后再次变化时，会重新暂停要求选择。Agent 可用 `aica_sync_control` 完成同样操作，不能自行决定覆盖哪一端。备份保存在 `.aica/backups/`，包括原档案、数据库、被替换文件和放弃重试的上传包；此目录不提交到 Git。

`aica sync pause` 只暂停双向同步，`aica sync resume` 恢复并重置错误重试；`aica pause` 暂停项目记录与上传，`aica disconnect` 撤销本机项目授权。失败采用有限退避，连续 5 次失败后等待恢复；登录凭证到期需重新连接。状态为 `synced` 且包含平台回执才表示提交已确认。

确认 Hook 遇到接口版本不匹配时，应停止本项目旧协调器并重连当前代码。确认请求允许 5 秒等待本地写盘；通信结果不明时不会自动重放用户需求或补授录制权限。

## 逐轮录制与完整传输

`--per-turn` 模式按每轮独立确认：录制选择 Yes 后记录可观察的需求、回复、工具输入输出、模型和 Harness 信息，正常结束时导出 `exports/aica.json`；选择 No 不写本轮内容。不可观察的模型参数或 Harness 内容保留缺失标记。

1. 图片先保存到项目目录，在本轮结束前调用 `aica_select_turn_artifacts` 并确认文件。可传 `tool_use_id`，关联本轮实际观察到的生成工具、节点和 Run；没有观察证据时不猜测来源。
2. `aica_bind_platform` 在逐轮模式只保存目标。每次正常结束、导出成功后，Hook 通过本机确认页单独询问是否上传；也可调用 `aica_sync_platform` 使用原生确认，能力不可用时回退本机页面。No、取消和超时均不发送。未绑定平台时会报告未上传。
3. 上传的是确认时的完整档案及全部已登记附件字节，不扫描目录。图片、日志、历史定义等均按哈希校验；缺少文件时整个上传准备失败。确认后档案变化必须重新确认。上传失败保留原包，下次确认后重试同一份字节；新修改需要另行上传。
4. `aica_pull_platform` 按 `repo_id` 和固定 `commit_id` 下载归档及附件。平台版本中登记的新增图片也会下载；平台只保存到别处、尚未写入该版本的结果不属于这次快照。
5. 拉取显示本地/远端节点数、附件数，明确确认后备份本地数据库、快照和配置，再更新当前项目。文件写到 `.aica/artifacts/`，逐一核验哈希；拒绝、下载损坏或确认期间本地变化均不覆盖。拉取后重新确认录制和上传。

“全量”指该快照声明的创作数据和附件；不包括未选择文件、本机凭证、未授权轮次或不可见的模型内部信息。可见性、上传回执、附件字节完整性分别验收。

拉取参数示例（先通过 `aica_get_archive` 取得当前快照 ID；项目须已配置相同平台源地址）：

```json
{
  "server_url": "https://platform.example",
  "repo_id": "00000000-0000-4000-8000-000000000001",
  "commit_id": "00000000-0000-4000-8000-000000000002",
  "request_id": "pull-review-001",
  "expected_snapshot_id": "从当前档案读取",
  "token_env": "AICA_DOWNLOAD_TOKEN"
}
```

上传使用平台签发的项目上传令牌；拉取使用有仓库读取权限的登录令牌。令牌由协调器环境提供。启用新接口后重新连接 MCP；旧协调器会明确报告需要更新。需重新生成/审阅对应客户端 Hook 配置，Stop 配置包含导出和上传确认的等待时间。

适用于当前 v0.1 开发版，共 21 个 MCP 工具，包含逐轮确认、JSON 导入、平台上传和覆盖拉取。命令和工具参数以当前分支实现为准。

AiCA 帮你把与 AI 的创作过程、提示词、工具与模型信息、图片等产物整理成一份创作档案。你可以把档案上传平台查看，也可以下载后恢复为本地项目，交给 Agent 接着创作。

## 1. 从哪里开始

| 你现在要做什么                     | 阅读位置                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------ |
| 第一次使用，确认环境和项目位置     | [准备工作](#2-准备工作)                                                  |
| 让 Agent 记录这次创作              | [记录创作](#3-让-agent-记录这次创作)                                     |
| 在六类 Agent 每轮开始前选择 Yes/No | [多客户端逐轮接入](AGENT_SETUP.md)                                       |
| 确认图片和创作步骤都已保存         | [检查记录](#4-确认记录和图片已经归档)                                    |
| 上传完整项目到平台                 | [导出与上传](#5-导出并上传平台)                                          |
| 绑定平台并确认上传         | [.aicaconfig 与平台上传](PLATFORM_UPLOAD.md)                             |
| 把平台下载的 JSON 交给本地 Agent   | [导入与继续创作](#6-把平台档案导入本地-agent)                            |
| 查找命令、工具和报错               | [速查](#8-命令速查)、[工具表](#9-mcp-工具速查)、[常见问题](#10-常见问题) |

常见流程：

```text
在项目目录中打开 Agent，并连接 aica-mcp
→ 授权记录这次 Session
→ Agent 完成创作步骤，提交步骤信息和选定产物
→ 检查节点和附件
→ 导出自包含 exports/aica.json
→ 在平台导入，查看或下载
→ 恢复到本地新子目录
→ 在新目录打开 Agent，读取档案并继续创作
```

## 2. 准备工作

本手册假设团队的 Conda `aica` 环境和本地包已经安装。首次安装、客户端接入见 [技术 README](README.md#安装) 和 [团队环境说明](../../docs/SETUP.md)。本手册中的示例不会安装或升级依赖。

1. 在你实际创作的项目目录打开终端和 Agent。例如画漫画时，打开漫画项目目录。
2. 激活环境，并确认命令可用：

   ```sh
   conda activate aica
   aica --help
   aica import --help
   ```

3. 确认 Agent 的 aica-mcp 已连接到这个项目。让 Agent 查看 `aica_get_archive`，核对项目标题和已有节点。
4. 更新过本地 MCP 包后，重新连接 MCP 或重新打开会话，以刷新工具列表。当前应能看到 `aica_record_step` 和 `aica_import_archive`。

MCP 服务的启动参数形如：

```sh
aica serve --project /absolute/path/to/your-project
```

通常由 Agent 客户端启动该 stdio 服务。已经接入时无需再手动启动一个。实际工作目录由 `--project` 决定；在终端切换目录，不会改变已经运行的 MCP 连接。

连接时可能先生成根目录 `aica.json`、`.aica/` 和录制数据库。这表示项目已初始化；实际内容采集由后续授权和提交决定。

## 3. 让 Agent 记录这次创作

逐轮模式见 [多客户端接入说明](AGENT_SETUP.md)：配置 Hook / 插件、审阅信任后，以 `--per-turn --client <客户端标识>` 启动 MCP。优先原生确认，缺少能力时打开本机确认页；No 或未选择不进入 AiCA。图片另行选择并确认。Gemini / Copilot 本版仅自动采集需求，各客户端原生验收仍需完成。

以下话术和 `aica_start_session`、`aica_record_step` 流程适用于显式手动模式，不能用于绕过逐轮门控。

### 可以直接发给 Agent

> 这次创作允许使用 aica-mcp 记录。请开启一个新的录制 Session，记录你能实际观察到的需求、创作步骤、工具调用和选定产物。每个完成的工具或模型步骤使用 aica_record_step，关联本项目中的输出文件。只记录能确认的模型、参数、时间和依赖，缺失信息明确标注。导出、上传和执行其他命令分别按我的指令处理。

Agent 应先调用 `aica_start_session`，再提交已授权的内容。Session 凭证由调用端管理，不写进公开档案或提示词。

### 一张生成图片怎样进入档案

假设 Agent 把图片保存到 `output/cover.png`。完整记录通常包含：

1. 这一步做了什么，例如“生成漫画封面”。
2. 实际使用的提示词、可观察参数、工具、生图模型，以及执行状态。
3. 执行这一步的 Agent 模型和可获取的 Harness 信息。
4. 明确选中的文件 `output/cover.png`，以及它与步骤、Run 的关联。

其中，Agent 模型负责对话和规划，生图模型负责生成图片；Harness 指 Agent 使用的客户端、工具和工作配置。能观察到的信息分别记录，无法确认的部分保留缺项。

Agent 提交 `aica_record_step` 后，录制器将选定文件复制到 `.aica/artifacts/<sha256>`，并建立节点、Run 和产物引用。原图仍可以留在输出目录。

如果只想保存一份已有图片，可以使用 `aica_capture_artifact`；它负责登记文件，创作步骤的建立需要另行提交。图片的像素或文件名不能证明它由哪个模型生成，模型信息来自实际观察或有证据的记录。

### 当前采集方式

当前版本由 Agent 或适配器主动调用工具提交观察内容。MCP 连接本身不会自动监听客户端的所有对话、原生工具或输出目录。手动操作和未提交的步骤，需要后续明确补录或从授权文件中提取。

手动模式按 Session 管理授权。严格逐轮模式由本地桥接、MCP 原生确认与录制服务共同门控；需先按 [Codex 接入说明](CODEX_TURN_SETUP.md) 安装、信任并验证 Hook。两种模式都应定期按下一节检查实际结果，不能将“已连接”理解为所有内容都已完整记录。

## 4. 确认记录和图片已经归档

可以直接发给 Agent：

> 请检查当前 AiCA 档案，列出创作节点数、Run 数、图片和附件数，以及缺失信息。分页核验附件的本地副本与哈希，确认刚才的图片关联到了哪一步。只报告实际检查结果。

对应工具：

| 要确认的事情           | 使用工具                                 | 怎样判断                                              |
| ---------------------- | ---------------------------------------- | ----------------------------------------------------- |
| 当前记录属于哪个项目   | `aica_get_archive`                       | 标题和已有内容符合当前项目                            |
| 步骤是否已提交         | `aica_get_items`，`collection=nodes`     | 能找到对应节点                                        |
| 是否记录了实际执行结果 | `aica_get_items`，`collection=runs`      | 能找到对应 Run 及状态；事后提取的步骤可能没有原始 Run |
| 图片是否已登记         | `aica_get_items`，`collection=artifacts` | 能找到图片记录、哈希及产生关联                        |
| 文件是否可以完整读取   | `aica_check_references`                  | 相关产物的本次检查状态为 `verified`                   |

这些分页接口每页最多 200 项。数据多时应继续翻页；后续页带上第一页返回的快照 ID。`all_references_verified=null` 表示本次响应没有覆盖全部引用，需检查剩余页；若返回 `false`，应查看具体缺失或损坏项。

`snapshot.complete` 反映已提交事件的缺口情况。对未观察或未提交的创作步骤，仍需依据实际记录判断完整性。

## 5. 导出并上传平台

在创作项目目录执行：

```sh
aica export --project . --portable --output exports/aica.json
```

`--project .` 指当前终端所在的项目。输出目录会自动创建。`--portable` 把已登记的必要产物字节嵌入 JSON，便于单文件传递。

也可以告诉 Agent：

> 请检查已登记附件是否齐全，然后通过 aica_export_archive 导出自包含 JSON 到 exports/aica.json，只在本地保存。

工具：`aica_export_archive`，参数：

```json
{ "portable": true, "output": "exports/aica.json" }
```

导出成功后，在平台进入“发布创作／导入项目”，选择 **`exports/aica.json`**，并选择新建项目或已有项目。具体按钮名称取决于当前前端。

文件限制：整个自包含 JSON 最大 128 MiB，元数据最大 16 MiB，单个附件最大 64 MiB。嵌入的图片等二进制内容通过 Base64 编码，因此 JSON 比附件总大小更大。

导出会保存当时的快照。手动上传模式下，后续创作完成后需要重新导出、上传；逐轮模式每次单独确认上传；手动 Session 模式可显式启用 [平台自动上传](PLATFORM_UPLOAD.md)。上传需单独确认内容和目标，录制授权不会自动授予上传权限。

### 两个 aica.json 的区别

| 文件                   | 用途                                         | 图片怎样保存                         |
| ---------------------- | -------------------------------------------- | ------------------------------------ |
| 项目根目录 `aica.json` | 本地录制器维护的当前快照                     | 通常引用 `.aica/artifacts/` 中的文件 |
| `exports/aica.json`    | 显式导出的自包含快照，可交给平台或另一台电脑 | 导出时嵌入已登记产物的字节           |

录制器运行期间，由它维护根目录快照。需补录或改定义时，使用对应接口，避免直接覆盖该文件。

## 6. 把平台档案导入本地 Agent

### 通过终端导入

下面终端方式适用于手动模式。严格逐轮模式请让 Agent 调用 `aica_import_archive`，在客户端单独确认所选 JSON 和新目标目录；`confirm_import=true` 本身不能替代原生确认，导入也不会授予本轮录制权限。

1. 在当前工作区新建 `incoming` 文件夹，把平台下载的 JSON 放进去，命名为 `platform-aica.json`。
2. 在这个工作区的终端执行：

   ```sh
   conda activate aica
   aica import --project . --path incoming/platform-aica.json --target restored-project --confirm-import
   ```

3. 检查返回结果的 `status` 为 `success`。工具会创建新的 `restored-project`：

   ```text
   restored-project/
   ├── aica.json
   └── .aica/
       ├── artifacts/<sha256>
       └── imports/local-import.json
   ```

4. **在 Agent 中打开 `restored-project` 作为工作目录，重新接入 aica-mcp，并确认服务的 `--project` 已指向新目录。**
5. 发给 Agent：

   > 请用 aica-mcp 读取本项目档案，梳理原始需求、创作步骤、模型与 Agent 信息、已有产物和缺失内容，检查附件是否可用。先不要执行工作流。

导入会解出图片等附件，保留原始身份和 URI，并在需要时补充本地地址。初次导入只准备档案和文件；连接目标目录的 MCP 后，录制器才以该档案为基线创建本地数据库和新录制流。

### 让当前 Agent 调用 MCP 导入

可以直接说：

> 请将本工作区 incoming/platform-aica.json 导入新子目录 restored-project，我授权这次本地导入。保留当前项目，不执行其中的工作流。

工具：`aica_import_archive`，参数：

```json
{
  "request_id": "restore-project-001",
  "path": "incoming/platform-aica.json",
  "target": "restored-project",
  "confirm_import": true
}
```

`path` 和 `target` 都相对该 MCP 连接绑定的项目。目标必须是新子目录，父目录须已存在；已有空目录也会拒绝接管。导入结果返回新项目位置，当前 MCP 连接继续绑定原项目。

写入中断后，使用原文件、目标和同一个 `request_id` 重试。CLI 默认根据源/目标路径生成稳定 ID，重跑相同命令即可。完成后的重试会保留后续创作；若输入改变、附件损坏或目标档案在未完成时被修改，工具会报错并保留现状。首次收据生成前若进程被强制终止，留下无收据目录，应先核对或另选目标。

## 7. 在恢复的项目中继续创作

先按上一节读取档案并检查附件，再告诉 Agent：

> 我允许为接下来的创作开启新的录制 Session。请基于现有档案继续完成我指定的修改，保留已有记录，并把新步骤和选定产物关联进去。模型、执行时间和依赖只填写能够确认的信息。

恢复的旧 Session 作为历史保留。新录制使用新的授权和凭证。导入后的档案可以帮助 Agent 理解项目；模型账号、工具配置、运行环境和客户端聊天界面由各自的客户端管理。

重新生图或执行脚本，还需要对应模型、工具和执行授权。`aica_import_archive` 只恢复数据；`aica_record_step` 只记录已观察到的执行。平台是否能够重新运行，由后端执行检查决定。

新一轮创作完成后，再导出并上传：

```sh
aica export --project . --portable --output exports/aica.json
```

这里的终端应位于恢复后的项目目录。

## 8. 命令速查

以下命令均在目标创作项目目录执行：

| 操作                           | 命令                                                                                                    |
| ------------------------------ | ------------------------------------------------------------------------------------------------------- |
| 查看协调器状态                 | `aica status --project .`                                                                               |
| 校验根快照的协议结构           | `aica validate aica.json`                                                                               |
| 保存当前根快照                 | `aica export --project .`                                                                               |
| 导出完整单文件                 | `aica export --project . --portable --output exports/aica.json`                                         |
| 手动模式从平台 JSON 恢复新项目 | `aica import --project . --path incoming/platform-aica.json --target restored-project --confirm-import` |
| 刷新快照并停止共享协调器       | `aica stop --project .`                                                                                 |

`status` 在协调器未运行时可能自动启动它，因此也可能初始化本地文件。`validate` 用于协议校验，文件字节是否完整可通过 MCP 的 `aica_check_references` 检查。停止协调器前应确认没有其他客户端仍在使用它。

需要录制一条由你明确选择的本地命令时，可参考：

```sh
aica run --project . --record yes -- python3 -c "print('hello AiCA')"
```

这条示例会实际运行 Python 并记录命令、输出和执行结果。`--record no` 仍会执行命令，只是不保存该命令的内容。其他输出文件需要另行显式登记，完整多产物步骤可由 `aica_record_step` 记录。

已完成的旧创作可以从指定日志或步骤证据文件提取，详见 [技术 README](README.md#从制作文件恢复步骤)。提取的内容标记为事后提取或推断，原始信息缺失时保留缺项。

## 9. MCP 工具速查

| 工具                         | 通俗说明                                          |
| ---------------------------- | ------------------------------------------------- |
| `aica_confirm_turn`          | 在客户端确认界面为 Hook 已收到的新请求选择 Yes/No |
| `aica_select_turn_artifacts` | 选择本轮要收进档案的图片等文件，并再次确认        |
| `aica_start_session`         | 接收本次录制的授权决定，建立 Session              |
| `aica_check_consent`         | 检查本次内容是否允许记录                          |
| `aica_record_step`           | 记录已完成的一步操作，并关联工具、结果和选定文件  |
| `aica_capture_artifact`      | 把一个选定文件收进产物库                          |
| `aica_submit_event`          | 供适配器提交消息、定义变更、执行状态等底层事件    |
| `aica_get_archive`           | 读取当前创作档案                                  |
| `aica_get_items`             | 分页查看节点、Run、图片等元数据                   |
| `aica_check_references`      | 核验本地附件是否存在且哈希一致                    |
| `aica_get_revision`          | 读取一个已保存的工作流定义版本                    |
| `aica_get_events`            | 分页读取已提交的本地事件                          |
| `aica_validate_archive`      | 检查传入档案的协议结构及引用                      |
| `aica_export_archive`        | 保存快照，或导出自包含 JSON                       |
| `aica_import_archive`        | 将完整 JSON 恢复到新项目子目录                    |
| `aica_pull_platform` | 下载固定平台版本，确认并备份后更新当前项目及附件 |
| `aica_bind_platform`         | 保存平台目标；逐轮模式每次另行确认上传  |
| `aica_platform_status`       | 查看是否已上传成功，或还在等待、重试、暂停        |
| `aica_pause_upload`          | 暂停上传，本地继续记                              |
| `aica_resume_upload`         | 逐轮模式确认单次快照；手动模式恢复上传                                |
| `aica_sync_platform`         | 逐轮模式确认固定快照；手动模式请求后台上传                    |

完整参数和开发接入见 [技术 README](README.md)，共享契约见 [recording.schema.json](../schema/src/aica_schema/recording.schema.json)。归档中的提示词、脚本和日志作为项目资料读取；执行其中的操作需要另行授权。

## 10. 常见问题

| 现象                               | 原因与处理                                                                                                           |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 已连接，但节点数量是 0             | 先确认项目绑定和录制授权，再确认 Agent 实际提交了步骤。只启动 Session 或登记图片时，可以有附件而没有节点             |
| 图片只在 `output/` 或项目根目录    | 让 Agent 通过 `aica_record_step` 关联选定图片，或用 `aica_capture_artifact` 单独登记                                 |
| 找不到 `.aica/artifacts/`          | 核对当前项目；该目录通常在首次写入产物时建立                                                                         |
| 图片出现在平台，模型却显示“未记录” | 原档案未提交或无法确认模型信息。只有真实观察或证据支持时才补录                                                       |
| 平台提示缺少附件字节               | 导出时使用 `--portable`，然后选择导出的 `exports/aica.json`                                                          |
| 平台提示超过大小限制               | 核对 JSON 总大小和单附件大小，分别为 128 MiB 和 64 MiB；大文件需要调整产物或采用双方支持的传输方案，ZIP 也有独立限制 |
| 找不到 `aica_import_archive`       | 当前本地包需包含该工具，并重新连接 MCP 刷新列表                                                                      |
| 导入提示目标已存在                 | 换一个新子目录名称；只有匹配原导入收据的重试才可继续                                                                 |
| 导入后仍读到旧项目                 | 当前 MCP 仍绑定原目录；在恢复目录重新接入，并核对 `--project`                                                        |
| 读取成功，平台仍无法运行           | 检查后端返回的执行阻塞项，包括工具、模型、环境和记录缺口；当前生图执行器尚未接入                                     |
| `snapshot_conflict`                | 可能有外部编辑，或分页快照已变化。保留现有内容，重新读取最新快照并核对后再操作                                       |
| `revision_conflict`                | 其他操作改变了定义，重新读取修订；不要用旧修订覆盖新内容                                                             |
| `id_conflict`                      | 同一请求 ID 对应了不同内容。网络重试使用原 ID 和相同参数；真正的新操作使用新 ID                                      |

`.aica/recording.sqlite3` 保存本地事件、授权状态和恢复信息，`outboxes/` 保存待确认请求，`artifacts/` 保存已采集内容。日常平台交付使用经过确认的自包含 JSON；完整数据库备份、Git 评估快照与公开分享分别处理。
