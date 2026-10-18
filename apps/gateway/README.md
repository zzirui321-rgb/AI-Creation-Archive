# D 第一阶段：档案导入与读取

新增 Graph 仓库平台 MVP：注册/登录/注销、公开/私有库、JSON/档案上库、版本历史、分支与 Fork，见 [PLATFORM_API.md](PLATFORM_API.md)。交给 C 的浏览器客户端在 `integration/platform-client.mjs`，含 TypeScript 声明与真实本机 HTTP 往返测试；现有 C 页面尚未切换。以下为保留的历史阶段说明。

新版 Graph 解析、异步状态、持久化取图和证据接口已在 `feature/d-graph-parser` 接入，供 C 使用的调用流程见 [GRAPH_PARSER.md](../../packages/runtime/GRAPH_PARSER.md)。其返回既定 GraphDocument，不替换本文的 A 原始 `/graph`；图编辑/布局、登录/Fork/分支平台能力及公网部署仍未完成。

后续语义分析已由 T018 在本分支增加：配置 DeepSeek、事实审阅、带证据的步骤/依赖/变量建议及分析状态 API，见 [语义分析说明](../../packages/runtime/README.md)。下文保留第一阶段接口说明；Runtime 执行与自动写回仍未实现。

任务 T017，基线为 A 的 `57b02f0`，独立分支 `feature/d-platform-import`。本阶段只实现远程档案基础：项目权限、传输包接收、持久化、Graph 查询和产物读取。Runtime、Fork、缓存、Golden Path、执行调度和 B 的预览留给后续阶段。

## 环境与启动

使用根目录 `environment.yml` 和 `requirements.txt` 中的既定 Conda `aica` 环境。第三方依赖已经列在该配置中，不另选版本。从仓库根目录安装本地包：

```sh
conda activate aica
python -m pip install --no-build-isolation --no-deps -e packages/schema -e packages/mcp-adapters -e packages/runtime -e apps/gateway
```

Windows PowerShell 配置示例：

```powershell
$env:AICA_DATA_DIR = Join-Path $env:LOCALAPPDATA 'AiCA/platform-import'
# 每次首次配置生成随机令牌，并保存到自己的凭证管理器。
$platformToken = python -c 'import secrets; print(secrets.token_urlsafe(32))'
$env:AICA_API_TOKENS = @{ developer = $platformToken } | ConvertTo-Json -Compress
aica-gateway --host 127.0.0.1 --port 8000
```

`AICA_API_TOKENS` 是“平台主体 → 独立随机令牌”的 JSON，每个令牌为 32–512 个可见 ASCII 字符，不得重复。令牌属于开发认证，不是生产账号或 OAuth 系统，不复用 A 录制器凭证。重启时提供相同主体和令牌才能继续访问已有项目；更换令牌不会更换主体身份。

数据目录必须位于本 checkout 之外。默认只有 `/health` 和开发 OpenAPI 文档可匿名访问；所有 `/api/` 数据接口都需要 `Authorization: Bearer <token>`。一个主体仅能访问自己创建的项目，对其他主体的项目返回 404。

服务只运行一个 worker，同一数据目录只能启动一个实例。外部 HTTPS 入口由部署环境提供；本阶段没有修改系统网络配置、开放公网端口或部署服务。可通过 `AICA_CORS_ORIGINS='["http://localhost:5173"]'` 指定浏览器来源；默认不启用跨域访问。

## 与 A 对接

1. `POST /api/projects`，JSON 为 `{"title":"作品名称"}`，保存返回的 `project.id`。
2. 在源项目中按 A 的说明采集产物，并运行 `aica bundle --project . --output ../creation.zip --offline`。活动录制项目省略 `--offline`，先导出一致快照。
3. 审阅档案与附件，经目标和内容授权后，将平台令牌放入 `AICA_UPLOAD_TOKEN`，运行：

```sh
aica upload ../creation.zip --url https://YOUR_HOST/api/projects/PROJECT_ID/imports --confirm-upload
```

传输格式、请求头和成功回执直接使用 A 的 [共享契约](../../packages/mcp-adapters/REMOTE_TRANSFER.md)。POST 正文为 ZIP 原始字节；服务端复用 `aica_mcp.transfer.verify_bundle`。首次完整持久化返回 201，同主体、同目标项目、同包重试返回 200 和完全相同的回执。回执中的 `import_id` 同时标识不可变平台快照。

档案原始 UTF-8 字节、源 `metadata.project_id`、产物实例 ID、URI 和哈希均保留。平台项目 ID 与源项目 ID 分开管理；源身份不会授予权限。相同产物 ID 可在不同导入快照中指向不同内容，查询时通过 `import_id` 选择对应快照。

## C 可消费的 API

| 方法 / 路径 | 返回 |
| --- | --- |
| `GET /health` | 服务存活状态 |
| `POST /api/projects` | 当前主体的新平台项目 |
| `GET /api/projects?offset=0&limit=50` | 自己的项目，limit 为 1–200 |
| `POST /api/projects/{p}/imports` | A 契约的导入回执 |
| `GET /api/projects/{p}/archives` | 导入历史，包括 id、时间和原始哈希 |
| `GET /api/projects/{p}/archive?import_id=...` | 档案、import_id 和 archive_sha256 |
| `GET /api/projects/{p}/graph?import_id=...` | graph、variables、interactive_questions、artifacts、runs、snapshot |
| `GET /api/projects/{p}/artifacts/{a}?import_id=...` | 对应快照中的产物原始字节 |

