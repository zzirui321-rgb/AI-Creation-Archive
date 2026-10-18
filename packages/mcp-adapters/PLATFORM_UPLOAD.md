# MCP 平台上传：使用说明与 A → D 接口契约

A 实现配置、授权、附件传输与拉取；D 维护实际平台 API。测试分别覆盖协议 fixture 与真实平台，最新 PNG 上传、作品列表、图片接口和拉取校验结果见 [T032](../../tasks/T032.json)。真实原生客户端确认界面和平台现场生图保存仍须单独验收。

## 逐轮模式

用户可通过 `aica connect` 的浏览器页面首次授权整个项目，随后自动记录、登记任务产物和上传；详见[项目首次授权](USAGE.md#项目首次授权登录一次后续自动处理)。下述逐轮单次确认规则适用于没有项目授权的工作区。

`--per-turn` 模式每轮独立确认上传，绑定只保存目标，旧的自动上传授权也不会继续触发发送。正常 Stop 导出完成后由 Hook 显示本机上传确认；`aica_sync_platform` 和 `aica_resume_upload` 则通过 MCP 确认当前固定快照。No、取消、确认不可用均不发送。成功返回 `uploaded: true` 与经过校验的回执；出现新记录后状态为 `confirmation_required`。

确认上传包含快照声明的全部已登记附件字节。选择文件时可用实际 `tool_use_id` 关联生成节点和 Run。失败保留已经确认的原包；再次确认后先重试原包，再单独确认新修改。拉取参见 [使用指南](USAGE.md#逐轮录制与完整传输)。

下述后台自动上传流程仅用于显式授权的手动 Session 模式。

## 1. 手动 Session 模式如何使用

先由平台建立一个项目，取得平台项目 UUID 和该账号的访问令牌。访问令牌通过 `AICA_UPLOAD_TOKEN` 环境变量提供给 MCP 进程；也可以在绑定时指定另一个环境变量名。已有 MCP/协调器进程不能读取其他终端后来设置的环境变量，需要在配置好环境后重新连接，必要时停止旧协调器再启动。

如果返回 `coordinator_upgrade_required`，在该创作项目中运行 `aica stop --project .`，等待协调器关闭后重新连接 MCP。停止前会保存本地快照；现有授权仍以录制器实际恢复结果为准，不要把重连当成新的录制授权。

密码和令牌不要发到 Agent 对话、命令参数或工具参数中。`aica connect` 的本机浏览器页面复用 D 的登录、项目列表/创建、上传令牌签发接口，私有保存凭证并自动绑定。以下环境变量流程仍可用于既有手动 Session 接入。

在用户创作目录中，可以对 Agent 说：

> 我已审阅这个项目的现有记录和已登记产物，允许将当前完整档案及此后录制的更新自动上传到平台 `https://platform.example` 的项目 `00000000-0000-4000-8000-000000000001`。令牌已通过 AICA_UPLOAD_TOKEN 提供，请绑定平台并开启自动上传。

Agent 调用 `aica_bind_platform`：

```json
{
  "server_url": "https://platform.example",
  "project_id": "00000000-0000-4000-8000-000000000001",
  "token_env": "AICA_UPLOAD_TOKEN",
  "interval_seconds": 5,
  "confirm_upload": true
}
```

也可在终端运行：

```sh
conda activate aica
aica platform bind --project . --server https://platform.example --project-id 00000000-0000-4000-8000-000000000001 --confirm-upload
aica platform status --project .
aica platform pause --project .
aica platform resume --project . --confirm-upload
aica platform sync --project .
```

绑定成功表示已保存目标和上传授权，随后由本地协调器上传，不能将绑定回执当成远程上传成功。查看 `aica_platform_status` 返回的 `state`、`current_seq`、`last_uploaded_seq` 和 `receipt`：只有验证通过的远程回执才会更新后两项。`synced` 表示当前录制序号已同步，`uploading` 表示后台正在处理，`pending` 表示还有新记录，`retry_wait` 表示退避等待，`blocked` 表示达到重试上限，`paused` 表示暂停。

每次成功后至少等待配置的间隔，再上传最新录制快照；间隔内的更新合并。它是定期上传完整快照，不是逐事件流式传输或增量对象同步。协调器持续运行时，上传不依赖 Agent 再主动调用工具；但创作内容仍须先由 Agent/适配器提交给录制器，不会自动拦截全部客户端操作。

暂停上传不暂停本地录制。恢复会先完成未确认的原包，再发送最新快照。已发出的网络请求无法撤回；暂停后不会开始新的请求。

## 2. `.aicaconfig` 与授权

文件位于用户项目根目录，格式为 JSON：

```json
{
  "version": 1,
  "server_url": "https://platform.example",
  "project_id": "00000000-0000-4000-8000-000000000001",
  "token_env": "AICA_UPLOAD_TOKEN",
  "auto_upload": true,
  "interval_seconds": 5,
  "allow_loopback_http": false
}
```

| 字段 | 含义 |
| --- | --- |
| `version` | 本地配置版本，目前为 1 |
| `server_url` | 平台源地址，不含 API 路径、账号、查询参数或片段 |
| `project_id` | 平台已存在的项目 UUID，不是档案内部的源项目 ID |
| `token_env` | MCP/协调器进程读取的平台令牌环境变量名，配置不存令牌值 |
| `auto_upload` | 是否允许后台开始新的上传请求 |
| `interval_seconds` | 成功上传后的最小发送间隔，1–300 秒，默认 5 秒 |
| `allow_loopback_http` | 仅为本机模拟测试允许 loopback HTTP，不能开启内网或公网 HTTP |

配置结构的唯一来源为共享 `recording.schema.json` 中的 `platform_config`；它是客户端配置契约，不改变 `.aica` 档案格式。

单独复制或编辑 `.aicaconfig` 不会产生上传授权。绑定工具还会在私有状态目录保存与工作区、录制流和配置目标匹配的授权回执。换目录、换录制流或修改目标等绑定字段后，应重新明确确认并绑定。配置不包含密钥，但包含平台地址和项目关联；团队分享前仍应核对这些信息。

私有恢复目录：

```text
.aica/outboxes/platform/<binding-id>/
├── authorization.json   用户确认绑定后的本机授权回执
├── state.json           同步序号、退避、错误与最后的远程回执
└── pending.zip          待确认的固定上传字节；成功后删除
```

这些文件不写进创作档案，也不作为普通附件自动上传。打包只读取 `aica.json` 和其中明确登记、校验通过的产物；不会扫描整个目录上传。新录制器的 `.aica/.gitignore` 默认排除内部状态，不要把新增授权与恢复状态加入 Git。

## 3. D 需要提供的上传接口

复用现有 [传输契约](REMOTE_TRANSFER.md) 和共享 [transfer.schema.json](../schema/src/aica_schema/transfer.schema.json)，不另造一套图片或档案字段。

```text
POST /api/projects/{project_id}/imports
Authorization: Bearer <platform-token>
Content-Type: application/vnd.aica.bundle+zip
Content-Length: <原始 ZIP 字节数>
Idempotency-Key: sha256:<整个 ZIP 的 SHA-256>
X-AiCA-Bundle-SHA256: <整个 ZIP 的 SHA-256>
Accept: application/json
```

请求体是 ZIP 原始二进制，不使用 multipart，也不使用 JSON 包一层 Base64。包内包含原始 `aica.json`、`manifest.json` 和去重的 `objects/<sha256>`。图片不依赖用户电脑路径。

D 应使用共享 `verify_bundle` 校验完整内容及哈希，验证账号对项目的写权限，持久化档案与必要产物，再原子发布快照与回执。平台项目 ID 和档案内部的源项目 ID 分开处理。图片只按登记的内容哈希接收，不读取外部 URL 或客户端绝对路径。

成功响应：首次完整接收返回 HTTP 201，相同账号、项目和幂等键的重试返回 HTTP 200，JSON 回执保持一致：

```json
{
  "status": "success",
  "format": "aica-transfer/0.1",
  "project_id": "00000000-0000-4000-8000-000000000001",
  "import_id": "00000000-0000-4000-8000-000000000002",
  "bundle_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "archive_sha256": "1111111111111111111111111111111111111111111111111111111111111111"
}
```

示例 hash 是占位符，实际回执必须与收到的包一致。不要增加 `url` 等共享回执 Schema 未定义的字段；作品链接可以在后续查询接口中提供。客户端验证格式、包哈希、档案哈希和目标项目 ID，只有全部通过才确认成功。本轮不接受 HTTP 202 作为成功。

错误建议使用 `{status:"error",code,message}`，401 为凭证无效，404 为目标不可访问，409 为幂等冲突，413 为超限，422 为契约错误，503 为存储不可用。客户端不保存或回显未经信任的服务端错误正文。

当前限制：ZIP 总大小 512 MiB，档案元数据 16 MiB，单个产物 64 MiB，最多 4096 个产物。D 不应再把整个 ZIP 当作一个 64 MiB 的产物。所有字节、哈希和产物实例 ID 保持原义。

## 4. 读取与前端对接

建议复用 D 分支的读取接口：

1. `GET /api/projects/{project_id}/archives` 查询导入历史。
2. `GET /api/projects/{project_id}/archive?import_id=...` 读取选定的档案。
3. `GET /api/projects/{project_id}/artifacts/{artifact_id}?import_id=...` 读取该快照中的实际图片/文件。

读取同样使用 Bearer 认证并校验项目权限。前端固定同一 `import_id` 关联档案与图片，通过带认证的 fetch 获取图片字节并创建 Blob URL；不要把令牌写进图片 URL。图片读取成功不应依赖源工作区还存在。

本轮 A 不修改 D 的数据库、登录、仓库/项目映射或 C 的页面。D 需要明确前端作品仓库如何关联平台 `project_id`，并按上述档案和产物接口接线。前端自动刷新、语义分析、公开分享及工作流执行仍由各自模块实施，自动上传不隐含这些操作的授权。

## 5. 中断、并发和重试约定

1. 本地采集先提交成功；后台网络失败不会撤销已录制内容。网络发送不持有录制器互斥锁，打包使用锁内取得的固定快照，随后在锁外读取已登记产物和传输。
2. 每个项目只有其协调器的一个上传工作线程，同一绑定先确认当前待传包，再处理更新；多个 Agent 的录制仍交给同一个项目协调器处理。
3. 上传前固定 `pending.zip` 并持久化身份。超时、断网、丢失回执和进程重启后，必须重传原包及原幂等键，不能重新导出替换未知结果的包。
4. 每次连续失败按 5、10、20、40 秒退避，最多自动尝试 5 次；达到上限后等待用户检查并调用同步或恢复。不会无限重试，也不会自动刷新平台令牌。
5. 新记录会合并到后续快照，期间原有待确认包保持不变。篡改或删除待确认包会报错，不能悄悄重建。应恢复原包后重试；换绑定后旧恢复目录仍保留供核对。
6. 绑定切换时，旧请求可能已发送；旧回执只归旧绑定，不会成为新目标的成功状态。暂停或退出时若请求结果未知，保留原包，下次幂等确认。
7. v0.1 不实现多台机器对同一平台项目的双向合并、分块续传或无限历史保留策略。D 保留不可变导入历史；跨录制流的 head 选择由后端另行明确。

## 6. 本地模拟服务和验收

模拟服务只监听 `127.0.0.1`，用于验证传输契约，不包含账号注册、完整权限、数据库事务、生产存储或前端业务。它的固定测试项目 ID 为 `00000000-0000-4000-8000-000000000001`。

先在两个终端中提供相同的测试 `AICA_UPLOAD_TOKEN`。终端一从仓库根目录启动：

```sh
conda activate aica
python3 packages/mcp-adapters/tests/mock_platform.py --port 8008
```

终端二进入另一个可分享的合成创作项目，录制并登记图片后绑定：

```sh
aica platform bind --project . --server http://127.0.0.1:8008 --project-id 00000000-0000-4000-8000-000000000001 --allow-loopback-http --confirm-upload
aica platform status --project .
```

`--allow-loopback-http` 仅用于这个本机测试。实际平台需要 HTTPS；此前提供的内网 HTTP 地址未在本轮配置或上传。

自动验证沿用清单的 `capture-tests`，专项命令为：

```sh
python3 -m pytest -q packages/mcp-adapters/tests/test_platform.py
```

测试包含原生 stdio MCP 的真实图片登记与后台 HTTP 上传、暂停期间继续记录、恢复后新快照、移走源目录后仍可读取平台图片，以及服务端已提交但回执丢失后的协调器重启/原包重传。HTTPX 模拟测试另外覆盖凭证、绑定复制、目标变更、错误回执、有限退避及损坏恢复。

D 完成接口后，应使用本文件相同的请求、回执和失败场景复验实际后端；不能把模拟服务验收当成真实服务器验收。
