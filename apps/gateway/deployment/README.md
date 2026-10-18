## 0.5.0 模块提示词、逐页内容和原图

新解析在同一授权模型请求中整理模块/每页提示词，可输出明确标记的AI扩展建议。
原文由程序保留，AI不能将建议冒充原始记录；不自动调用模型重新生成图片。
共享模块内逐页展示摘要、原始提示词、整理/建议与已上传的图片，独立原始页节点也可单列模块。
大JSON材料先提取完整页面目录/原提示词，再按模型预算提供有界预览；原文不会因模型输出预算被截断。
主Graph格式保持0.1并兼容旧图，解析策略版本0.5.0；旧的审阅输入需重新准备。

GET /api/repos/{repo_id}/modules/{node_id}/materials?commit_id={commit_id}
沿用该仓库及固定提交权限，提供提示词、逐页内容和图片可用状态。旧Graph可从已保存材料补充原始内容，
不发起模型请求、不改写历史提交。需要新的AI整合摘要/扩展建议时，由用户审阅并发起新提炼。
图片通过既有受鉴权、校验哈希的产物接口获取，前端用Blob展示并在卸载时释放；不读取客户端路径或抓外链。
仅上传含图片路径的普通JSON时，图片无法凭空显示，应上传完整ZIP或含内嵌产物的便携JSON。

## AI提炼自动上库更新

用户确认的解析成功现在由后端自动保存到其私有仓库，关闭页面也会继续保存。
同一平台项目的后续解析追加版本，同一解析ID幂等；服务启动补存此前已成功的用户解析。
若既有库已公开，后续自动保存创建新的私有库，不自动公开新结果。
上传/MCP同步仍不自动触发付费AI，执行产物回写与此语义Graph保存是不同流程。
前端失败时可重试保存，POST /api/projects/{project_id}/semantic-parses/{parse_id}/repository
只补存已有结果，不再次请求模型；GET解析状态包含publication.repo_id/commit_id。

# 本机局域网部署与 C 前端联调

本阶段由用户直接授权：在本机部署前后端、接通功能，并只对局域网开放端口。C 下载快照复制到本 checkout 的 `apps/web` 后接线，下载目录原件未改。原有 D 平台阶段的未提交差异全部保留；本阶段分支 `feature/d-local-lan-integration`，不提交、推送或合并。

## 访问与操作

- 本机：`http://127.0.0.1:8000/`。
- 本次核对的局域网入口：`http://10.19.51.6:8000/`。
- 前端和 `/api` 同源，由同一个 Uvicorn 服务提供；客户端使用当前页面 origin，另一台电脑不会误连自身 localhost。
- 点击“登录”→“没有账号，创建账号”建立自己的平台账号；用户名 3–32 位小写字母/数字/连字符，密码至少 12 位。不要使用旧演示账号；刷新后需重新登录。
- 上传前端可读取的 `.json` / `.aica`，新建私有库；保存写入不可变 Graph 版本；发布后可被其他用户查看、Fork。分支栏可新建与切换分支，切换前先保存编辑。
- 收藏和模型设置仍是浏览器本地偏好；未实现服务端收藏/OAuth。生成预览仍沿用 C 的模拟/可配置 B 网关路径，本阶段未配置或调用真实生成服务。JSON 不会自动上传本机图片；正式 ZIP/附件仍可通过既有 SDK/API 导入。

本次创建一个专用联调测试账号和两份明确标记的合成 Graph 仓库（公开原库、私有 Fork），不是用户默认账号，不包含真实个人内容或模型产物。测试密码、令牌不写入仓库或部署说明。

## 服务与数据

沿用 Conda `aica`。首次复制前端后使用其原始 npm lockfile 执行 `npm ci --no-audit --no-fund`，没有修改锁文件或升级运行时；后续从 `apps/web` 运行 `npm run build`。

仓库根目录启动/停止：

```powershell
& .\apps\gateway\deployment\Start-AiCA.ps1
& .\apps\gateway\deployment\Stop-AiCA.ps1
```

启动脚本自动定位 checkout、既有 aica Python 和构建目录，后台运行 `uvicorn aica_gateway.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1`。只配置子进程环境，不保存全局环境变量。不会开多个 worker，端口已占用则拒绝启动。停止时校验 PID、解释器路径和创建时间，避免停止其他进程。日志按时间分开保存。

```text
%LOCALAPPDATA%/AiCA/lan-deployment/
├── service.json                   PID、时间、路径与端口，无凭证
├── gateway-<时间>.stdout.log
├── gateway-<时间>.stderr.log
└── data/
    ├── platform.sqlite3
    ├── gateway.lock
    ├── snapshots/<import-id>/<sha256>
    ├── uploads/
    └── staging/
```