省略 `import_id` 时读取最新快照。历史列表也支持 offset/limit；需要一致关联 Graph 和图片时，先保存 Graph 返回的 `import_id`，后续图片请求携带同一个值。offset 分页不提供并发视图冻结。

浏览器使用带认证头的 fetch 下载图片后转为 Blob URL，或通过可信同源代理读取；不要把令牌写进图片 URL。PNG/JPEG/WebP/GIF/AVIF 可 inline 展示，其余 MIME 按附件下载；产物响应携带 SHA-256、nosniff、sandbox 和 private/no-store。服务不使用源电脑路径读取图片，也不请求档案外部 URL。

错误形态为 `status:error`、`code`、`message`。401 为缺失或错误令牌；404 为不可访问或不存在；409 为身份冲突；413 为超限；422 为请求/传输契约不合法；503 为存储失败、损坏或附件缺失。输入校验不回显提交内容。

## 存储与失败语义

```text
AICA_DATA_DIR/
├── platform.sqlite3        项目、原始档案 BLOB、清单和幂等回执
├── snapshots/<import-id>/<sha256>   已发布快照的产物内容
├── staging/                完整校验后的私有待发布对象
├── uploads/                请求私有临时文件
└── gateway.lock            单实例锁
```

服务先校验请求和全部包内对象，再同步暂存文件，原子移动快照目录，最后通过一个数据库事务发布档案、项目 head 和回执。数据库提交前读取者看不到候选快照。可捕获失败会回滚并清理临时内容；进程在文件移动和数据库提交之间崩溃可能留下无数据库引用的对象目录，它不会被 API 暴露，本阶段不自动清理这些目录。

SQLite 使用 FULL 同步和显式写事务。Windows 不提供本实现所用的目录 fsync，不能据此宣称硬件断电下绝对无损。备份需要同时覆盖数据库和对应快照文件；不要手工删除仍在使用的 SQLite 侧车文件。

## 验证与下一阶段

在既定环境中从仓库根目录运行：

```sh
python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests
python -m pytest -q -c apps/gateway/pytest.ini packages/schema/tests packages/mcp-adapters/tests apps/gateway/tests
python -m ruff check apps/gateway
python -m pip check
python scripts/validate_collaboration.py
git diff --check
```

测试包括 A 客户端往返、真实本机 TLS、独立客户端读取、源目录移除后图片仍可用、并发幂等、事务故障回滚、历史隔离、项目隔离、服务重启和损坏检查。真实 TLS 测试只显式信任临时测试证书，不关闭证书验证。实际运行结果以 [STATUS](../../docs/STATUS.md) 为准；本机回环测试不代表双机或公网验收。

下一阶段先与团队冻结平台 API 和存储边界，再单独实施 D 的 Fork、Dirty、缓存和受控执行；B/C 可以使用本阶段读取接口联调。

## 平台版本交给 Agent 继续创作

`GET /api/repos/{repo_id}/handoff?commit_id={commit_id}&format=aica` 返回自包含 `workflow.aica`（标准 AiCA 0.1 JSON，含附件）；`format=bundle` 返回完整传输 ZIP。沿用作品版本读取权限，固定提交，不移动分支或触发模型。纯 UI 新建作品也支持此接口。

工作台的“导出 Agent 工作流 .aica”和“完整接手包 ZIP”会先保存当前编辑，再导出刚保存的提交。图片较多、超过共享协议单文件兼容预算时改用 ZIP。原来的 `/archive` 默认 JSON 仍下载原档；其 ZIP 内容协商及已有 `aica_pull_platform` 现在获得同样的接手资料，无需改 MCP 上传回执或拉取参数。

导出 Graph 包含当前节点、提示词、模型、参数、明确依赖和新增/删除节点；旧定义留在历史修订中，历史 Run 不会伪装成新目标已执行。语义/候选边保留在工作流说明，不当作执行依赖；删掉输入边会列出待解决输入。UI 独有且无执行适配器的模块标记 `agent_configuration_required`，不虚构可直接运行的命令。非协议格式的 UI 节点 ID 稳定映射为协议 ID，原 ID 保留在节点扩展中。

从 `aica.json.extensions["aica.agent-handoff"]` 可取得三份已登记附件的 ID：

- `instructions_artifact_id`：人类和 Agent 可读的接手说明。
- `workflow_artifact_id`：`aica-agent-workflow/0.1`，含当前目标、`recompute/rebuild/reuse/skip` 计划、Agent/Harness、图片及其生成请求、原图和局部选区。
- `operations_artifact_id`：`aica-platform-operations/0.1`，由固定提交祖先链重建的已保存差异，含真实保存作者、时间、提示词/模型/参数、图关系、封面及保存的预览历史。保存时间不冒充点击或逐键输入时间。

图片仅收录固定提交中引用的、经过归属及哈希验证的已保存产物；保存的未确认预览标记 `unconfirmed`，不作为已确认结果。未保存的后台生成不自动进入旧版本。新图片同时保留用户修改要求与网关解析后的生成输入；旧任务未保存的有效生成输入明确标记缺失。Key 不入导出文件。未上传的外链或客户端文件引用列为缺项，不尝试下载或猜测恢复。

单文件可通过已有 `aica_import_archive` 恢复到新的工作区子目录；ZIP 可由已有 `aica_pull_platform` 拉取固定仓库/提交。恢复后按附件 ID 查询 `artifacts`，优先使用 `locations` 中的 `.aica/artifacts/<sha256>`；ZIP 内的便携路径为 `objects/<sha256>`。导入后的 Agent 应先阅读三份接手资料和图片，再根据用户新任务继续。此阶段不增加自动双向同步、执行授权或录制授权。