本次运行数据位于 checkout 外的 `%LOCALAPPDATA%/AiCA/lan-deployment/data`。`service.json` 记录当前 PID；不把一次运行的 PID 固定到脚本。后台进程持续运行；电脑重启后需重新运行启动脚本，未添加开机任务或系统服务。

## 数据库结构

实测 SQLite **3.54.1**，单机文件型关系数据库，开启外键、FULL 同步，写操作用显式事务。Graph 结构按版本存 JSON，数据库管理版本关系和权限。

| 表 | 内容 |
| --- | --- |
| users | 账号 UUID、用户名、显示名、随机盐和 scrypt 密码哈希 |
| sessions / auth_limits | 随机会话摘要、用户、过期时间；认证尝试限流 |
| repositories | 仓库、所有者、公开/私有、Fork 来源 |
| repo_commits | 不可变 Graph JSON、SHA-256、父版本、作者、可选档案快照 |
| repo_branches | 仓库内分支名与 head 版本指针 |
| projects / imports / receipts | 原 A 档案项目、完整档案 BLOB、附件清单及幂等上传回执 |
| semantic_analyses / semantic_graph_parses / semantic_graph_branches | 既有语义分析/解析状态和 Graph 结果 |

大附件不塞进 Graph JSON，按哈希保存在 snapshots；原 URI 保留溯源。备份时先停本服务，再一并复制整个 data 目录；不能只拷贝数据库而漏掉附件。当前结构适合本机 MVP；多机生产部署属于后续独立阶段。

## 局域网规则

用户明确选择局域网访问，随后再次明确要求弹出管理员授权窗口。首次 UAC 取消，未创建规则；第二次用户允许后创建并实测核对：

```text
Name: AiCA-LAN-TCP-8000
Direction: Inbound; Action: Allow; Enabled: True
Protocol: TCP; LocalPort: 8000
LocalAddress: 10.19.51.6
RemoteAddress: 10.19.48.0/21
Profile: Private; EdgeTraversal: Block
```

没有关闭防火墙、修改网络类别、端口转发或公网隧道。服务器仅监听 IPv4。IP/网段变化后需要重新核对规则，不能机械复用旧地址。若需撤销本规则，在管理员 PowerShell 执行 `Remove-NetFirewallRule -Name 'AiCA-LAN-TCP-8000'`。

## 验证与边界

浏览器实际通过：真实平台登录、JSON 导入/建库、三节点两依赖 Graph、创建 experiment/lan、发布与 Fork 成独立私有库。匿名新页面在服务重启后读取公开 Graph 和分支；节点详情弹窗 open 状态已核对。前端 SDK 浏览器 fetch 的 Illegal invocation 已修复（绑定 globalThis），并保留 SDK 测试。

前端 22 测试与 TypeScript/Vite 构建通过；原 Runtime/Gateway 183 回归通过，静态部署新增 2 测试通过；SDK 3 项通过。真实服务停启后公开库 commit ID 与文档 SHA-256 均不变；后台服务恢复健康。本机回环和本机 LAN 地址的首页/health/公开库都返回 200；防火墙规则已核对，但没有第二台电脑的实际访问证据。

浏览器在跨用户回复时丢失临时 tab 引用，按文档重新创建 localhost tab 后恢复；直接以浏览器数值 ID 绑定 LAN tab 曾超时，局域网 HTTP 检查使用无代理的本机客户端独立完成，不将它冒充跨机器验证。截图保存在本机 deliverables 目录；不包含测试密码或令牌。

完整命令、改动、已知 A 模块两项 Windows 符号链接权限失败和下一步见 `docs/STATUS.md`。本阶段到此停止，不继续执行、PR/Merge 或生产数据库迁移。
# 2026-10-17 更新：B 装配、MCP 和 HTTPS

当前源基线为 bd_First_Edition_16.29@9e293f2；A 的共享协议与 MCP 源码同步自
feature/a-protocol-mcp@9fae685。B 的 c-provider-settings.patch 已装配到平台前端；
执行 Runtime 仍按交接固定为 test@6b98aa4 加 A@a0cc735 及两份 D 补丁，隔离于新解析器。
不将旧 Runtime 包覆盖新 Graph 解析器。

使用既有 aica 环境。分别在 apps/web、packages/artifact-renderers 执行
`npm ci --no-audit --no-fund`，使用已交付锁文件，不升级版本；apps/web 中执行 `npm run build`。
在仓库根目录准备隔离 Runtime（首次或补丁更新时）：

```powershell
conda run --no-capture-output -n aica python apps/gateway/src/preview/scripts/prepare_runtime.py --destination "$env:LOCALAPPDATA/AiCA/lan-deployment/execution-sources-v1"
./apps/gateway/deployment/Stop-AiCA.ps1
./apps/gateway/deployment/Start-AiCA.ps1
./apps/gateway/deployment/Start-AiCA-HTTPS.ps1 -LocalAddress <本机局域网IPv4>
```

HTTP 8000 保留，HTTPS 8443 通过本机 TLS 代理访问同一服务和数据库。
HTTPS 的 PID 和证书位于部署目录的 https-service.json 和 tls/；可用
Stop-AiCA-HTTPS.ps1 独立停止。只在用户批准后，以管理员身份运行
Enable-AiCA-LAN.ps1，指定 Port=8443、已核验 LocalAddress 和 RemoteSubnet，保持 Private。
证书不自动安装到任何电脑的根信任库。不要关闭 TLS 校验。

本次机器地址为 https://10.19.51.6:8443，HTTP 网站仍为 http://10.19.51.6:8000。
允许的远端范围为 10.19.48.0/21。CA 下载入口为 `/api/platform/ca-certificate`。
本次 CA PEM 文件 SHA-256：
`2013f86120c2f5978b4158a02c884dd60f61ba6263f1634101a4f91608db47e7`。
在可信渠道核对后，浏览器用户自行将 CA 导入信任库；证书当前包含此 IP、localhost、127.0.0.1。
地址变化时应重新签发证书并复核防火墙范围，不能沿用错误的 SAN。

平台“导入”页的“MCP 自动上传与已接收档案”可创建项目、生成仅此项目上传的
30 天令牌、刷新不可变快照并进入 AI 输入审阅。令牌仅显示一次，不存浏览器持久化；
数据库只存摘要。签发与撤销接口分别为 POST /api/mcp/upload-tokens、
DELETE /api/mcp/upload-tokens/{id}。上传令牌不能登录、读库、提炼或上传别的项目。

本分支给 A 客户端增加了可选 `AICA_UPLOAD_CA_FILE`，用于显式信任这个局域网 CA；
使用本分支的 MCP 代码或将该小改动交接给 A，原 A 分支尚未包含此扩展。
在启动 MCP 的同一环境设置 `AICA_UPLOAD_TOKEN` 和 `AICA_UPLOAD_CA_FILE` 后重连 MCP；
不要把令牌发给 Agent、放在命令参数、写进 .aicaconfig 或 Git。
绑定沿用 A 工具 aica_bind_platform，server_url 为 HTTPS 源地址，project_id 为平台 UUID，
confirm_upload=true 必须来自用户对当前档案和后续更新的明确授权。

POST /api/projects/{id}/imports 接受原始 ZIP，以及带登记内嵌附件的 portable JSON；
两者校验原始字节哈希和幂等键，分别返回共享 aica-transfer/0.1 或 aica-json/0.1 回执。
JSON 原始上传入口 /archive-json 也保留，可只上传元数据；缺失附件如实标记。
网站不再设置 5MiB/16MiB JSON 上传/文档总大小限制；A 的 ZIP 512MiB、单产物64MiB、
portable 内嵌对象及元数据校验仍按 A 契约执行。JSON 仍需满足结构、模块与字段约束。
上传不授权模型调用；选择快照并审阅、勾选后才调用 AI。

解析器 0.4.0 增强通用职责/独立替换边界，保持证据与候选身份。
较大文字/JSON附件在校验完整字节哈希后按预算发送首、中、尾摘录，明确标记不完整；
不因单附件超过文本预算就完全丢弃正文。不改原始档案。模型输入仍有128KiB/400事实预算，
超大事实集合应在执行工作台选择节点范围，不宣称全部材料都进入模型。
没有进行付费模型质量对比，真实效果需要用户用其材料确认。

`/runtime-api` 由平台会话认证，按用户启动独立 loopback 执行 Worker、独立数据和内部令牌。
Worker 私密令牌不发给浏览器；鉴权图片在组件中转换临时 Blob，仓库保存稳定产物路径。
此为初版进程隔离实现，不是生产弹性调度；执行结果不会自动合并回社区仓库。
HTTPS 是完整工作台使用浏览器 WebCrypto 的推荐入口。


## integration_MVP：MCP 原档入库与读取

MVP从定档分支bd_First_Edition_16.29的0663e1e开始。保留现有后端，优先MCP和前端集成。
后续有明确来源分支的合并采用对方实现优先，再验证后端接口；本阶段没有合并无关录制模块。

`POST /api/projects/{project_id}/imports` 在同一SQLite事务中保存导入、原回执、项目到作品映射和原档版本。
返回201/200时，注册账号的归档已关联私有作品；失败返回非成功状态并回滚，重试使用原包及原幂等键。
不能在共享MCP回执中添加repository字段（现行Schema禁止额外字段）。关联状态独立读取：

- `GET /api/projects/{project_id}/repository`：当前项目head对应的repository `{id,commit_id,state:"ready",url}`；历史未登记或空项目为null。
- `POST /api/projects/{project_id}/repository`：账号登录后幂等补登记最新有效head，复用附件，不触发AI。
- `GET /api/projects/{project_id}/archives`、`archive?import_id=...`、`graph?import_id=...`、`artifacts/{id}?import_id=...`：账号或绑定此项目的MCP令牌读取。
- `GET /api/repos/{repo_id}/archive-info?commit_id=...`：当前版本源归档与附件目录。
- `GET /api/repos/{repo_id}/archive?commit_id=...`：下载原始aica.json，字节哈希在X-AiCA-SHA256；附件使用既有仓库artifacts接口读取。

项目令牌不能列账号作品、访问其他项目或修改作品；读取图片/下载同样使用Bearer请求头，不把令牌拼入URL。
复用历史名称parsed_project_repositories作为唯一项目映射；import_publications使用唯一import_id与commit_id保护幂等。
重复请求不会移动head；迟到处理以项目当前head为准。后续可选AI解析复用同一卡片，旧快照解析不覆盖新导入。
新同步/新解析把关联作品置为private，防止新原档意外公开；需要公开时由用户明确发布。
Web不依赖AI提炼，直接展示原始Graph，并支持原档下载和附件分页预览/下载。“我的创造”可见时每20秒刷新，进入/聚焦也刷新。

部署前用SQLite backup保存本机数据恢复点，沿用既有启动脚本。测试使用合成传输包，不触发外部模型。
目标项目最终验收必须使用数据库登记的所属账号登录，确认卡片、Graph和附件，不能用另一账号或数据库记录代替页面验收。


## integration_MVP 前端v2适配（2026-10-18）

C 前端不再需要独立 aica_graph 或8766端口。沿用现有Gateway8000和HTTPS8443；先在已有环境 `npm run build`，再使用原Stop/Start脚本更新。数据库升级自动增加frontend_jobs表；重启前备份SQLite并确认没有运行任务。

`/api/creation-parser/imports`及jobs/status/result/artifacts提供账号认证的原始记录导入；`/previews`提供图片/选区编辑及千问文生视频任务，账户权限复用平台。供应商Key从body单次传入，Authorization保留平台令牌，Key不进任务表。任务结果保存为账号目录中的哈希产物，确认后作品保存新版本，后台完成的预览可重新登录后恢复历史。关闭等待不等于供应商取消；重启中断不自动重试收费请求。

MCP继续原POST /api/projects/{id}/imports和原严格回执，项目令牌上传/拉取不变，自动私有入库沿用已有事务。新增接口不扩展MCP回执，不触发AI提炼。

验证使用Mock供应商；真实生成视觉质量未收费验收。千问视频依据官方文档：https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference 。非千问视频需配置团队网关；不支持静默忽略参考图后调用文生视频。


## A新版完整拉取（2026-10-18）

来源 feature/a-protocol-mcp@4b1884b。21个工具；逐轮模式绑定只保存目标，每轮单独确认上传，拒绝/取消不发送；平台后端不绕过本机确认。

新版aica_pull_platform请求固定repo_id/commit_id，使用仓库读取权限的登录令牌（不是项目上传令牌）。GET /api/repos/{repo_id}/archive?commit_id=... 在Accept包含application/vnd.aica.bundle+zip时返回完整ZIP；JSON浏览器原档下载保持不变。包采用共享传输Schema/全部哈希验证，含已确认且保存到该commit的生成结果、原附件与历史定义；不包含只生成而未确认保存的结果。前端原始归档区也提供完整包下载按钮。

客户端必须重新连接更新的MCP；旧协调器提示coordinator_upgrade_required时按A文档先保存/停止再重连。平台不自动修改个人Host Hook配置，不自动录制或上传。拉取覆盖本地由A原生确认并先备份，不在服务端代替用户批准。地址继续沿用现有HTTP8000/HTTPS8443；建议使用可信本机CA的HTTPS入口，显式内网HTTP仅按A客户端选项开启。

新生成结果保存prompt/model和真实产物；复杂UI/语义关系保留在aica.platform-workspace扩展，不宣称已变成可直接执行的Agent工作流。缺源归档的纯UI新建作品目前不支持MCP完整拉取，返回明确not_found，不伪造原始项目身份。
