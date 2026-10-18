# 修复创作导入页缺少MCP项目令牌入口（2026-10-18）

- 任务 D-MCP-PANEL-WIRING-20261018，分支 feat/D/mcp-upload-panel-wiring-20261018，基线 integration_MVP@320c1ae。截图页面路由为 `#/new`；McpUploadPanel 只被未挂载的 SemanticImportPanel 引用，故用户看不到“创建 MCP 上传项目”。
- 改动：ImportPage 现在展示 MCP 项目/令牌面板；访客显示登录入口，成功后返回当前导入页；令牌管理场景隐藏 AI 快照提炼操作，保留现有项目 API 和令牌 API。
- 命令：`npm run build`（apps/web，成功）；备份SQLite至 `%LOCALAPPDATA%/AiCA/lan-deployment/backups/before-mcp-token-panel-20261018-102747.sqlite3`；Stop/Start AiCA Gateway 部署；HTTP/HTTPS资产与健康检查。
- 文件：apps/web/src/App.tsx、ImportPage.tsx、McpUploadPanel.tsx、任务记录及本状态文件。未更改后端接口、MCP协议或数据库结构；未生成/访问令牌。
- 验证：构建成功；实际服务返回新的 ImportPage bundle，包含MCP面板、登录提示和“创建 MCP 上传项目”文案；HTTP 8000 与 HTTPS 8443 `/health` 均200，HTTPS 仍使用本机CA。未运行测试套件。
- 部署：Gateway PID 4136，HTTPS代理PID 38564；项目无运行中的解析/预览任务；数据备份完成。未改变防火墙或证书信任。
- 下一步：用户刷新 `http://10.19.51.6:8000/#/new`。当前截图显示访客，登录后展开“MCP 自动上传与已接收档案”，即可创建平台项目并生成令牌。实现留在任务分支，未提交或合并 integration_MVP。
# 本机平台停机恢复（2026-10-18）

- 用户反馈平台不可访问。检查发现部署状态文件记录的 Gateway PID 23776 与 HTTPS 代理 PID 42248 均已退出，8000/8443 无监听；SQLite 只读查询确认无运行中的解析或前端预览任务。
- 最近 Gateway 日志仅有启动记录及一次客户端连接重置 WinError 10054，没有说明服务进程退出原因；不能据此断定为应用崩溃。
- 命令：运行现有 `Start-AiCA.ps1` 与 `Start-AiCA-HTTPS.ps1 -LocalAddress 10.19.51.6`；通过 HTTP 及使用本机 CA 验证的 HTTPS 请求检查 `/health` 与首页。
- 文件：仅本机部署状态/日志更新；本记录更新。当前 Gateway PID 39932，HTTPS 代理 PID 38564；8000 和 8443 均监听 `0.0.0.0`，两协议健康及首页均返回 200。防火墙和证书信任设置未改。
- 验证：HTTP `http://127.0.0.1:8000/health` 200；HTTPS `https://10.19.51.6:8443/health` 使用本机 CA 验证为 200；首页两端均 200，响应长度 637 字节。
- 阻塞/下一步：停机根因目前无法从保留日志确定。服务已恢复；如再次停止，需结合系统关机/睡眠记录和当时新日志定位。未运行测试套件。
# 本机安装 integration_MVP MCP 供平台联调（2026-10-18）

- 分支 integration_MVP；按用户请求安装 AiCA MCP 到本机 Codex。复用既有 aica Conda 环境和 HTTPS CA，不更改全局 Python/依赖版本，也不信任安装未知证书。
- 命令：`conda run --no-capture-output -n aica python -m pip install --no-build-isolation --no-deps -e packages/schema -e packages/mcp-adapters`；`conda run --no-capture-output -n aica python -m aica_mcp --help`；使用 Python tomllib 校验 Codex 配置。
- 配置：用户级 `%USERPROFILE%/.codex/config.toml` 新增 `mcp_servers.aica`，启动当前 integration_MVP 可编辑安装，`--project .` 绑定客户端当前项目；MCP 上传使用已存在的 `AICA_UPLOAD_CA_FILE` 本机CA路径。没有写入上传令牌、拉取令牌或密码。
- 文件：本机 Codex 配置新增 MCP 项；本状态记录更新。仓库源码未改，依赖未升级，未启动录制协调器、未上传或拉取任何作品。
- 验证：安装成功，CLI 可加载；MCP TOML 可解析且启动参数正确。未运行用户数据操作或测试套件。
- 阻塞：须重启 Codex 加载新配置；真实平台上传/拉取仍需在目标工作目录绑定项目，并由用户在本机安全设置项目上传令牌及拉取登录令牌。当前 MCP 的下载 TLS 使用系统证书库，若系统未信任本机CA，完整拉取需先完成信任配置或补充 MCP 下载CA支持。
- 下一步：重启 Codex，在包含待联调档案的项目打开会话，检查 `aica_*` 工具后绑定测试项目；单独执行上传与固定版本拉取验收。
# 本阶段：integration_MVP 合入A新版逐轮确认与完整归档拉取（2026-10-18）

- 用户授权按 feature/a-protocol-mcp 最新文档开发合并，沿用D角色；任务 D-MCP-V2-INTEGRATION-20261018，独立任务分支 feat/D/mcp-v2-integration-20261018，集成目标 integration_MVP。来源4b1884b，逐轮上传确认/完整拉取优先采用A代码；不修改其他阶段业务。
- 已阅读USAGE、PLATFORM_UPLOAD、BACKEND_HANDOFF及pulling实现。合入21工具、逐轮绑定只存目标/每轮确认、完整附件上传、固定repo/commit拉取与本地备份恢复。沿用现有环境，不安装依赖；来源pyproject仅补包资源声明。
- D补齐 /api/repos/{id}/archive 的Accept ZIP协商：使用共享build_bundle/verify_bundle构建固定版本完整包，保留源附件及原始哈希，加入已确认并保存的C生成结果和旧版图片编辑结果；未知来源或缺失附件拒绝整包，不静默遗漏。临时包发送后清理。
- 未确认或未保存的预览不导出；账号/仓库版本权限沿用；Fork仅允许引用实际祖先仓库的已登记结果；固定ZIP元数据保证同commit重复下载hash稳定。当前保存的UI记录作为扩展保留，明确生成节点附带prompt/model及真实图片，不编造执行Run或端口依赖。旧定义以历史附件保留，新版本使用独立revision/snapshot身份。
- 原上传POST、严格JSON回执、项目令牌和浏览器原始JSON下载保持兼容。Web添加下载完整版本包入口，MCP通过Accept自动得到ZIP。未扩展AI提炼/付费生成。
- 验证：共享Schema及A拉取/全量同步专项76 passed；Gateway原MCP升级/发布6 passed；Gateway新前端/旧图片编辑导出8 passed；ruff gateway/schema/mcp通过；前端build通过。新增集成覆盖A真实download_archive调用TestClient Gateway、生成图片字节、历史源图、权限和旧版本隔离。
- A全套采集测试在本机Windows运行：257 passed、58 failed、2 skipped（406s），不能宣称全套通过。已定位到Windows字节锁使隐私扫描读取锁文件PermissionError、无符号链接权限、原生客户端测试关闭协调器时连接已退出等平台兼容项；不通过关闭锁/升权或改变生产授权机制掩盖。专项上传/拉取链路与本阶段后端验证独立通过，原生Host交互仍需A现场验收。
- 部署前无运行中解析/预览任务，SQLite已备份before-mcp-v2-20261018.sqlite3；既有Stop/Start部署PID23776，HTTP8000/可信CA HTTPS8443健康200且首页匹配最终dist。
- 所属账号真实页面下载AiCA-comic-2完整ZIP，140节点/319附件/319内容对象，共享verify_bundle全部校验通过；A原版download_archive使用仅本地Mock传输读取这份真实部署下载文件成功（未把私有档案发到外部）。ZIP SHA-256 dca0d693564f7016a94f9588d95babb808a17b14f64c20683b029914e11e796c。新增测试还验证同版本ZIP稳定hash与Fork继承。
- 实现合并检查点34bd3c033b625e8893bd52ebb6a594291355e917，第二父提交4b1884b，来源优先保留A实现。页面截图保存在会话产物mcp-v2-complete-download.png，原档包保留本机Downloads，不进Git；正常注销验收账号。任务in_review，需A现场检查原生Host和Windows全套兼容项，未宣称真实原生确认或付费模型验收。下一步按用户授权合入并推送integration_MVP，本阶段结束。

# 本阶段：integration_MVP C前端v2整合与必要后端替代（2026-10-18）

- 任务 D-MVP-FRONTEND-V2-INTEGRATION-20261018；合并来源 feat/frontend-v2@978a513，以 C 界面/Graph/结果编辑代码为基础，保留当前 Gateway/MCP 权威接口。用户明确授权自行替代缺失 aica_graph。
- 新增 Gateway frontend_v2.py：认证导入、原档及附件映射、持久化预览任务、局部选区编辑、千问异步文生视频与 Range。图片复用 B 供应商实现，选区以原图合成，未选像素保持。任务按账号隔离、支持请求幂等；Key 不写任务表，失败不自动收费重试。未合并或更改 B/MCP 协议实现。
- C 前端接入真实注册/登录、账号作品、保存/Fork/分支、MCP 原档附件、带认证的图片读取；导入回执复用已创建作品，避免第二张卡片。新增预览历史可刷新恢复，确认后保存作品新版本；源语义图和原档映射保留。
- 替代导入只转换明确原始记录，不触发 AI；无产物映射的档案显示来源记录及附件，不推测结果节点。保留 C 外部网关模式；本机选区编辑与千问文生视频已实现，非千问视频需外部网关。
- 命令/验证：npm test -- --run（51 passed）；npm run build（通过）；pytest test_frontend_v2（3 passed，含图像保护像素/权限/幂等/视频Range）；test_import_publication+test_mcp_upgrade 与新增图片/导入专项共8 passed；test_module_pages+test_graph_autosave（6 passed）；ruff check apps/gateway（通过）。无真实收费模型调用，未新增环境/依赖或改锁文件。
- 部署前确认没有 queued/running 解析，备份到本机 backups/before-frontend-v2-20261018.sqlite3；原 Stop/Start 脚本重启，Gateway PID19800。未改防火墙、HTTPS代理或网络。
- 浏览器真实账号 tianxh 已登录新 C 界面，AiCA-comic-2 唯一原作卡片正常；数据已由使用者后续同步为140条来源/319附件（不是此前137/306旧快照），打开成功，仍未伪造图片或依赖。附件实际JSON内容和下载入口已打开；含图片的既有机器人作品显示1个结果节点，原图1254×1254实际加载，节点详情及矩形/圆形/椭圆/手绘选区入口可用。未修改用户作品或点击收费生成。
- 风险/限制：模型实际视觉质量待用户使用自己的供应商验看；前端导入非 MCP 事务链，进程异常中断可能需人工清理未完成卡片；重启不自动恢复供应商轮询以避免重复付费。无阻塞。
- 合并检查点 e3066c7f3f27b4f40fc69cd85facf340d3a83330（第二父提交为C来源978a513）；HTTP8000与可信CA校验HTTPS8443健康200，首页字节匹配最终dist。协作结构/空白/新增内容凭据模式检查通过。页面截图保存在本机会话产物frontend-v2-mcp-archive.png及frontend-v2-local-edit.png，不提交私有归档截图。账号认证沿用原后端内存Bearer策略，刷新后需重新登录，作品和预览任务仍在服务端。
- 下一步：用户刷新重新登录后使用实际模型验看质量；本阶段不扩展AI提炼或Agent工作流。任务in_review，整合阶段结束。

# 本阶段：integration_MVP MCP上传与原档案读取（2026-10-18）

- 任务D-MVP-MCP-PUBLICATION-20261018；用户将bd_First_Edition_16.29定档在0663e1e，从此创建integration_MVP。保留后端，MVP优先前端与MCP，后续实际合并以来源实现优先；本阶段不扩展AI提炼。
- 已fetch最新A分支feature/a-protocol-mcp，交接快照d6c0cae；当前传输回执additionalProperties=false，上传响应字段保持原样；独立GET/POST /api/projects/{project_id}/repository查询/幂等补登记。未进行无关录制模块整包合并或环境/依赖变更。
- 导入事务内同步调用作品发布：同一数据库BEGIN IMMEDIATE锁内更新imports/receipts/项目head、项目仓库映射、版本及作品head；失败整体回滚，ZIP落盘清理沿用原机制。复用历史名parsed_project_repositories的唯一project_id/repo_id映射，并新增import_publications唯一import_id/commit_id；不创建第二套映射。
- 首次导入私有作品，后续导入更新同一卡片与main历史；回执重试不产生版本；迟到关联以项目最新head为准；后续可选AI解析复用同一作品，旧导入的迟到解析不覆盖最新导入head。关联作品若曾公开，新同步/新解析恢复为私有，防止新数据自动公开。
- 原始Graph版本只去掉内嵌base64对象，源原字节/哈希/附件仍保留在imports及快照。作品API提供原档下载和附件目录；Web显示原始Graph、附件分页/文本或图片预览/下载；浏览器内带认证fetch，不在URL写令牌；我的创造进入/聚焦及可见时每20秒拉取更新。
- MCP项目令牌新增仅绑定项目的GET archives/archive/graph/artifacts/repository读取权限；不能列账号作品、读取其他项目或写作品。原有账号与令牌归属校验保留。
- 新专项覆盖首次上传、原档/附件读取、回执Schema、重复/后续/迟到同步、并发首次上传、旧数据补登记、数据库失败回滚和项目令牌只读边界。专项与既有模块/解析9 passed in11.78s；平台20 passed in18.40s。旧平台测试仍期待6MiB非法正文413，与既有取消5MiB限制后的422不符，修正该陈旧断言。Ruff通过。
- 目标案例核对：项目1f1ecd3d-4145-4c76-be9a-c0410c6a9901标题MCP联调测试；当前最新head为fb729167-ec7b-4c53-b7ad-c2f305d2479f，归档标题AiCA-comic-2，137节点/306附件（JSON及NDJSON，无图片）。真实归属账号tianxh，用户另提供的shu无法访问此私有项目；未篡改归属或将数据转到shu。
- 构建完成：TS/Vite及workflow 6 tests通过；MCP升级专项3 passed in3.80s；协作结构/空白检查通过。既有Stop/Start脚本部署，网关PID42504，HTTP8000/HTTPS8443健康200且首页匹配新dist。部署前无运行中解析，已在本机部署目录backups保存SQLite恢复点。
- 目标补登记已实际执行两次，结果一致：私有作品c0048597-9d7d-4eba-83de-226a3094b913，原始版本aafaa2aa-da22-498f-a4c8-4b0f6c70cd5f，关联最新导入fb729167-ec7b-4c53-b7ad-c2f305d2479f。只有1版本，306附件全部读取/哈希验证通过，未重新上传。未认证读取作品原档404。
- 最终页面验收（2026-10-18）已完成：用户提供所属账号后，经正常登录进入tianxh的“我的创造”，实际看到AiCA-comic-2唯一目标卡片（另一个作品为既有库）；打开作品原始Graph和306附件目录，选中JSON附件显示其实际内容及下载入口。没有创建测试作品、伪造会话或改项目归属。
- 从页面点击“下载原始aica.json”得到真实下载文件，解析为AiCA-comic-2/137节点/306附件；SHA-256为6bacdc97f77c8cbb972e1dadf5eee3db38b5897fc2621dab13341da923e21698，与数据库保存源归档完全一致。页面验收使用本机loopback访问同一部署，避开先前LAN浏览器标签超时；LAN HTTP/HTTPS健康已验证。验收后通过正常退出按钮注销本次账号会话并关闭临时标签。
- 页面截图保存在本机会话产物mvp-creations-owner.png、mvp-archive-assets.png、mvp-attachment-content.png，不将用户归档内容截图或密码提交仓库。
- 额外只读核验：使用现有TypeScript编译器运行真实前端parseWorkflow处理该保存版本，成功得到AiCA-comic-2/137节点；源数据实际没有边，也没有节点producer附件映射，306附件由独立目录展示，不伪造依赖或产物关联。此检查不替代目标账号浏览器验收。
- 实现检查点4a33bdbf3042448b0472b3b99c72565a61135c06；任务已转in_review，最终所属账号浏览器验收通过。没有触发AI或模型付费调用，没有保存用户提供的密码；本阶段结束，未宣称完成其他团队分支合并。

# 本阶段：模块图像修改同步下游漫画（2026-10-18）

- D-MODULE-IMAGE-PROPAGATION-20261018；按用户要求仅实现图像修改传播，不扩展Agent可复用工作流。
- 增加只读image-edit-plan：沿data/control及有向semantic依赖找下游，忽略alternative_to，显式依赖优先，循环/重复边去重。根据源Graph的一跳产物节点补回设定图关联，不按文件名猜配。
- 当前实际漫画库只读检查：角色设定模块直接artifact_ids为空；源图像登记在composition节点。新计划找到设定图及漫画共14张。源档案同hash且唯一producer的无producer副本视为别名，展示更新结果，避免28条重复导出造成重复绘图；不同页面不按hash粗略合并。
- Web默认勾选模块及下游同步，显示模块/张数与每张一次模型调用提示。逐张执行及保存版本；当前模块先改，下游以自己的原图和已修改的设定图作双参考。千问按官方要求把待编辑页面放最后保持页面比例，其他供应商维持主图优先。
- 失败保留已提交图片；保持页面打开时可继续未完成步骤；成功请求ID重复提交返回原结果。无自动付费失败重试、无Key持久化。刷新会丢失浏览器内存中的批次游标，不能宣称跨刷新恢复；并发版本冲突会阻止覆盖。实际视觉识别与人物一致性仍需真实模型人工验看。
- 画廊只显示当前有效图片，源版本保留；模块逐页artifacts接口覆盖至当前版本修改结果，原始证据ID不改写。
- 文件：Gateway repo_media/platform/repository，Web RepositoryImages，模块专项、任务及STATUS。未改依赖、锁文件、防火墙或Agent执行契约。
- 验证：pytest模块内容/自动上库6 passed in5.48s（Mock），含双图参考、幂等、预览覆盖、分支隔离及依赖去重；Ruff通过；npm run build通过；validate_collaboration.py成功；git diff --check通过。未执行真实付费绘图。
- 使用既有Stop-AiCA.ps1/Start-AiCA.ps1部署，重启前queued/running解析数为0。网关PID25716；HTTP8000和HTTPS8443健康200且首页匹配新dist。
- 浏览器：既有合成QA账号打开人物设定模块，确认默认同步勾选、模块范围、每张模型调用费用提示与按钮；未点击生成。截图保存在本机会话产物，不提交测试账号或截图到仓库。官方千问多参考图约定见 https://www.alibabacloud.com/help/en/model-studio/qwen-image-edit-api 。
- 实现检查点：cbe0fff1668a69e9702f799d6a3ebc4c581fabd0；交接状态in_review。
- 下一步：用户刷新平台，在角色设定模块填写简短修改，检查14张范围后点击同步，逐张检查实际模型结果。本阶段结束。

# 本阶段：百炼真实鉴权与失效模型选择修复（2026-10-18）

- D-QWEN-MODEL-RESOLUTION-20261018。用户提供临时凭据并询问API/URL；仅进行官方接口非生成诊断与本次修复。
- 直接验证现有百炼URL：兼容模型列表GET200，证明Key通过该接口鉴权；返回图像模型包含qwen-image-2.0、2.0-pro、2.1-pro、3.0系列，没有平台此前自动选用的qwen-image-edit-plus。
- 官方multimodal-generation/generation端点，edit-plus+空messages返回404 InvalidParameter / Model not exist；qwen-image-2.0+空messages返回400 InvalidParameter / messages list cannot be empty。未发送图片或生成提示词，没有生成图片；这证明原URL可达且新名称被识别，不宣称完整绘图已经验收。
- 根因：平台将旧qwen-image自动映射到当前接口不存在的edit-plus；前两轮仅fixture模拟了旧模型，未查实时模型清单，导致误以为请求参数修复足够。现修正旧qwen-image/edit-plus兼容到qwen-image-2.0，Web新默认及说明同步。其他用户明确配置的模型保持原值。
- 文件：Gateway repo_media、Web settings/RepositoryImages、模块专项、本任务/STATUS。没有更换供应商/URL、没有改依赖、没有自动付费重试。
- 安全：Key只在诊断进程内存用于用户指定百炼官方域名，已清除变量；日志输出只有状态码/安全错误说明和图像模型名称，没有Key。用户已被提醒聊天公开的Key应撤销更换；未写入任何源码、配置文件或版本记录。
- 专项pytest4 passed in6.06s、Ruff通过；前端TS/Vite构建通过；无Key模型列表401/带Key200交叉验证。网关PID43468，HTTP8000/HTTPS8443健康200且首页匹配新dist，实现9935b30a8d1743ac7c3c848500a2002606280580已推送；本阶段结束。实际付费生图未执行，仍需用户在网页用新Key确认一次。

# 本阶段：千问编辑失败与结果显示修复（2026-10-18）

- D-IMAGE-EDIT-RESULT-20261018；用户确认服务商URL为dashscope.aliyuncs.com/api/v1、模型qwen-image。只完成本轮问题修复。
- 生产日志确认多次POST image-edits返回502，repository_image_edits尚无成功记录；旧B异常把HTTP拒绝原因统一转为provider_error，无法从旧记录恢复实际状态码或断言具体参数就是旧失败原因。
- B千问参考图请求仅发送支持的可选参数，去除steps等生图参数；编辑不强制原图size，采用服务合法默认输出尺寸，本地只对模型候选对齐，原图及遮罩外像素不改变。仍按明示规则用qwen-image-edit-plus执行原图编辑。
- OpenAI兼容参考图请求改为multipart图片文件上传，并从generations自动切换edits；不向编辑端发送seed/steps或任意原图尺寸。依据官方Images Edit及百炼Qwen Image Edit协议，无供应商切换、无自动重试。
- 保留PreviewError具体代码；HTTP拒绝只提取状态及限长字母数字错误码，不记录/转发上游正文、Authorization或Key。Gateway给出401/403/404/400/429/timeout对应中文原因。
- Web成功后先显示并选中新图，项目重载失败不再误报生图失败；右侧画廊选择当前分支历史中最近生成结果，普通后续草稿保存也不会把结果丢回原图。模块/逐页人工修改自动带入改图描述，仍需点击生成以避免输入时自动产生费用。
- 文件：B providers/live_api；Gateway repo_media及模块专项；Web RepositoryImages/Studio；任务与STATUS。没有改依赖、锁文件、Key存储或防火墙。
- 专项：模块内容/自动上库pytest 4 passed in6.03s；新增OpenAI multipart、结果选择、上游401安全错误断言；既有Qwen原图、遮罩像素/幂等/Fork/权限覆盖通过。Ruff通过。首次前端构建发现可选page.prompts未判空，修正后重建。
- 用户Key仅在浏览器内存，未读取或持久化；本阶段不具备其真实Key，没有重发真实付费绘图请求，不能声称该百炼账号已成功生成。刷新后需重新填写Key，再点击一次改图；若服务仍拒绝，现在能显示具体原因。
- 已使用既有Stop/Start脚本更新；最终HTTP8000/HTTPS8443健康200，首页匹配当前dist。网关PID43888，实现检查点79ac3161dd1a09b225ed3a86788c7a717f3a4c98已推送。本阶段结束，不延伸至其他功能。

# 本阶段：清晰Graph、原图增量编辑及大档案预览（2026-10-17）

- D-GRAPH-IMAGE-REUSE-20261017；用户三项要求作为同一集成阶段，沿用D、前端对接和本次B接入授权。不升级环境/依赖，不改防火墙。
- Graph：语义边参与SCC分层，循环不会无限抬升；邻居排序减少交叉，实测卡片宽高与间距避免覆盖；邻层走正交线，跨层/回边绕外侧，线宽不随缩放变细。仍允许用户拖动卡片，复杂图不宣称数学最优无交叉。
- 提炼图模块详情前置“选原图 + 你想改什么”表单，原始提示词及AI整合内容保留；选择目标图片后只调用该图编辑，其他图片及模块保留复用。需要下一张图时明确选择，不自动批量付费重画。
- Gateway新增固定提交的/images目录、原尺寸/缩略图读取、/modules/{node_id}/image-edits；接B LiveAPIProvider与prepare_image_output(masked_replace)，用内存传入用户配置与密钥，不保存Key、不静默生成离线替代图、不自动重试。千问reference图片通过官方multimodal content.image发送；qwen-image文生图配置在编辑时选择qwen-image-edit-plus，前端明示。
- 可选直接拖矩形：框内接受模型候选，框外由B本地合成保留原像素。模型候选尺寸不同时仅对候选对齐，原图不缩放。不框选为原图语义编辑，不宣称模型能保证每个未指定像素一致。新结果与来源/指令/模型写入ModuleImageEdit，源Artifact ID保持原义，未绑定整个语义图执行协议。
- 修改结果、私有草稿可见性、提交和幂等回执在同一SQLite事务提交，固定expected_head防覆盖；重复request_id复用成功结果。生成文件位于仓库外独立目录，读取需要仓库权限与版本内引用，Fork仅允许沿父仓库来源继承。未知供应商能力或API错误明确失败。
- 大JSON：已验证的嵌入素材首次物化到既有内容寻址文件，缓存最多16份轻量目录，后续图片请求不再反复解析112MB JSON；仍检查上传映射、大小和SHA。前端8张一页缩略图，原图按需读取、分页详情原图进入可视范围才加载，Blob卸载释放。
- 正式Graph生产契约新增可选ModuleImageEdit/image_edits，生成Schema/TS，旧版本保持可读。改动包括Gateway repo_media/platform/repository；B providers/live_api的千问参考图最小适配；Web GraphExplorer/workflow/Studio/RepositoryImages/SemanticModuleContent及样式；契约生成物、专项、任务记录。
- 最小验证：既有aica Python模块内容/自动上库专项最终4 passed in5.88s，覆盖参考图确实发给Qwen、区域内改变/框外精确保留、原图不变、幂等一次调用、结果提交重读、跨用户404；Ruff通过。Web workflow专项6 passed（含语义循环+实测卡片尺寸两方向不重叠）；TS/Vite构建通过。全部绘图调用为MockTransport，未声称真实千问账号付费验收。
- 实际112MB源档案：28张图，目录首次建立4.22秒，温缓存0.0秒；前两张缩略图52385/56669字节，原图可读。本阶段未再次调用解析模型或付费绘图；没有可用绘图Key。
- 浏览器：独立私有合成联调账号及明确标记样例，11节点/12条语义关系；横向/纵向DOM检查重叠对数0，图片Blob实际显示，原图修改简单文本及可选区域入口可见。该合成样例不冒充用户真实提炼。未发布到社区、未使用真实用户密码。
- 命令：module-content-tests、web-build、gateway-style，追加npm run test -- src/workflow.test.ts；协作校验、git diff --check；既有Stop/Start部署脚本。初次UI样例误用ZIP导入路径导致请求头422，已改用正式archive-json；两次npm/测试路径使用了错误cwd，纠正后检查通过，未安装任何依赖。
- 部署：确认active解析为0后使用既有Stop/Start脚本更新，网关PID44860；HTTP8000/HTTPS8443健康200，首页与当前dist字节一致。实现检查点0f0869db9af06a1e211a8331201644ff8ed00b03已推送。下一步：本阶段结束。用户刷新后点击模块即可选图描述改动；真实绘图仍需在设置填入自己的Key和可用编辑模型。

# 本阶段：百MB档案紧凑提案与真实验证（2026-10-17）

- D-GRAPH-COMPACT-OUTPUT-20261017，仅修复当前100MB档案提炼截断并验证真实结果。
- 已证实上一轮finish_reason=length、completion_tokens4096、prompt_tokens76910。上阶段仅靠提示词约束不能限制模型冗余输出，本阶段不再沿用同一完整提案Schema处理大项目。
- 0.5.3：超过12页使用内部CompactProposal（模块短目的/共享prompt/必要semantic边）；n/e短编号还原为真实节点与证据，程序填充候选分析者身份，再走原正式ModelProposal、来源/归属/依赖校验。模型无法用短编号绕过来源校验；正式Graph契约未变。
- 大项目snapshot使用8192输出额度和120秒超时，普通任务沿用配置；准备材料及前端告知8192上限及费用。不增加自动重试，不静默续写、不接收截断JSON。
- AI未收纳的分页由规则恢复为明确标记的源内容模块，不冒充AI提炼或执行结果。原始提示词及图片继续保留。
- Gateway准备与模块材料读取复用一次已授权/校验的snapshot，避免每个附件都重新加载112MB档案；仍逐附件校验哈希、大小与上传映射。
- 最小fixture：test_module_pages.py、test_graph_autosave.py、test_graph_api.py::test_truncation_diagnostic_survives_polling_without_retry，最终5 passed in5.94s；Ruff通过；npm run build通过。短编号紧凑输出、75页恢复和8192 snapshot不改变共享provider配置已有专项覆盖。
- 真实调用（非fixture）：用户已上传112353689字节档案，36源节点/80事实/75页面。新模型输入158912字节；DeepSeek真实1次请求成功，prompt_tokens50611/completion_tokens1633/total_tokens52244，输出10个AI模块，自动存私库。未进行自动重试。
- 核对发现12条无原始节点归属的页面未被AI收纳，程序按原文补为1个rule源内容模块；完整75页通过原来源校验，追加仓库新版本，未修改旧成功解析和旧提交。最终11模块（10 AI + 1 rule）。
- 实际私库70f5e963-36bf-47a2-9fe4-14213e0b4717，完整提交85f59704-f46a-4e27-851e-0b6146f1926a；实际parse c332e68c-29f3-4f80-983f-0b96003fd5cc。身份/凭据、档案和模型正文未提交代码库。
- 文件：Runtime graph/compact_output.py、provider/context/module_content/assembly；Gateway graphs/repository/module_content及专项；Web导入策略说明；本任务/STATUS。未改依赖、环境、锁文件或防火墙。
- 部署：active解析为0后用既有Stop/Start脚本更新；网关PID17332；HTTP8000/HTTPS8443健康200且首页匹配当前dist，匿名私库404，持久化75页/11模块已核验。
- 边界：本次真实112MB档案已成功并上库；不能保证任意损坏档案、无限上下文或外部模型/网络永不失败。大项目逐页仍采用原始内容，不逐页生成AI扩展；语义Graph不伪造执行结果。
- 实现检查点：640b12265922659ccf466af4eb10244dabb0ee2d，已推送用户指定分支。
- 下一步：本阶段结束；用户打开已保存结果，无需重传或再次付费解析。

# 本阶段：百MB档案输出预算与失败诊断（2026-10-17）

- D-GRAPH-LARGE-OUTPUT-20261017；仅处理本次大档案提炼失败。
- 实际失败记录：解析器0.5.1，80事实、75页面，max_tokens4096，invalid_semantic_graph且usage为空。旧Gateway丢弃异常细节，无法确认实际失败是输出截断、Schema还是来源校验；不能将推测宣称为已证实原因。
- 0.5.2对超过12页的输入显式选择modules_only_pages_restored_from_source：AI专注模块及共享提示词，page摘要/原始提示词/图片从完整源材料恢复，不逐页生成AI扩展。前端在调用前说明此策略，不增加输出额度、模型次数或自动重试。
- semantic_graph_parses兼容添加error_detail；持久化安全的stage/finish_reason/字段校验位置及已返回usage；不把completion或reasoning写入此字段。旧任务保持原记录，不编造诊断。前端两入口中文区分截断、JSON、Schema与来源校验，并提供失败后手动重新准备已有档案（仍需确认才调用AI）。
- 只读重做用户实际上传档案的本地prepare：112353689字节、36源节点、80事实、75页、51提示词；新策略正确触发，未调用模型、未创建新解析或修改旧失败结果。
- 改动：Runtime graph/context.py、provider.py；Gateway graphs.py及专项测试；Web SemanticImportPanel/SemanticReview、parseFailure、两个客户端类型；任务记录与STATUS。
- 命令：既有aica Python pytest（test_module_pages.py、test_graph_api.py::test_truncation_diagnostic_survives_polling_without_retry、test_graph_autosave.py），5 passed in6.25s；Ruff check apps/gateway及runtime graph通过；apps/web npm run build通过。MockTransport验证75页恢复及一次请求、截断原因和用量可轮询、不重复调用。
- 部署使用既有Stop/Start-AiCA脚本，操作前active_parses=0；网关PID29220；HTTP8000/HTTPS8443健康200，首页与当前dist字节一致，error_detail迁移已核验。
- 限制：本阶段没有重发用户真实付费解析请求；尚不能声称其真实AI结果已成功。源档案已保存，可从上传快照重新准备并由用户确认提炼。超大输入的原有安全边界保留。
- 实现检查点：b9f7f9e36a2860cbffb3fa6ca70a5c486e74e5d9，已推送用户指定分支。
- 下一步：本阶段结束；刷新网页，选择已有快照或重传后审阅新策略并确认一次提炼。

# 本阶段：语义提示词编辑与大输入修复（2026-10-17）

- D-GRAPH-EDIT-LARGE-INPUT-20261017；沿用用户指定分支和D/前端对接授权，仅完成本次修复。
- 语义模块及每页新增“我的提示词修改”，支持复制AI整合/原文起步、清空撤销、自动/手动保存；真实写入semanticGraph，而非只更新显示字段。原文、AI候选与人工修改分开，重开后保留。
- 仓库提交在source及graph_id一致时继承上一版本的来源绑定，修改后原图继续按固定提交授权读取。仓库提交记录新版本，语义revision_id继续标识解析基线；本阶段未生成或伪造执行结果。
- 解析器0.5.1移除400事实硬门槛；超过128KiB时按长度逐级压缩事实内容预览，保留所有事实/证据/分页引用，并在coverage声明AI没有读取省略内容。不增加模型调用次数或自动重试。源材料完整内容用于提示词和分页恢复，附件材料预算耗尽后也继续抽取完整目录。
- 并非无限模型上下文：压缩后的引用目录仍有2MiB安全边界，原有200源节点、历史和单文本附件读取边界保留。原始上传数据不因模型预览被裁剪。
- 改动：Gateway platform/module_content、专项test_module_pages；Web Studio/SemanticModuleContent/workflow和生成类型；Runtime graph contract/context/provider/input_budget、生成Schema/TS；任务记录。本阶段未改环境、锁文件、防火墙或B执行器。
- 命令：既有aica Python执行模块内容及自动保存pytest、Ruff、契约生成器；apps/web执行npm run build；协作校验与git diff --check。专项3 passed in4.36s；Ruff通过；TS/Vite构建通过。MockTransport fixture验证，未调用真实付费模型。
- 专项覆盖：模块/逐页修改保存重读、原始提示词完整保留、旧提交不变、并发旧head冲突、修改后图片读取、450事实及超长文本预览。首次专项因请求缺必填message而返回422，补齐测试请求后验证409冲突通过。
- 部署：确认无queued/running解析后，用既有Stop-AiCA.ps1 / Start-AiCA.ps1更新，网关PID32108；HTTP8000及HTTPS8443健康200，首页字节与当前dist完全一致；HTTPS代理及局域网规则沿用。
- 实现检查点：386a94868bc597306475e2a774f2b4a99dce65ff，已推送用户指定分支；专项与构建证据见本阶段及任务记录。
- 下一步：刷新网站，在创作图模块详情点击编辑提示词；截图中失败的导入重新发起提炼即可。旧成功图可直接编辑保存。本阶段结束。

# 模块提示词、逐页内容和图片阶段（2026-10-17）

- 发布完成：实现 `c743d09`；首次推送因B同时更新远端而拒绝，fetch后普通无冲突合并B `fd187bf`，保留全部图层更新，未force push。合并检查点 `04f01343b8b61360563a5cbc692e095ff4569894` 已推送并核对远端一致。合并后2项专项4.74s、Ruff、前端构建通过；最终网关PID41516，HTTP/HTTPS健康、材料路由及合并后静态构建字节核验通过，原证书/数据/网络规则不变。模型输出中的页摘要/提示词采用紧凑提议，程序补齐来源身份，保持原模型输出预算。B交接中的全量体积断言历史结果未在本阶段冒充已重跑或通过。

- 用户明确要求把JSON相关提示词整合到模块，可适当拓展，逐页提炼并显示图片；任务D-GRAPH-PAGE-PROMPTS-20261017。沿用D/前端接线授权和bd_First_Edition_16.29，基线300a582。先检查干净工作区、现有契约/组装/前端/产物权限；此阶段不改A原始格式、不把候选图变成执行图。
- 原因：Step原来无提示词/分页结构，前端仅从少量变量中找prompt且artifacts为空；旧策略明确把逐页内容收进证据、不展开。用户本轮要求覆盖该展示策略。
- Runtime解析策略0.5.0：Step兼容新增可选prompts/pages，旧图默认空；原始提示词由确定性提取器从字段、literal绑定及JSON字符串、已哈希校验JSON材料保留；AI只能输出整合/建议并引用证据，不可伪造recorded。Page保留每页摘要、提示词和真实登记图片ID；原始节点仍单一归属，材料页不变成虚假执行调用。
- 大JSON文本在压缩到模型材料预算前提取完整页/原提示词目录；模型收到原始位置和有界预览，原文由后端保存，不让模型重复复述。模型仍单次授权调用、原输出预算和128KiB/400事实边界不变；没有新增模型/Embedding/依赖或自动付费重跑。
- 图片关联仅采用页记录的显式URI/产物ID、真实producer关系，不按哈希或猜测文件名绑定；无可靠页归属的模块图片单列，缺失图片明确提示。原始图、照片不因AI文本建议被重新生成。
- Gateway新增GET /api/repos/{repo_id}/modules/{node_id}/materials?commit_id={commit_id}，复用仓库+固定提交授权和已上传产物哈希校验；旧图从保存的解析事实/材料恢复原始提示词与页信息，不修改不可变历史、不调用AI。无源绑定的外部Graph只展示自身字段并明示图片不可读。
- 前端模块详情和执行工作台语义视图显示原始提示词、AI整合、AI扩展建议三个分区，逐页摘要/提示词/真实图。产物请求携带平台会话，Blob在组件卸载时释放；不把令牌放URL、不读用户电脑路径或抓取外链。原有解析成功自动上库保留。
- 文件：Runtime graph/{contract,context,module_content,assembly,materials,provider,validation}.py，生产契约导出的Schema/TS及生成脚本/样例默认字段；Gateway module_content.py、platform路由和test_module_pages.py；前端SemanticModuleContent及样式、Studio/SemanticReview/GraphExplorer/workflow/platform、共享生成类型；策略/部署交接、collaboration和任务记录。
- 命令与验证：现有aica Python执行graph_contract_design/export.py（19项生成产物）；ruff检查Gateway及改动Runtime通过；pytest仅test_module_pages.py和test_graph_autosave.py：2 passed in4.67s。覆盖大于24KiB的JSON材料、逐字原提示词、单独AI建议、2页图片字节/MIME、旧图无额外模型调用恢复、缺失图片、跨账号隔离及原自动上库。模型为MockTransport合成fixture，真实模型费用0。TypeScript/Vite生产构建通过；首次构建发现可选page.prompts未默认空数组，已修正后通过。不重复大范围测试，未声称实测AI语义质量更优。
- 模型正文/页面有预算边界；目录预览不能冒充模型阅读了全量材料。原文完整保留，JSON只有图片路径时仍需完整ZIP/便携JSON才能显示图片；新的AI整合需要用户重新审阅并发起提炼，旧图只做事实补充。
- 部署使用既有Stop-AiCA/Start-AiCA脚本，保留SQLite/证书/网络规则和已有数据；最终HTTP/HTTPS、静态构建和新增接口核验后选择性推送指定分支，完成本阶段。

# AI提炼结果自动上库阶段（2026-10-17）

- 发布完成：实现提交 `dc412c7bdbb873d63ff7cf88db7479dc2a4dd646` 已推送指定分支并核对远端一致。实际启动补存1个既有成功解析，无新模型调用。

- 用户明确要求AI解析完成自动进入库；任务D-GRAPH-AUTOSAVE-20261017，沿用指定bd_First_Edition_16.29，基线72e1217。仅实现语义Graph自动上库，不在同一阶段加入执行结果回写。
- 原来解析结果已保存在后端，主导入页还需手动建库/提交；执行工作台语义查看没有上库接线。现由后端GraphService在成功落盘后自动提交至当前用户的私有库，与浏览器生命周期无关；启动时补存既有成功且未上库的用户解析，不再次调用模型。
- 每个解析ID只保存一个提交，数据库事务覆盖仓库创建、版本和幂等记录；同一平台项目后续解析追加私有main版本。若用户曾将旧库公开，后续解析进入新的私有库，不向公开库自动写入。保留项目、import及原始附件来源关联。
- 解析结果先独立落盘，仓库保存失败不把已成功解析改成失败；新增鉴权POST /api/projects/{project_id}/semantic-parses/{parse_id}/repository可幂等补存，不会重跑模型。解析查询附publication仓库/提交ID。
- 前端两个提炼入口接线：主导入页完成后自动确认保存并打开Graph，失败保留补存按钮；工作台语义视图显示已保存仓库链接，并同步刷新当前用户的前端作品列表。审阅确认文字注明成功后自动保存私有库。
- 文件：apps/gateway/src/aica_gateway/{app,graphs,platform}.py、apps/web/src/{App,SemanticImportPanel,SemanticReview,platform}.tsx/ts、专项test_graph_autosave.py、collaboration.json、任务/部署交接和本记录。
- 命令及结果：git status/定向读取链路；python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests/test_graph_autosave.py：1 passed in2.69s（内含后台自动保存、幂等、新版本、存储故障补存、隔离及公有库保护）；python -m ruff check apps/gateway：通过；apps/web npm run build：TypeScript/Vite通过。额外公有库断言最初误读repo响应的head，改为读取graph.commit.id后通过，未改产品校验。模型使用既有MockTransport离线fixture，无真实付费模型调用。
- Stop-AiCA/Start-AiCA部署，最终网关PID45728，HTTPS代理保持原服务；HTTP/HTTPS健康200、静态index等于新构建、OpenAPI含补存路由。SQLite原数据保留，新增映射表；未更改网络规则或模型配置。
- 下一步：选择性提交并推送指定分支，用户刷新后正常审阅提炼即可自动上库。本阶段结束；执行产物自动回写仍是另一项功能，不把语义Graph说成已可执行。

# 新版网站 / MCP 对接部署阶段（2026-10-17）

- 任务 D-MCP-PLATFORM-UPDATE-20261017。用户指定更新 bd_First_Edition_16.29 并部署，明确允许仅局域网放行8443；沿用D角色，用户授权按A/B交接同步与装配C端，不变更队友分支。阶段只覆盖本轮更新，不启动其他待办。
- git fetch 后将网站 f0fc294 快进至9e293f2；A共享协议/MCP源码与说明同步自feature/a-protocol-mcp@9fae685，未把A分支整体合并。读取AGENTS、角色/环境/协作/分支规范、A PLATFORM_UPLOAD/BACKEND_HANDOFF/REMOTE_TRANSFER、B BD_ALIGNMENT/PROVIDER_COMPLETION及补丁说明。原工作区当时干净，既有初版提交和数据库保留。
- 文件：apps/web应用B c-provider-settings.patch（含交付锁文件）；apps/gateway添加MCP项目上传令牌、portable JSON及鉴权执行代理；packages/schema/src、packages/mcp-adapters/src同步A，后者另增加AICA_UPLOAD_CA_FILE；runtime graph/context/materials/provider和策略文档升级0.4.0；部署脚本/交接、专项测试、collaboration与本任务记录。
- 取消前后端原5MiB文档与16MiB裸JSON入口限制，保留结构/字段校验和A共享ZIP512MiB、单产物64MiB、portable JSON128MiB及元数据16MiB契约。不用网站限制冒充协议限制。内嵌附件在哈希验证后可实际读取；普通JSON缺失附件如实报告。
- 提炼加强通用职责、独立替换与交付物追溯；大文字附件不再因超过单次文本预算完全跳过，校验完整哈希后取明确标记的不完整摘录。保留原材料、证据和模型输入128KiB/400事实边界；未用真实付费模型做质量对比，不宣称效果已经实测更优。
- MCP：POST /api/projects/{id}/imports保持共享二进制ZIP/JSON与回执；上传专用令牌30天、摘要存储、项目级范围、可撤销。前端可建平台项目、一次显示令牌、刷新已收快照并审阅提炼；不自动触发AI/公开发布。按用户独立loopback执行Worker与数据，浏览器只用平台会话；鉴权图片转临时Blob读取，持久化稳定产物路径。执行结果不会自动回写社区仓库。
- 命令：git fetch/merge --ff-only；git apply --check及应用B交付补丁；git restore --source=origin/feature/a-protocol-mcp（仅上述A源码/交接范围）；两个前端目录npm ci --no-audit --no-fund，使用交付锁；apps/web npm run build；prepare_runtime.py按交接导出固定Runtime/A和D补丁至仓库外；Stop-AiCA/Start-AiCA/Start-AiCA-HTTPS；批准后的管理员Enable-AiCA-LAN.ps1 -Port8443 -LocalAddress10.19.51.6 -RemoteSubnet10.19.48.0/21。
- 最小验证：专项pytest4 passed in4.21s；前端parserClient/preview-service/settings专项12 passed；TypeScript/Vite生产构建通过；python -m ruff check apps/gateway通过；协作记录校验通过。首次构建因B本地依赖尚未安装失败，按其交付锁npm ci后通过；专项测试的两个无效fixture调用修正后通过，未放松产品校验。不重复大范围回归。
- 真实部署验证：HTTPS经本机CA校验，/health和前端200；A真实upload_bundle首次/幂等回执一致；A PlatformSync真实后台线程完成synced并保存回执；B local_fallback预览200、认证图片200（80891字节），来源标签正确；隔离Runtime项目查询200；Graph配置显示0.4.0。使用合成记录/账户，未调用付费模型。未声称已从第二台电脑验证访问。烟雾Worker核验命令行后停止，最终服务重启应用最新源码。
- HTTP http://10.19.51.6:8000保留，HTTPS https://10.19.51.6:8443。防火墙实读：Enabled/Private/Allow，LocalAddress10.19.51.6，RemoteAddress10.19.48.0/21。CA与私钥、SQLite、执行源导出、日志/PID均在LOCALAPPDATA部署目录，不进Git；CA不自动加入任何客户端信任库，MCP需本分支CA扩展或A后续同步此小改动。
- 原有WinError1314符号链接回归限制沿用历史记录，不宣称本阶段完整回归/主干合入。下一步：选择性提交并推送指定分支，核对远端SHA；用户按apps/gateway/deployment/README.md配置客户端CA与令牌。分支发布后本阶段停止。

- 发布结果：实现提交 `6c9d7f9b9dcdf92a45e22330dedfe32c40b7ce6c` 已推送 `origin/bd_First_Edition_16.29`，git ls-remote核验一致。最终网关PID31348、HTTPS代理PID42248；HTTP/HTTPS健康均200，静态index与最新dist字节一致，OpenAPI包含MCP令牌与执行代理路径。暂存71个源码/文档文件的实际模型密钥精确匹配、常见凭据模式和运行数据排除检查为0发现；未上传密钥/SQLite/日志。补写此交接后结束本阶段。

# .aica AI 提炼入口修正阶段

- 日期：2026-10-17，Asia/Shanghai；任务 `D-AICA-AI-REFINEMENT-20261017`，分支 `feature/d-aica-ai-refinement`。用户明确要求已经生成的 .aica 也进入 AI 复用内容提炼，不能继续直接显示原始节点。仅修正该入口与档案上传支持，保留前序未提交工作；不改A协议、来源Runtime或模型配置。
- Gateway 新增鉴权 `POST /api/projects/{p}/archive-json`，接收最多16MiB正式A JSON，调用A require_archive，按接收原始字节保存/hash、原子提交并按项目+内容去重；201首次/200重放。内部回执 aica-archive-json/0.1，原ZIP上传契约不改。JSON没有传附件，不访问用户电脑路径/外部URL，缺失附件独立记入manifest；附件查询明确返回unavailable而非StopIteration。原档案中的可观察事实/URI/availability不擅自改写。
- C 的原始 .aica / aica.json 统一送入AI准备入口：包括从旧JSON导入控件选取正式A档案时自动转交上方提炼面板，隐藏原节点直接建库按钮。支持 .aica/.json/ZIP，已生成语义Graph仍直接导入；非正式展示记录不伪装成A档案。JSON准备阶段保留原节点/文字供模型分析，原始图不能冒充新提炼结果。
- 页面展示parser版本、输入预览和缺失附件数量，说明只有JSON内信息/附件元数据可用；完整附件内容需ZIP。无事实时拒绝开始模型请求。用户审阅并明确勾选外发后才调用0.3.0解析器，AI负责提炼复用模块；成功后保存并打开新语义Graph。单次文件的request_id保持稳定，网络重试可复用服务端幂等任务，避免重复费用。旧仓库不会自动重算或被覆盖。
- 已执行最小验证：TypeScript/Vite构建通过；Ruff `apps/gateway/src`通过；真实本机HTTP上传既有合成A档案201、相同内容重放200且回执相同、原JSON读回一致、prepare为0.3.0并生成12项facts和module_hints；4份未上传附件被明确记录，准备可继续但不编造内容。allow_external_processing=false时403，任务列表仍空，模型调用0次。沿用用户减少测试要求，没有运行pytest/Vitest全量或真实付费模型请求。
- 已执行既有 Stop-AiCA/Start-AiCA，后台PID42356，入口 `http://10.19.51.6:8000/` 不变；数据库、既有库和局域网规则保留。最终前端构建发布已包含旧入口到提炼面板的转交。一次文案脚本因cwd未找到文件没有写入，改在仓库根目录执行后修复并重新构建。
- 修改：Gateway repository.py/app.py、SDK及类型/C副本；C SemanticImportPanel.tsx/ImportPage.tsx；本任务/STATUS与接口说明。没有新增依赖/改锁文件/改全局环境或原数据；未提交推送合并。命令：git status/switch、Get-Content、apply_patch、Copy-Item、具名临时Python HTTP/合成数据核对、既有conda npm build/Ruff、启停脚本、协作及diff检查。
- 下一步：用户刷新平台、重新上传原始 .aica，审阅→确认AI提炼→保存并查看新Graph。缺失附件可补标准ZIP；已经保存的旧10节点展示记录不能仅靠刷新变成提炼图。停止本阶段；实际用户材料的模型提炼由用户确认发起，不冒充本轮已实测。

---

# 解析接入阶段：复用优先 Graph 上平台

- 日期：2026-10-17，Asia/Shanghai；任务 `D-SEMANTIC-PLATFORM-20261017`，分支 `feature/d-platform-semantic-graph`。用户要求部署已完成解析模块并显示提炼后的 Graph，明确减少测试；随后指定同级 `AiCA-d-reusable-graph-modules/packages/runtime`。仅本阶段，保留前序所有未提交修改，未推送/合并。
- 来源 checkout `feature/d-reusable-graph-modules`，HEAD `6e4a3c35e98a4b6575aa005c366603d169b18ba2`。只读该来源，将平台实际使用的 context.py、provider.py、reusability.py 及 GRAPH_MODULE_POLICY 复制到部署 checkout；源文件未改，消费契约仍为 aica-semantic-graph/0.1。已运行 API 确认 parser/prompt 为0.3.0、模型配置可用，没有打印凭证或改变模型配置。
- 前端识别提炼 Graph：按既定生成类型映射 label/purpose、候选状态、证据、variables和coverage，默认卡片展示模块短名/短职责；详细材料和约束在节点详情。data/control/semantic 保留类型和各自ID，语义边与候选边虚线区分；语义关系不参与执行依赖布局/dirty推演，不伪造 Golden Path、模型、Harness或执行历史。提炼图保持只读/不可执行；JSON导出和保存保留完整原始Graph。
- “开始创作”新增原始ZIP解析入口：上传A标准包→本机准备输入→页面展开审阅→用户勾选允许外发DeepSeek后发起一次解析→轮询状态→成功后保存精确 graph/revision 到私有仓库并打开。解析失败不自动重试；离开页面停止轮询但不伪称取消服务端任务。已有 graph.json 直接沿用下方JSON导入，免再次模型调用。
- 后端支持严格GraphDocument结构/引用/执行DAG校验，正式解析成功图通过 semantic-commits 与源project/import关联。独立上传的Graph只做结构/引用验证，不冒充已核验原档案证据。浏览器在LAN HTTP缺少WebCrypto时使用鉴权 browser-imports，由服务器计算接收内容哈希后仍走原A传输验证和幂等存储。
- 检查按用户要求缩小：`npm run build` TypeScript/Vite通过；`python -m ruff check packages/runtime/src apps/gateway/src`通过；一份既有合成Graph结构检查通过；真实部署HTTP读取parser_version=0.3.0/configured=true，确认新路由，并实际写入/读取一份4节点合成语义图，返回文档与输入完全一致。该检查留在专用联调账号的私有库，非用户真实数据。没有跑pytest/Vitest全量或付费模型调用。初次构建的React19 useRef缺少初值已修复。
- 执行既有 Stop/Start 脚本重启本平台，当前PID24100，原数据库和LAN规则保留，`http://10.19.51.6:8000/`入口不变；前端最新静态构建已发布。用户已有8模块漫画结果仍只在原本地结果目录，未擅自自动公开、修改或再次解析。
- 修改：Runtime三文件/策略说明，Gateway app/platform与SDK/类型，C workflow/platform/ImportPage/SemanticImportPanel/App/GraphExplorer/Studio及同源生成类型，本任务/STATUS。未安装依赖、改冻结Graph契约或A/B/来源checkout。命令另包括Get-Content/rg/Copy-Item/git switch/status/diff、具名临时Python只读结构及HTTP冒烟。协作结构和diff-check写回后核对。
- 后续：刷新平台进入“开始创作”，使用自己的账号直接导入已有graph.json，或上传标准ZIP并审阅后自行发起一次提炼。模型真实调用与用户材料端到端未重跑，公开分享需用户选择发布。停止本阶段，不扩展语义编辑/执行服务。

---

# 本机部署阶段：C 前端接线与局域网入口

- 日期：2026-10-17，Asia/Shanghai；任务 `D-LAN-INTEGRATION-20261017`。用户要求本机部署两端并串联，明确选择“局域网内其他电脑可访问”，随后明确要求重新弹出管理员授权窗口。范围为这一个部署/联调阶段，授权包含本地 C 集成副本；不转移 C 所有权。
- 分支 `feature/d-local-lan-integration`，HEAD 基线仍为 `ba0bd85f16e8d49fb05b3acf52b73a1cae7fd9e6`；上一平台阶段未提交差异完整保留，没有丢弃、覆盖或冒充提交/合入。当前仍是本地未提交候选，没有推送/PR/合并。
- 用户给的 C `apps/web` 复制到本 checkout 原占位 `apps/web`；原下载目录不改。用其 lockfile 执行 `npm ci --no-audit --no-fund`，124 包安装成功；本地/原件 lockfile SHA-256 均为 `343EEBF26305F2849314877EC902936108F0F954D664732D3AC392D16CA70FFE`。没有升级运行时、修改根依赖或改锁文件。
- C 副本接入真实注册/登录/注销、当前账号隔离、公开/我的仓库、JSON 导入上库、异步保存/发布、Fork、新建/切换 Branch。保存串行排队并沿用返回的 head，409 错误保留给页面；失败不显示保存成功或公开发布。刷新重新登录，平台令牌仅内存保存，不与模型 API Key 混用；浏览器本地收藏/模型设置保留为本地偏好，没有自动上传旧本地数据。
- Gateway 通过 `AICA_WEB_DIR` 只挂载构建产物，页面和 API 同端口同源；前端取当前 window.location.origin，LAN HTTP 中使用 getRandomValues 生成客户端随机标识。修复 SDK `fetch` 未绑定 globalThis 导致浏览器 Illegal invocation。原 `/api` 权限、静态文件隔离与 A/B 逻辑保持；未接通真实生图供应商或调用模型。
- 后台服务已经启动，当前 PID **40768**，IPv4 `0.0.0.0:8000`，单 worker。页面 `http://127.0.0.1:8000/`，本次核对的 LAN 地址 `http://10.19.51.6:8000/`。数据在 checkout 外 `%LOCALAPPDATA%/AiCA/lan-deployment/data/platform.sqlite3`，实测 SQLite **3.54.1**，Graph JSON 按不可变版本存储，分支保存 head，附件在 snapshots。
- 首次 UAC 被取消，未添加规则；用户再次明确授权后通过 UAC 添加并核对 `AiCA-LAN-TCP-8000`：Enabled/Allow/Inbound，TCP8000，LocalAddress 10.19.51.6，RemoteAddress 10.19.48.0/21，Private profile，禁止边缘遍历。没有关闭防火墙、改网络类别、端口转发、公网隧道或 detector/IOC 操作。

## 本阶段命令与验证

| 命令/验证 | 结果 |
| --- | --- |
| `conda run --no-capture-output -n aica npm ci --no-audit --no-fund`（apps/web） | 成功；沿用原 lockfile，未安装新运行时 |
| `conda run --no-capture-output -n aica npm test`（apps/web） | 最终 **22 passed**，含新增队列 head、保存失败不公开和真实账号适配测试 |
| `conda run --no-capture-output -n aica npm run build`（apps/web） | TypeScript 与 Vite 构建通过；最终文案修改后再次构建 |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests` | **183 passed in54.15s**；运行时/原API回归通过 |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests/test_web_deployment.py` | 新增 **2 passed in0.78s**；静态页面不遮蔽 API、不泄露数据、只接收构建目录 |
| `conda run --no-capture-output -n aica python -m ruff check apps/gateway` | 通过 |
| `conda run --no-capture-output -n aica node --test apps/gateway/integration/platform-client.test.mjs` | 最终 **3 passed** |
| `Start-AiCA.ps1`、`Stop-AiCA.ps1`、再启动 | 后台运行与停止/重启通过；公开库 commit.id 和 document_hash 不变，恢复健康 |
| 无代理 HTTP 客户端，127.0.0.1 和 10.19.51.6 的 `/`、`/health`、`/api/repos` | 均200，页面/JSON MIME正确；不冒充第二台电脑实测 |
| Browser CUA | 真实登录→导入3节点2依赖→创建库→新建experiment/lan→发布→Fork私有副本；匿名新页面重启后读取公开库和分支，节点详情 dialog.open=true |

其他命令：git status/switch/diff、rg、Get-Content、Copy-Item、Get-FileHash、apply_patch、ipconfig、只读 SQLite 表/计数查询、Get-NetConnectionProfile/Get-NetTCPConnection、Get-NetFirewallRule/AddressFilter/PortFilter、具名 New-NetFirewallRule 经 UAC 执行。最终协作校验与 diff-check 写回后复核。没有运行 sudo/systemctl/caput/receiver 或高压命令。

初次停服校验因 PowerShell7 ConvertFrom-Json 自动转换 UTC DateTime 后又 Parse 而误判 PID 创建时间，安全拒绝且未停错进程；核对路径/命令/时间后修复双重解析，停启成功。一次文案脚本 cwd 错误导致未找到文件，未写入任何文件，已在正确目录修复并构建。浏览器跨回复临时 tab 丢失、以数值 browser ID 绑定 LAN tab 超时后，改用既有 iab 别名创建 localhost tab，立即 markDeliverable 后恢复；LAN连通另用真实无代理 HTTP 验证。未绕过浏览器安全提示。

## 文件、边界和下一步

- 新增 C 前端工作副本，主要接线：App/auth/LoginPage/platform/BranchPanel/Studio/ImportPage/PublishDialog/workspace/ProjectCard；新增 SDK副本/类型与 platform.test。Gateway 新增静态挂载、deployment 启停/防火墙脚本与说明、2项静态部署测试；修复 SDK fetch绑定。更新当前任务、Gateway API/C README 和本状态。依赖目录、dist、数据库、日志、凭证均不进 Git。
- 部署记录与数据库说明：`apps/gateway/deployment/README.md`。运行状态文件在 `%LOCALAPPDATA%/AiCA/lan-deployment/service.json`；脚本按实际位置发现解释器/checkout，PID仅为本次事实。没有配置开机任务；电脑重启后运行 Start-AiCA.ps1。
- 留下1个专用合成联调账号、1份公开合成Graph和1份私有Fork用于核查；不记录测试密码或令牌。截图为工作区 deliverables/aica-lan-graph.jpg，无凭证。
- 限制：尚无第二台电脑的访问证据；不宣称公网部署或真实生成执行。A 原有两项 Windows 符号链接测试权限失败见上一阶段，未改权限/跳过/重复全量A检查。当前服务与局域网规则已生效，阶段完成；下一步由同网段另一台电脑访问入口，团队评审本地 C 集成副本。本次停止，不扩展生产架构。

---

# D 平台阶段：Graph 类 GitHub 后端 MVP

- 日期：2026-10-17，Asia/Shanghai；任务 `D-REPO-PLATFORM-20261017`。用户明确按 D 职责要求登录、上库、查看库、Fork、新建 Branch 和 C 对接；本次仅此一个阶段。独立分支 `feature/d-graph-repository-platform`，基线 `ba0bd85f16e8d49fb05b3acf52b73a1cae7fd9e6`；启动时工作区干净。当前交付为本地未提交候选，无远端认领接受、提交、推送、PR、合并或部署。
- 实现账号注册/登录/当前用户/注销，随机 Bearer 会话摘要、scrypt 密码哈希、7 天到期、持久化认证限流。保留原开发令牌与 `/api/projects`；新库 API 要求真实注册账号，保留 `user:` 主体空间，默认可无预置令牌启动。
- 实现私有/公开库、我的库/发现列表和搜索、JSON 上库、正式 ZIP 档案上库关联、Graph 查看、不可变版本历史、分支及 Fork。默认私有；写权限仅所有者，匿名仅可读公开库。SQLite 写事务强制 `expected_head` 乐观并发；Fork 固定所选版本并记录谱系，修改独立。分支为平台 Graph 指针，不创建磁盘 Git 分支，不修改 A 档案内部结构。
- 复用 A 的协议验证、传输包验证和原 Gateway 产物存储；正式 `.aica` 与 C 展示 steps/modules 分别校验。验证模块唯一、引用、DAG、提示词、JSON 深度/大小/可移植性和常见凭证字段；不请求远程 URI，不调用模型或执行用户工具。仓库附件按固定版本和仓库权限读，不能借公开库读取其他私有项目。
- C 交付：`apps/gateway/PLATFORM_API.md`、无依赖 `integration/platform-client.mjs` 及 `.d.mts`。直接兼容 C 的 Studio 导出记录，提供登录、建库、保存、发布、Fork、分支、ZIP 和 Blob 媒体接口。已检查用户提供的 C 源码；C 原页面没有修改或切换到服务端，下载快照、本地数据和 A/B 代码均保持原样。

## 命令和验证

命令在仓库根目录运行，Python/Node 沿用既定 Conda aica，不安装或升级依赖。被测源码为本阶段工作树；没有把基线 commit 冒充实现的被测 commit。

| 命令 | 结果 |
| --- | --- |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests/test_platform_api.py` | 首次 12 passed、2 failed；发现 strict UUID 拒绝 HTTP JSON 字符串，已修复字段解析，最终回归覆盖 |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests/test_platform_client_roundtrip.py` | 1 passed in 3.77s；真实本机 HTTP SDK 往返 |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests` | 最终 183 passed in 51.67s，含 20 项新增平台测试和 1 项真实 HTTP 往返 |
| `conda run --no-capture-output -n aica node --test apps/gateway/integration/platform-client.test.mjs` | 3 passed；拦截 fetch 的客户端契约测试，明确与真实 HTTP 分开 |
| `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/schema/tests packages/mcp-adapters/tests packages/runtime/tests apps/gateway/tests` | JSON 边界补强前 282 passed、2 failed in 132.89s；A 原有两项 WinError 1314，详见下文；后续仅改 JSON 边界并增加 4 项回归，最终受影响 Runtime/Gateway 已复核 |
| `conda run --no-capture-output -n aica python -m ruff check packages/runtime apps/gateway` | 最终通过；初次新测试 import 排序已修复 |
| `conda run --no-capture-output -n aica python -m pip check` | No broken requirements found |
| `conda run --no-capture-output -n aica python scripts/validate_collaboration.py` | 最终记录写回后 22 tasks / 27 bindings，通过 |
| `git diff --check` | 最终记录写回后通过 |

其他命令：Get-Content、rg / rg --files、Get-ChildItem、git status/rev-parse/switch/diff、apply_patch；检查现有角色/资源映射、前端模型、后端实现和环境。没有运行 detector/IOC 命令、安装、供应商调用或公网网络配置。

真实 HTTP 使用临时 Uvicorn 和 Node SDK，完成注册、登录、JSON 上库、私有拒绝、公开读取、建分支、Fork 独立改动、过期 head 拒绝、ZIP 上传、图片读取、注销。测试结束关闭临时服务。其余新增验证覆盖账号和会话重启、密码/令牌非明文存储、跨库版本隔离、公开产物授权、并发一个赢家、存储异常事务回滚、DAG 与 Golden Path 引用、C 导出格式/CORS PATCH、超限与无效 JSON。没有浏览器 UI 端到端验收或公网部署。

## 文件、阻塞、下一步

- 修改 `apps/gateway/src/aica_gateway/app.py`、Gateway README；新增 `platform.py`、`PLATFORM_API.md`、两份 Python 测试、客户端/类型声明/两份 JS 验证文件。本任务和 `docs/STATUS.md` 记录本阶段；没有修改 A/B/C、共享 Schema、根环境、锁文件或真实凭证。
- 全量回归仍受 A 原有 `test_recorder.py::test_reference_inspection_blocks_symlink_escape_and_checks_size` 和 `test_transfer.py::test_unavailable_or_unsafe_artifacts_never_produce_a_bundle[escape]` 阻塞。两项在 `Path.symlink_to` 抛出 Windows 权限错误 WinError 1314；未跳过、未改测试、未改系统权限。最后四项 JSON/历史版本测试加入后没有重复全量 A 回归；相关 Gateway/Runtime 最终通过，不宣称全量通过。
- 本阶段后端功能完成；任务保留 handoff，待团队认领/非作者评审与具备符号链接能力的既定环境完整回归。C 接下来依照 PLATFORM_API 接入页面，用平台 UUID 替换本机 ID；OAuth、账号恢复、协作者/PR/Merge、执行与生产部署均另立阶段，不在本次继续。源码保持本地可审阅差异。

---

# D 诊断阶段：模型回复校验失败与单次真实复核

- 日期：2026-10-17，Asia/Shanghai。任务 `D-GRAPH-DIAGNOSTICS-20261017`；用户明确要求定位解决此前同一已确认外发材料的失败，沿用 D Runtime 范围。独立分支 `fix/d-graph-provider-diagnostics`，基线 `3157569046faacdb4a871f0cc281df4bccdf7905`。不改 A/B/C、Graph Schema、密钥配置或环境。
- 确定代码缺陷：ProtocolError 是 ValueError 子类，旧 broad catch 把 finish_reason 校验的截断原因及 JSON/Schema 错误均改写成泛化报错。旧回复没有落盘，因此不能事后精确确认旧次原因。
- 修复：拆分 response_envelope/response_usage/output_truncated/output_unfinished/proposal_json/proposal_schema 阶段，保留 GraphResponseError；CLI 写本地 provider-response.json（脱敏回答正文、模型/预算、finish_reason、用量、有限字段错误；无请求头或 reasoning_content），attempt 保存摘要；Graph组装失败另记 graph_validation。Schema错误不含原输入值，正文替换实际凭证；默认不重试。
- 新增显式 CLI --max-output-tokens，范围512..8192，预算在发请求前校验并记入引擎快照；省略仍沿用配置，不改凭证文件。为同一授权输入在新目录单次复核时临时用8192输出、120秒超时，未改变全局或本机配置。
- 实际一次 DeepSeek Flash 调用成功：11个候选步骤、10条语义关系、0变量，prompt_tokens=29221、completion_tokens=5771、total_tokens=34992。5771超过旧4096预算，截断是旧失败的高可信推断，仍不能冒充旧回复已恢复。真实结果仅保存本地新 diagnostic-8192 目录，不进Git。旧源档案及旧run全部文件哈希保持不变。
- 离线复核真实图通过 validate_document，所有原始节点/Run引用均空，没有伪造执行历史；25个声明附件通过哈希。审计脚本初次用默认16MiB读取限制导致大附件审计失败，随后改用既有流式哈希检查，未改附件或放宽解析预算。先前PowerShell聚合空数组计数为1，Python逐节点审计确认实际引用为0。
- 验证：新增8项合成回归，最终 semantic-tests **162 passed in22.29s**；semantic-style Ruff通过；先行专项39项通过。覆盖截断/JSON/Schema/封装分阶段、脱敏不落凭证/思维链、保存失败及用量、不重试、显式输出预算和发起前拒绝非法预算；真实模型与离线测试分开记录。
- 命令：git status/switch/diff/ls-remote；rg、Get-Content、apply_patch；既有 Conda aica 的 pytest/Ruff、具名临时单次模型脚本及只读复核脚本、协作校验。修改仅 Runtime provider/CLI/assembly、说明与测试、本任务及状态；没有安装、服务部署或自动付费重试。
- 交付：代码检查点 `237abc528936587f05048ab41033b012db0433d8`；[Draft PR #14](https://github.com/AiCA-Team/AiCA/pull/14)叠加于报告PR #13，未合并或部署。协作校验21任务/27绑定及diff-check通过。用户直接打开新生成的report.html，不需再付费解析；未来新目录可显式用8192预算。停止本阶段。

---
# D 报告阶段：本地输入材料关联原图

- 日期：2026-10-17，Asia/Shanghai。任务 `D-INPUT-IMAGE-LINKS-20261017`，沿用用户已确认 D 角色；独立分支 `fix/d-input-report-image-links`，基线 `b7d4a67f7f02868faeafe8c85884c632a17ea72b`。范围仅 Runtime 本地报告、任务及状态；未改 A/C/B 或环境。
- 输入报告新增图片缩略图与“打开原图”链接，复用 A 的本地路径及哈希校验；PNG/JPEG/WebP/GIF，单个 16 MiB 以内，远程地址不抓取，ZIP 不解包。file URI 仅用于本地 HTML，不进入模型事实或 Graph Schema。移动原项目可能使链接失效。
- 已刷新用户既有本地 input-report，14 张图片通过校验并各有缩略图和链接；旧报告另存备份。原档案、request、prepared-input、attempt 的字节哈希均未变，无模型调用。报告明确它是准备阶段快照，后续尝试状态见 attempt.json；既有模型格式失败保留，非本阶段修复项。
- 验证：既有 semantic-tests **154 passed in 21.71s**，semantic-style Ruff 通过；首次导入空行排序告警已修正。具名临时本地审计脚本核对链接目标存在、14 个图片标签和链接、CSP 仅放行本地图片及保护文件哈希。没有新增镜像实现的测试。
- 命令：git status/switch/diff/ls-remote；Get-Content、rg、apply_patch；既有 Conda aica 的 pytest/Ruff、本地报告刷新审计、协作校验。修改 report.py、CLI装配、GRAPH_CLI.md、本任务和本文件；不调用供应商、不安装依赖。
- 交付：代码检查点 `27578c1`；[Draft PR #13](https://github.com/AiCA-Team/AiCA/pull/13) 叠加于附件适配 PR #12，未合并或部署。协作校验20任务/27绑定通过；文档末尾空行已修正，最终diff-check通过。停止本阶段，用户刷新既有报告并展开图片附件查看原图。

---
# D 修复阶段：附件型 A 档案的事实准备与候选步骤

- 日期：2026-10-17，Asia/Shanghai；任务 `D-ARTIFACT-INPUT-FIX-20261017`，独立分支 `fix/d-artifact-only-graph-input`，基线 `52559a58b6121e0022e4684efb54e6b9c56793c8`。用户指定真实 `aica-light-comic` 项目，要求解决第一步没有任何数据。沿用 D 的 Runtime/Gateway（排除 B preview）资源；A 源码、用户原始文件、C 前端及环境未改。
- 已复现：原始根档案通过 A 校验，graph.nodes/runs 均空；25 个附件，5 份历史分段的事件总计 session.created 1 条、artifact.recorded 20 条，没有消息或工具开始/结束。旧准备器忽略未绑定节点的材料，返回 facts=0 却标 prepared。exports/aica.json 另有 111,964,952 字节内嵌图片；不用其 base64 作为模型输入。初次只读摘要脚本误输出嵌入内容过多，后续诊断只输出类型/计数和有界摘要。
- 修复：生产准备器索引无 producer 绑定的创作附件，哈希校验后读取累计 96 KiB 以内的文本/JSON/Markdown；二进制只带元数据。附件内容缺失/超限保留 issue，不假装已读取。准备零事实给 NO_ANALYSABLE_CONTENT，CLI preview 与 CLI/API 发起模型时拒绝 analysis_empty；不会以空输入调用模型。
- 材料可锚定 source_node_ids/run_ids 为空的候选语义步骤，以 archive 证据及登记 artifact_ids 关联；ARTIFACT_ONLY_SOURCE 强制披露执行来源缺失，未知附件不能冒充 user/agent 消息、原始节点、实际 Run 或 verified 依赖，缺乏执行参数时不生成变量。未关联材料保留 UNASSIGNED_ARTIFACT。Schema/TS 的四个消费文件字节不变，C 说明升至 r3 明确“关联材料”和真实产出的区别。
- CLI 第一步生成 input-report.html，显式区分输入检查与 LLM 拆解，优先展示已读正文并列出材料/正文/原始节点计数；Evidence API 对文本/JSON 证据额外提供有界脱敏 content/content_state。没有公网部署或前端改动。
- 真实本地复核：同一个根档案从 **0 项事实 → 20 项材料事实，5 份正文**；准备后 JSON 97,692 字节，原始 aica.json 与修复前准备哈希一致，25 个声明附件均通过原哈希复核，四份冻结契约不变，实际模型凭证在 Runtime/Gateway 文件匹配为 0。真实预览目录为 checkout 同级 `light-comic-ready-20261017T062900Z`，没有将其内容提交 Git或发送 DeepSeek，模型调用为 0。
- 验证：新增 11 项合成 Runtime/API 回归；最终既有 semantic-tests 命令 **154 passed in 21.94s**，Ruff semantic-style 通过。覆盖附件材料准备、HTML 输入报告、附件锚点及同一材料支持多个独立步骤的身份稳定性、空输入、来源/角色/假节点防护、未关联材料及标准上传→持久化取图→读取正文证据。没有重复 A 全量系统权限测试；既有 symlink 环境限制保持原记录。
- 命令：git status/branch/switch、rg、Get-ChildItem、读取既有实现、apply_patch；既有 Conda aica 运行真实 preview、临时具名只读摘要/哈希审计、pytest/Ruff。一次多段 patch 因重复目标被工具拒绝、一次文档上下文不匹配，均在写入前校正；空输入测试初版先触发源目录输出保护，调整合成目录后验证真实空输入错误码。无安装、升级、创作工具调用、真实材料外发、生产配置或系统权限修改。
- 交付：代码检查点 `c8ed7777553e25da126c05c634b1e9bcde6f308f`；修复分支已推送，创建 [Draft PR #12](https://github.com/AiCA-Team/AiCA/pull/12)，叠加于 CLI PR #10，经解析器 PR #9 进入 D。未合并或部署。协作校验 19 任务/27 绑定及 diff-check 通过。
- 后续：用户打开新预览检查内容，并显式同意外发后运行 parse；旧 facts=0 的准备目录不可继续沿用。精确调用历史仍需 A 提供节点/Run/消息，不能用附件推断冒充录制。当前保存评审交付后停止本阶段。

---

# D 本地测试入口阶段：自有 A 档案 → Graph 与可读报告

- 日期：2026-10-17，Asia/Shanghai；任务 `D-GRAPH-CLI-20261017`，分支 `feature/d-graph-parser-cli`，基线 `fab4302`。用户准备自行输入 A 处理后的档案测试拆解，现有入口仅 API、旧探测脚本固定为合成输入；本阶段补通用本地入口，不扩展平台功能。
- 新增 `python -m aica_runtime.graph preview INPUT --output NEW_RUN_DIR` 与 `parse NEW_RUN_DIR --confirm-external`。支持 A JSON/项目目录/.aica 目录/标准传输 ZIP，复用 A 的附件及传输校验和既有生产解析；仅读取原始档案，不启动录制器或改源数据。输出目录不得在源项目或 Git 中，已有目录不覆盖。
- preview 不调用模型，保存可审阅输入和本地身份；parse 比较原数据与审阅哈希后最多调用一次模型，保存 attempt/provider-result/graph。重复尝试被拒绝，失败/中断保留可核实状态。图身份是本地试验命名空间，不声称已上平台。
- 新增安全转义的静态 HTML 报告，内含 SVG 关系图、步骤详情、原始调用/Run、参数与当前值、消息证据和 coverage；无脚本或外部资源，可双击本地打开。使用说明：[GRAPH_CLI](../packages/runtime/GRAPH_CLI.md)。未改变 GraphDocument Schema、解析算法或 C 前端。
- 验证：新增 9 项 CLI 检查；最终既有 semantic-tests 命令 **143 passed in 20.88s**；semantic-style Ruff 通过。覆盖标准包/项目输入一致、源字节不变、审阅/权限、重复付费保护、失败保存、HTML 注入转义、历史预算。真实 `python -m ... --help` 与合成档案 preview 命令通过；没有新增模型调用或使用真实用户数据。上阶段的 A 全量 Windows symlink 限制未重复测试或改写。
- 离线使用此前保存的合成实测图生成 `deliverables/graph-report-example-20261017-134124.html`（checkout 同级）；这是展示示例，不是用户新档案结果。命令冒烟的输出在 checkout 同级 `cli-preview-smoke-20261017T054231Z`。均不进入 Git。
- 命令：读取现有 CLI/协议/任务，git switch 独立分支，apply_patch；既有 Conda aica 环境执行 pytest/Ruff/模块命令；临时具名 Python 仅离线渲染报告，未安装依赖或启动服务。改动仅 Runtime、任务与本状态记录；A/B/C、Gateway、环境和源档案无改动。后续由用户按文档输入自己的路径并明确外发后测试；本阶段交付后停止。
- 交付：实现检查点 `1d0e9ef457ff3ea0478f3a34939637c8fde7a390`，分支已推送，创建 [Draft PR #10](https://github.com/AiCA-Team/AiCA/pull/10)，叠加于 PR #9 的解析器分支，后续按评审流程进入 D。18 任务/27 绑定协作检查和差异检查通过；未合并或部署。后续提交仅完善交付元数据，本阶段到此停止。

---

# D 实现阶段：通用 Graph 解析、持久化与 C 读取接口

- 日期：2026-10-17，Asia/Shanghai。任务 `D-GRAPH-PARSER-20261017`；用户在接受协作文档后明确授权本阶段实现。独立分支 `feature/d-graph-parser`，基线 `ad8a1ce`，集成目标仍为 `feature/d-backend`。D 可写 Runtime、Gateway（排除 B preview）、本任务和 phase-status；无 A/C/B 或环境文件修改。
- 生产实现：`packages/runtime/src/aica_runtime/graph/` 包含唯一契约、A 档案/消息准备器、对话与执行索引、DeepSeek 提议、确定性图组装和校验；原设计 contract/validation 测试文件改为兼容导入。`apps/gateway/src/aica_gateway/graphs.py` 提供异步有界解析、SQLite 原子持久化、幂等与重启恢复；app 装配准备/提交/状态/列表/精确取图/证据及提供者状态路由；Repository 增加固定导入原字节读取。Graph 0.1 的三份消费 Schema 和生成 TS 保持字节不变。
- 接入文档：[GRAPH_PARSER](../packages/runtime/GRAPH_PARSER.md)、[验证记录](../packages/runtime/GRAPH_PARSER_VALIDATION.md)。给 C 的协作说明更新为 r2，明确已实现和规划接口，含实际 JSON 包装和状态路径；A 原始 `/graph` 和旧 T018 报告 API 保留。新增解析不改原档案或导入 head，每个新请求保存独立 draft/executable=false 图，LLM 解释全部候选。
- 最终专项：`conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests` → **134 passed in 23.20s**（新增 33 项）；`python -m ruff check packages/runtime apps/gateway` 通过。首次实现点完整 semantic-regression → 235 passed、2 failed in 113.59s；失败均为 A 原有 symlink 测试创建链接时 WinError 1314，未改测试或系统权限。之后仅细化参数定位、提示方向和交付说明，最终专项覆盖改动，不声称全量全绿。
- 真实联调：具名 `run_graph_parser_e2e.py --confirm-external --root OUTSIDE_NEW_RUN_DIR`，只用合成 A 记录，一次真实 DeepSeek HTTPS 调用；4 步、6 变量、1 条 verified 依赖，9906 tokens，原档案不变、同请求去重和重启读回通过。API transport 是进程内 TestClient，未启动公网服务。完整安全结果：[graph-parser-live.json](../packages/runtime/tests/evidence/graph-parser-live.json)。本次运行目录为 checkout 同级 `AiCA-graph-parser-live-20261017T050300Z`，私有数据库不进 Git。
- 语义复核：真实候选中的一条 continues 方向及混合主体解释仍需审核；最终 prompt 0.1.1 明确关系方向，没有再次模型调用或声称已验证修复。结构/角色引用校验不替代自然语言审核。准备器排除系统消息及内部凭证，并处理 A 实际保留事件名 session.created。
- 其他命令：按清单读取 Git/协议/资源和既有测试入口；使用 apply_patch、Copy-Item、Set-Content 实现文件迁移与合成证据复制；git switch 新任务分支、ls-remote 确认无冲突并核对 A 交付、diff 核对 A 源码无变。冻结文件、证据、凭证、链接、协作和差异检查在交付记录登记。没有安装、升级、改系统/网络/生产配置、执行创作工具或向 C 发送消息。
- 已完成额外核验：17 任务/27 绑定协作检查通过，git diff/暂存差异检查通过；84 个文件的实际模型凭证匹配为 0；四份冻结契约、文档本地链接、真实结果与原始源字节验证通过。原始真实结果 SHA256 为 `66c6bf65a122c8e7aa5384fbd86b35ce67ccc977069cdd149f5ea10020c65fcc`。
- 本阶段解析与读取验收完成，代码检查点 `3bfa005b56a346b8cb1b985666a052f959cb79c8`；后续仅登记交付元数据。已推送 `feature/d-graph-parser`，创建并核对目标为 `feature/d-backend` 的 [Draft PR #9](https://github.com/AiCA-Team/AiCA/pull/9)，未合并。PR 包含尚未合并的 PR #8 设计基线，供一起评审。C 实际画布联调、公网部署、语义编辑/布局及平台登录/Fork/多分支仍未完成。下一步非作者评审与 C 接入；按单阶段要求，本轮到此停止业务推进。

---

# D 单阶段交付：供 C 接入的 Graph 0.1 协作文档

- 日期：2026-10-17，Asia/Shanghai。用户要求确定 Schema 格式并整理成可转交 C 的文档；沿用 `D-GRAPH-DESIGN-20261017` 与独立分支 `feature/d-semantic-design-discussion`，本轮仅完成文档交付。
- 更新 [C_HANDOFF.md](../packages/runtime/design/graph-v0.1/C_HANDOFF.md)，版本 `D-C-GRAPH-0.1-r1`：固定 D 侧 0.1 消费格式，逐项说明 GraphDocument、Step/Claim、data/control/semantic、Variable、Evidence/Coverage、语义编辑与布局；含 10 段完整 JSON、C 字段映射、接口规划、冲突处理及接收清单。C 接受仍待确认，规划接口尚未实现/部署。
- 制作 `AiCA-Graph-C-v0.1-20261017-124240.zip`，输出在 checkout 同级的本次 deliverables 目录。包内 21 个文件含中文文档、3 份消费 Schema、生成 TS、三套合成记录/语义图、编辑/布局请求、A 权威 Schema 参考、入口和逐文件 MANIFEST；不包含密钥、数据库或真实用户日志。ZIP SHA256：`15f79494cb48ff417b8315f4fb4b17c2a870a1c64eb9c27868077b9836ac4f7b`。
- 验证：10 段 JSON 经现有 Pydantic 类型校验；单步骤文档示例与三套包内完整图经既有语义校验；ZIP CRC、全部文件长度/哈希、文档本地链接及原始样例字节通过。4 个消费契约文件与本轮基线 `9d502bb` 字节一致。未重跑业务测试，既有 101 passed 为上一阶段历史结果；无新增模型调用或服务启动。
- 命令：Get-Content、rg、git status/rev-parse/diff；临时具名 Python 脚本经既有 `conda run --no-capture-output -n aica python` 组装文档、校验及打包；`python scripts/validate_collaboration.py` 通过（16 任务/27 绑定），`git diff --check` 通过。修改文件仅本说明、C_HANDOFF.md 和原任务 JSON；Schema、生成类型、业务代码和 A/B/C checkout 均未修改。
- 下一步：C 根据样例对接并回传接收清单；再进入独立的新版解析服务/API 实现与联调阶段。本轮不合入或部署，完成文档交付后停止。

---

# D 两小时设计阶段：Graph 解析方案与格式 0.1

- 日期：2026-10-17，Asia/Shanghai；本次从 11:42 开始，任务 `D-GRAPH-DESIGN-20261017`，单阶段范围为技术方案、派生接口格式与可运行契约验证。分支 `feature/d-semantic-design-discussion`，基线 `78ff5fa`；最终集成目标仍为既有 `feature/d-backend`。
- 用户已澄清 RAG 只是外部辅助组件的例子，不能把它当作预定路线；已确认默认“创作步骤为主，可展开内部调用”。第一版复用现有 Python/Pydantic/jsonschema/DeepSeek，采用事实关联、对话状态容器、LLM 提议、程序组装和约束校验；Embedding/专用模型/视觉分析按评估问题扩展，不作为前置依赖。
- D 交付目录：[Graph 0.1](../packages/runtime/design/graph-v0.1/README.md)，包括算法规格、GraphDocument/ModelProposal/编辑/布局 Schema、同源生成 TypeScript、三套 A 合成记录与语义图、给 C 的字段映射与候选 API，以及验证记录。契约实验代码仅在 runtime tests 中，现有生产 Runtime/Gateway 不导入它们；A/B/C 源码及环境文件未修改。
- 图格式保留到原始事件、节点、Run、产物和精确快照的映射；解释带角色/主体/证据/审核状态。显式 data/control 与 semantic 关系分开，程序保留 A 真实绑定并检查聚合后的执行 DAG。变量值从原始定位读取，不由模型编造。语义修订与布局版本分别设计，新解析不覆盖用户已编辑修订。本版固定 executable=false，不声称已实现重放。
- 实际 DeepSeek 两次调用均只发送合成事实：第一次分组为 4 步，但因执行/语义边字段混用被拒绝，5622 tokens；改为判别联合格式后，第二次 4 步/1 条显式数据依赖/4 变量/无未归组节点，契约通过，5849 tokens。总计 11471 tokens、自动重试为 0，未执行生图或音频工具。原始失败、请求、响应、用量和第二次离线规范化派生图均保留，不以两个样例承诺准确率。
- 验证：`a2ed3a889f11392ed6d80e00c5acc2bfc162e0d5` 的专项 101 passed in 14.37s（新增设计检查 50 项），Ruff 通过。`943cfed` 的 TS 严格编译通过，后续生成 TS 未变；协作检查 16 任务/27 绑定通过。测试覆盖历史执行必须报告范围及磁盘原始样例字节哈希；样例局部 .gitattributes 将 text 置 unset，避免 Windows 换行改变证据。没有重复 A 全量录制回归，既有 Windows 符号链接限制未被改写为已解决。
- 过程异常：早期两处 import 排序已修正；并行 Conda 激活出现共享临时文件冲突，已改为串行，不改环境；初版样例时序在冻结前修正；真实探测器最初用 live-probe 标注组装图模型，原记录保留，实际模型元数据通过离线派生图修正，无额外模型调用。
- 实际命令：只读 Git/文件发现及现有 gh 用户/仓库元数据检查；新增导出器与 probe 的具名命令见 [VALIDATION](../packages/runtime/design/graph-v0.1/VALIDATION.md)；检查采用既有 semantic-tests、semantic-style、collaboration-validation、diff-check，并只读使用既有 C 的 tsc。没有安装、升级、启动服务、修改生产配置或合入共享分支。
- 本阶段设计候选完成并进入评审，代码检查点 `a2ed3a8`；元数据后续提交仅更新说明与记录。已推送 `feature/d-semantic-design-discussion`，并创建目标为 `feature/d-backend` 的 [Draft PR #8](https://github.com/AiCA-Team/AiCA/pull/8)，已核对 open/draft/分支身份，未合入或部署。交付完成于 12:14，距本阶段开始约 32 分钟。当前格式为 D 基线，C 尚未确认；新增端点、账号/仓库/Fork/branch 服务和完整解析状态机仍未实现。下一步评审本契约并由 C 确认映射，再另立解析服务接入阶段；本轮到此停止。

---

# D 设计讨论：混合语义解析与 Graph 对接边界

- 日期：2026-10-17，Asia/Shanghai。本轮仅做实现前的设计讨论；用户明确要求先讨论语义解析模块。没有开始新的业务实现、调用付费模型、启动服务或推送代码。
- 本地讨论分支 `feature/d-semantic-design-discussion`；基线 `78ff5fabe67e1f7d592fcfcaf7eb0fb9add7df87`。沿用用户明确的 D 职责；本轮唯一写入为 phase-status（本文件），不修改 A 的 archive-contract/capture-reconstruction 或 C 的 graph-application。
- 用户本轮确认的边界：A 提供既有契约下的结构化记录；D 负责语义解析、派生 Graph 接口与平台后端；C 负责画布展示与编辑。最终实现交付目标为已有 `feature/d-backend`，本轮不向该分支合入内容。
- 用户已确认默认节点粒度为“创作步骤为主，可展开内部调用”。步骤保留到原始消息、事件、Run、产物的映射；不能把每轮对话强制等同于一个步骤。

## 待讨论方案与现有证据

建议采用确定性事实整理、步骤与候选关系发现、受限检索、LLM 语义解释、结构与证据校验、可审核派生版本的处理链。检索源包括当前项目与指定版本的历史、版本化工具定义及已审核解析样例；参考知识与实际事件证据分别标记。工具调用关联、参数实际值和 Run 状态优先依据记录解析；意图、步骤聚合、跨轮指代及变量用途由模型提出候选。

原始 A 档案保持独立；D 的语义步骤可映射多个原始节点和调用。语义关系与可执行 data/control 依赖分开，只有明确输入输出绑定且通过校验的依赖参与执行 DAG。试错路径不能自动当作平台 branch；平台 branch 指向不可变的已提交图版本。前端布局与语义版本分别管理，编辑携带预期版本并返回冲突，后续解析需保留用户修订。

已读现有 contracts.py、evidence.py、semantic.py 和 T018/T019 记录。现有原型已提供事实引用、独立报告和结构校验；T019 保存的模型归因偏差说明“引用存在”不能证明解释正确。现有 Step 逐个引用原始 node_id，仍需设计跨调用步骤聚合、字段级归因、纠错及语义图提交接口。只读核对集成 checkout 的 C README、graph-model.ts、auth.ts 和 ABCD-INTEGRATION.md；其中账号与部分社区/图数据仍标记为本地演示，本轮不改动该 checkout。

## 命令、检查与下一步

- 实际命令：Get-Location、rg --files、rg -n、Get-ChildItem、Get-Content；对相关 checkout 执行 git status --short、git branch --show-current、git remote -v、git log；D checkout 执行 git branch -avv、git status --porcelain、git switch -c feature/d-semantic-design-discussion、git rev-parse HEAD、git diff --check、git diff --stat。使用 apply_patch 更新本文件。
- 探索异常：工作区父目录不是 Git 仓库，改为各已发现 checkout 检查；一次误写 ROLE_ONBOARDING.md，随后按清单读取正确的 ROLE-ONBOARDING.md；一次 Windows 路径通配搜索失败，不以失败输出支持结论。
- 外部只读资料：RAG 原论文 https://arxiv.org/abs/2005.11401 ，Microsoft GraphRAG 索引说明 https://microsoft.github.io/graphrag/index/overview/ ，长上下文研究 https://aclanthology.org/2024.tacl-1.9/ 。这些资料用于方案参考，不是本项目解析效果的验证。
- 检查：git diff --check 通过；只改本文件。业务测试 not_run，原因是本轮没有业务实现；现有测试结果均为历史记录，没有重跑或将其作为新方案的验收结果。
- 待定：步骤拆分/合并与稳定 ID 规则、字段级证据及归因契约、编辑提交与解析重跑的合并策略、C 当前页面所需的平台 API。新方案效果尚未验证，没有冻结正式 Schema 或承诺解析准确率。
- 下一步：继续与用户讨论候选方案；达成设计共识后，在独立阶段交付语义契约、正反样例与评估集，再分别实现解析及平台能力。按用户要求，本轮到此停止业务推进。

---

# D 实测阶段：真实生图经 MCP 录制、解析并入库

- 日期：2026-10-17，Asia/Shanghai；真实平台核验完成于 09:49:23。
- 任务 T019；分支 `feature/d-mcp-image-e2e`；基线 `320904e`；脚本及证据提交 `1b96df5b00be7eb10a08d04c592c6750b3138d15`。
- 用户明确要求实际测试图像生成任务经 MCP 录制及语义解析入库，并禁止编造结果。本阶段仅做该真实链路验证，保持生产源码、A/B/C 和环境配置不变。

## 实际执行与数据库结果

1. 使用既有 aica 环境及已安装的 aica-mcp，经 MCP SDK 真实 stdio initialize/list_tools 成功，返回 11 个工具。建立授权 Session、Harness、Agent、节点和 running Run。
2. 本聊天实际调用内置 image_gen.imagegen，提示词由测试 Host 选定：黑猫宇航员站在月球，地球背景，暖色水彩，方形无文字。真实返回 PNG，并复制到本次源项目；没有调用模拟生图工具或使用合成工具返回。
3. 通过 aica_capture_artifact 复制及哈希实际 PNG，再提交真实结束事件和导出。图片 3,369,178 字节；SHA256 为 `eb999a2de4f331bc27e589903dbd54879d4e291aae5155caf27f649f6c16c9c5`。快照 complete=true、gaps=[]，Run=succeeded。
4. 启动真实本机 Uvicorn HTTPS 服务，显式信任本次生成的本地证书，使用 A 的 build_bundle / upload_bundle 导入档案及产物。未关闭证书校验。
5. 预览脱敏事实并提交匹配的 input_sha256，使用真实 DeepSeek flash，调用一次，取得 1 个步骤、2 个变量（prompt/string、transparent_background/boolean）和有成功 Run 证据的单节点路径建议。prompt_tokens=3199、completion_tokens=758、total_tokens=3957。
6. 直接只读查询 SQLite，实际 projects/imports/receipts/semantic_analyses 各 1 行；分析状态 succeeded，result 与 API 查询值相同，integrity_check=ok。同请求重试仍是同一记录。
7. 关闭并重新启动后端，结果和图片仍可通过独立 HTTPS 请求读取，图片哈希相同，原始档案未修改。本次启动的 MCP 协调器及两次后端服务已停止，数据库和产物保留。

| 身份 | 实际值 |
| --- | --- |
| project_id | e8f95641-6347-4c91-b629-883a602c89a6 |
| import_id | eacff730-32fe-4179-baad-a1aab63f3a46 |
| analysis_id | 2eb9065b-f4ef-44d1-9a1c-05e5a9737796 |
| run_id | d456d4ee-7543-49df-a093-dc4bc5045f10 |

## 证据、命令和边界

- 仓库内核验摘要：[mcp-image-live.json](../packages/runtime/tests/evidence/mcp-image-live.json)，记录实际 SQLite 状态、模型用量、结果及复核备注。
- 本次运行目录位于 checkout 的同级 `AiCA-live-image-20261017T014707Z`。可读报告：[REPORT.md](../../AiCA-live-image-20261017T014707Z/REPORT.md)；实际数据库：[platform.sqlite3](../../AiCA-live-image-20261017T014707Z/platform/platform.sqlite3)。该目录是本次运行事实，不是团队固定路径配置。
- 该目录还保留真实 PNG、aica.json、录制数据库、MCP 回执、传输包、API 结果、独立只读检查脚本和模型尝试记录。目录 ACL 限当前用户和 SYSTEM；会话凭证、TLS 私钥及原始数据库不进入 Git。无需重新调用模型即可查询现有数据库。
- 复核 SQL：`SELECT id, project_id, import_id, state, engine, result FROM semantic_analyses;`。独立 inspect_database.py 实际执行并保存 database-inspection.json。

以下 Python 命令均经 `conda run --no-capture-output -n aica` 运行。RUN_DIR 表示上文实际运行目录，IMAGE 表示本聊天真实工具返回的 PNG；不是虚构占位执行。

| 命令 | 实际结果 |
| --- | --- |
| python packages/runtime/tests/run_mcp_image_e2e.py begin --root RUN_DIR --confirm-record | 真实 MCP 11 工具，开始 Run |
| 内置 image_gen.imagegen，参数见 RUN_DIR/prompt.json | 返回实际 PNG，未请求透明背景 |
| python packages/runtime/tests/run_mcp_image_e2e.py complete --root RUN_DIR --image IMAGE --confirm-record | 图片归档、哈希、结束 Run、导出成功 |
| python packages/runtime/tests/run_mcp_image_e2e.py platform --root RUN_DIR --confirm-external | 真实 HTTPS 上传、模型、SQLite 及重启验证通过 |
| python RUN_DIR/inspect_database.py | 独立只读 SQLite 查询，取得真实模型意图、步骤、变量及成功 Run |
| python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests | 51 passed in 6.99s |
| python -m ruff check packages/runtime apps/gateway | All checks passed |
| python scripts/validate_collaboration.py | 15 项任务、27 个绑定，Schema/引用/DAG 通过 |
| python RUN_DIR/audit_delivery.py | 本次 DeepSeek/Session 凭证匹配为 0；自建录制协调器已停止 |
| git diff --cached --check | 脚本、摘要及最终状态差异通过 |

脚本最初 Ruff 报一处未使用 transfer 变量，已改为保存真实 bundle 校验结果后通过。独立 SQL 首次尝试经 Windows Conda 多行 -c 返回空输出，不用该空输出作证据；改为具名脚本执行，实际输出并保存查询结果。未因这些问题重跑付费模型或生图。

本次 MCP 是 Host 显式转交实际调用事件，没有安装自动监听 Hook，也没有改客户端全局 MCP 配置。档案 manual 表示缺少可移植执行绑定，不代表支持平台重放。本机测试不等同于双机、公网或生产部署验收。

模型仍有语义偏差：将 Host 选定的黑猫题材表述为“用户希望”，存在归因过度。原始模型输出和数据库保持原样，另存 review_notes，报告继续为 inferred/draft/requires_review。链路通过不表示语义完全正确，也没有执行路径回放。

新增 run_mcp_image_e2e.py 和核验摘要，更新 Runtime README、T019 及本状态记录。本次未修改生产代码，未重复全量回归；上一阶段 154 passed、2 个 A 原有 WinError 1314 失败仍保留，T017/T018 状态不变。T019 仅进入本机测试证据评审；没有推送、合入或部署。下一步是非作者复核本次证据和归因偏差；自动捕获与报告确认另立阶段。本阶段到此停止。

---

# D 第二阶段：基于 A 事实的 DeepSeek 语义分析

- 日期：2026-10-17，Asia/Shanghai。
- 任务 T018；分支 `feature/d-semantic-analysis`；实现提交 `11efbc10bbd9b7f1283d29b5b7e7562a28958c03`；阶段基线 `dfa804332704446789394da10c9a04c3f520919c`。
- 用户明确将语义解析交给 D，并授权使用所提供的 DeepSeek 服务、由本会话选择模型。继承最初 A 快照上的独立 D 实现，没有接管其他 D 分支。
- 本阶段实现完成，专项 51 项通过，真实模型合成样例联调通过；完整回归 154 passed、2 failed。失败仍是下文第一阶段已在原始 A 快照复现的 Windows 符号链接权限问题。T018 保持 blocked，尚未进入评审、合入或 Demo 验收。

## 本阶段实现

复用 A 的 `aica_schema` 和真实日志提取器，固定平台导入快照，整理节点、Run、产物及消息事件。事实包含 JSON Pointer 或事件附件/行号、原内容摘要；解包字面量及 JSON 字符串参数，保留输入绑定。常见凭证、Bearer、签名 URL 脱敏，排除 system 消息和明确的私有推理字段，标明缺失历史、录制完整性与子图范围。输入有节点、事实、字节及历史读取上限。

调用前通过 API 返回实际输入及 SHA256，提交必须匹配审阅哈希并明确允许外发。DeepSeek 输出创作意图、步骤用途、候选 data/control/semantic 依赖、可开放变量和成功路径建议；服务端校验证据、节点、变量实际值及类型、组合依赖无环性。Golden Path 需要当前修订、active 分支、按时间确定的最新成功 Run，且引用成功记录、依赖闭合和拓扑有序。

结果作为独立报告保存，所有解释为待审核推断；引用存在不等于模型解释正确。报告不会修改原档案或直接成为可执行 Graph。平台提供输入预览、模型配置状态、分析提交、列表和详情，沿用主体/项目权限。持久化请求身份、运行状态及错误，单项目一次分析，同请求重试复用结果；网络不确定或重启标 unknown，不自动重新调用模型。

本阶段不实现报告确认写回、Fork、缓存、执行调度或前端。A 的源码、Schema 和测试，B/C 模块，以及根环境约束均未修改。接口和操作说明见 [Runtime README](../packages/runtime/README.md)。

## 模型配置和真实联调

- 真实查询官方 `/models`，返回 `deepseek-flash`、`deepseek-v4-pro`，本机配置选择 `deepseek-flash`。
- 凭证位于仓库外 `%LOCALAPPDATA%/AiCA/secrets/semantic-provider.json`；Windows ACL 禁止继承，仅当前用户及 SYSTEM。源码、差异和非忽略文件检查未发现本次凭证。不把密钥值写入文档、任务或测试快照。
- 使用官方 HTTPS Chat Completions 和 JSON Output，关闭 thinking，不请求工具或保存 reasoning_content。当前 max_tokens=4096、timeout_seconds=60，无自动重试；证书校验开启，不跟随重定向或继承环境代理。
- 一次真实 DeepSeek 调用使用 A 提取器生成的合成“黑猫宇航员角色图 → 月球分镜”录制事实，完成打包导入、输入审阅、分析、保存查询及同请求重试，原档案保持不变。
- 实际结果：3 个步骤、3 条候选依赖、6 个变量；prompt_tokens=2631、completion_tokens=2022、total_tokens=4653。三条依赖分别描述同一角色图到分镜的 data/semantic/control 建议，不是三组不同节点关系。
- 样例没有成功 Run，Golden Path 返回空数组并说明证据不足。未执行生图工具，未发送真实项目日志或私有附件。[真实结果快照](../packages/runtime/tests/evidence/deepseek-live.json) 明确标记 `real_model_on_synthetic_facts`。
- 真实调用后补充的 URL/标识符脱敏由最终专项测试覆盖；不为更新检查点重复付费调用模型。

## 本阶段命令及验证

以下 Python 命令在仓库根目录通过 `conda run --no-capture-output -n aica` 执行，沿用已创建的环境，第三方依赖版本不变。Conda 激活串行启动。最终专项、回归及静态检查的业务源码与上述实现提交一致。

| 命令 | 实际结果 |
| --- | --- |
| python -m pip install --no-build-isolation --no-deps -e packages/runtime -e apps/gateway | 成功，仅安装/更新两个本地包 |
| python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests | 51 passed in 9.94s |
| python -m pytest -q -c apps/gateway/pytest.ini packages/schema/tests packages/mcp-adapters/tests packages/runtime/tests apps/gateway/tests | 154 passed、2 failed in 85.87s，原有 WinError 1314 |
| python -m ruff check packages/runtime apps/gateway | All checks passed |
| python -m pip check | No broken requirements found |
| python scripts/validate_collaboration.py | 14 项任务、27 个绑定；Schema、资源/命令引用及 DAG 通过 |
| python packages/runtime/tests/run_live_deepseek.py --confirm-external --output packages/runtime/tests/evidence/deepseek-live.json | 真实模型合成样例联调成功，4653 tokens |
| git diff --cached --check | 实现及最终记录的暂存差异通过 |

普通测试使用 HTTPX MockTransport，真实模型调用脚本不被 pytest 自动收集。专项覆盖证据/参数校验、依赖环、最新失败覆盖旧成功、成功路径约束、脱敏、输入分享授权、哈希冲突、项目隔离、并发幂等、失败与重启。前阶段真实本机 HTTPS 上传/读取测试仍包含在专项中。

额外只读检查：加载私有配置但不打印凭证的临时脚本扫描 Git 跟踪及非忽略文件和差异，凭证匹配为 0；真实结果无 reasoning_content 字段。检查 ACL 继承关闭及两条限定授权。对比最初 A 基线，A/B/C、根环境及原始档案资产差异为空。只读模型清单探测和审计脚本位于系统临时目录，完成后清理。

## 本阶段文件、阻塞和下一步

- 新增 `packages/runtime/pyproject.toml`、README、`src/aica_runtime/{__init__,contracts,evidence,semantic,deepseek}.py`、合成事实及单元测试、可显式运行的真实联调脚本和结果快照。
- Gateway 新增 `semantics.py`、`test_semantic_api.py`，更新 app 路由、包声明、pytest 路径和 README。
- 协作记录更新 `collaboration.json`、`tasks/T018.json`、`docs/STATUS.md`，T017 保留原状态。
- 必需 semantic-regression 仍 failed：两个 A 原有符号链接测试在当前 Windows 抛出 WinError 1314；不跳过测试、改 A 测试或更改系统权限。
- 下一步：在具备符号链接能力的既定环境补齐完整回归，再做非作者评审和集成。报告确认写回及执行编译另立阶段。本阶段到此停止；仅有本地提交，没有推送或部署。

---

# D 第一阶段：平台档案导入、存储与读取

- 日期：2026-10-17，Asia/Shanghai。
- 任务 T017；分支 `feature/d-platform-import`；实现提交 `3b5b419036d8417c27cae2264b7d9c5d8cc9254f`。
- 基线为 A 的 `57b02f069d7974528afa58cea6151f7b646dc0da`。用户确认 D，明确选择独立实现并授权按既定配置创建环境；没有接管已有 D 分支或 T005/T006。
- 本阶段代码完成，D 专项测试通过；全量回归受两个已在原始 A 快照复现的 Windows 权限失败阻塞。T017 保持 blocked，未标记 in_review / integrated / demo_verified。
- 其他成员分支已使用 T013–T016，本独立任务采用 T017。

## 实现和边界

复用 A 的传输 Schema 和 verify_bundle，保留档案原始字节、ID、源项目身份、URI 和哈希。提供平台主体认证、项目隔离、流式包接收、全部附件校验、事务发布、幂等回执、历史快照、Graph 和真实产物读取。文件与数据库通过单实例锁、暂存、原子移动及提交后可见性配合；重启后可以继续读取。

本阶段没有 Runtime、Fork、缓存、Golden Path 或执行调度。B 预览与 C 前端未修改。源电脑目录不参与平台读取；不会请求档案外部 URL 或执行上传脚本。启动、CORS、API 和剩余生产边界见 [Gateway README](../apps/gateway/README.md)。

## 环境安装及命令记录

新建既定 Conda aica：Python 3.12.15、Node.js 24.21.0、Git 2.56.0、SQLite 3.54.1（满足原约束 libsqlite>=3.51.3）。environment.yml、requirements.txt、根 pyproject.toml 未改动。

- conda env create --file environment.yml 最初在 TUNA repodata 报 TLS EOF；单次禁用 zstd 和系统 truststore 重试仍失败。
- curl、urllib3、Requests 探测定位到该进程继承系统代理的连接问题。最终在系统临时目录的 Python helper 中调用原 conda.cli.main，仅该进程设置 requests.Session.trust_env=False、CONDA_REPODATA_USE_ZST=false，成功创建同一环境。证书校验保持开启，源和版本不变，未修改系统代理。stdin helper 不兼容 Windows multiprocessing，改为带 main guard 的临时文件后解决。
- conda run --no-capture-output -n aica python -m pip install --no-build-isolation --no-deps -e packages/schema -e packages/mcp-adapters -e apps/gateway：成功，仅安装三个本地包。
- 并发 Conda activation 曾碰到临时文件竞争，后续改为串行启动。首次 Ruff 仅修正新测试的两处 import 排序。

以下 Python 命令均在仓库根目录通过 conda run --no-capture-output -n aica 执行。验证时的业务源码与上述实现提交相同。

| 命令 | 实际结果 |
| --- | --- |
| python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests | 20 passed in 6.95s |
| python -m pytest -q -c apps/gateway/pytest.ini packages/schema/tests packages/mcp-adapters/tests apps/gateway/tests | 123 passed, 2 failed in 71.18s，失败原因见下文 |
| python -m ruff check apps/gateway packages/schema packages/mcp-adapters | All checks passed |
| python -m ruff check apps/gateway | 最终专项检查通过 |
| python -m pip check | No broken requirements found |
| python scripts/validate_collaboration.py | 13 项任务、27 个绑定；Schema、资源/命令引用和任务 DAG 通过 |
| aica-gateway --help | 本地安装的 CLI 入口通过 |
| git diff --cached --check | 新增实现暂存差异通过 |

真实 HTTPS 测试启动本机临时 Uvicorn TLS 服务，显式信任临时证书。A 的 upload_bundle 上传两次取得相同回执；删除源目录后，独立 HTTP 客户端仍能读取平台 Graph 和原图。未跳过证书校验。专项测试另覆盖权限、并发、落库失败回滚、历史隔离、损坏、上传限制和重启。本机回环测试不代表双机或公网验收。

## 回归阻塞

- packages/mcp-adapters/tests/test_recorder.py::test_reference_inspection_blocks_symlink_escape_and_checks_size
- packages/mcp-adapters/tests/test_transfer.py::test_unavailable_or_unsafe_artifacts_never_produce_a_bundle[escape]

两项均在 Path.symlink_to 处抛出 OSError: [WinError 1314] 客户端没有所需的特权。在用户最初提供的、未修改的 A 快照中使用相同环境单独复现，结果为 2 failed in 0.71s。复现采用 PYTHONDONTWRITEBYTECODE=1 和 -p no:cacheprovider，没有向原快照写入测试缓存。

未降低检查、跳过失败、修改 A 测试或系统权限。所需 gateway-regression 检查仍记录 failed。

## 文件和交接

- 新增 apps/gateway/README.md、pyproject.toml、pytest.ini、src/aica_gateway/{__init__,app,cli,repository}.py、tests/{conftest,test_archive_api,test_https_roundtrip}.py。
- 协调记录：collaboration.json、tasks/T017.json、docs/STATUS.md。
- git diff 对比 A 基线确认：A 的源码/测试、既定环境、根档案/原始引用产物、B preview、C 前端、Runtime 均未改变。
- 仅本地实现检查点；没有推送、部署或合入。
- 下一步：在具备符号链接能力的既定环境补齐两个原有测试及完整回归证据，然后安排非作者评审与 A 依赖集成。生产认证/存储和真实双机部署另行验收；Runtime 下一阶段再实施，本次到此停止。

## 2026-10-17 初版网站分支发布

用户指定新分支 bd_First_Edition_16.29，基线 ba0bd85f16e8d49fb05b3acf52b73a1cae7fd9e6。保存本轮平台前后端、Graph AI 提炼、原始 .aica 导入和 Windows 局域网部署脚本；保留既有改动，不合并其他分支。

命令：git remote -v、gh repo view、git ls-remote、git switch -c bd_First_Edition_16.29、rg 凭据检查、git diff --check；随后执行协作记录校验、暂存差异检查、git commit 和 git push -u origin bd_First_Edition_16.29。

文件范围：apps/gateway、apps/web、packages/runtime/graph 相关实现及策略、collaboration.json、tasks 和本记录。本次仅发布已有实现，使用前阶段构建/最小接口验证证据，不重复大范围测试。数据库、模型凭据、日志、node_modules 和构建产物不入库。已知符号链接权限回归限制及真实模型调用边界沿用前阶段记录。

当前：分支已创建，凭据模式扫描和空白检查通过；提交与远端同步待完成。下一步：提交后核对远端分支 SHA，记录发布结果并结束本阶段。

## 2026-10-18 局域网网站恢复

- 用户报告网站不可访问。检查发现网关和 HTTPS 代理均未运行；数据目录及部署状态文件仍在。旧网关日志只记录正常启动，没有进程退出原因，因此无法确认服务为何停止。
- 使用 `apps/gateway/deployment/Start-AiCA.ps1` 恢复 HTTP 网关（PID 3516），再使用 `Start-AiCA-HTTPS.ps1 -LocalAddress 10.19.51.6` 恢复 HTTPS 代理（PID 27952）。原 LAN 地址仍配置在本机。
- 验证：`http://127.0.0.1:8000/health`、`http://10.19.51.6:8000/health`、`https://10.19.51.6:8443/health` 均返回成功；LAN HTTP/HTTPS 首页均返回 200。
- 文件：仅更新本状态记录；未改应用代码或数据库。
- 阻塞：无。下一步：用户刷新页面；本机重启后若服务未自动启动，需再次运行既有启动脚本（当前未配置 Windows 服务或开机任务）。

## 2026-10-18 MCP字段导入映射修复

- 任务 `D-MCP-FIELD-MAPPING-20261018`；沿用用户确认D及前后端集成范围。分支 `fix/D/mcp-import-field-mapping-20261018`，实现提交 `8b1d428`，基线 `c0b5aa1`。
- 统一后端展示投影：实际Run的prompt/model优先于定义值，保留历次Run、定义输入与JSON来源；通过Run.agent_id关联Agent和Harness，Agent模型单独展示，不冒充生成模型。历史修订中仍属于当前节点的Run也可查看。
- 历史事件仅从已上传且哈希验证的附件展开，展示明确登记的用户要求；不读取客户端路径、抓外链、推测步骤或发起AI调用。未记录、已记录但无法识别、历史附件未展开分别标记。历史预算为累计64MiB、每行2MiB、2000条用户消息，超出保留原附件并给出提示，不限制整个归档大小。
- 新导入、旧任务缓存及作品重新打开共用映射；旧UI空字段可从原档补回，同时保留用户已保存的修改/主动清空。原归档及MCP回执不变。
- 命令：既有aica环境运行 `python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests`（最终78 passed，82.03s）、`python -m ruff check apps/gateway`（passed）；apps/web运行 `npm test`（52 passed）及 `npm run build`（passed）；`git diff --check`（passed）。新增回归覆盖实际输入优先、Agent模型分离、历史事件、旧缓存恢复、幂等、UI修改保留、权限和严格回执。
- 部署前核对无queued/running任务，SQLite在线backup生成 `before-mcp-fields-20261018-132225.sqlite3`，随后运行既有Stop/Start-AiCA脚本。新Gateway PID19348；本机HTTP8000/HTTPS8443健康成功。只读检查4个公开库Graph接口均可读，但这些公开版本无原档映射，不将它们冒充用户私有样本验收。
- 文件：后端frontend_v2/platform/process_view、前端workflow/platform/导入适配/Studio、对应测试、任务记录及本状态文档；既定环境和MCP采集代码未改。
- 边界与下一步：未取得用户具体JSON，文件本身未采集的模型/提示词无法恢复；待非作者评审后通过PR合入integration_MVP。本阶段代码推送独立任务分支，不宣称已合并。

## 2026-10-18 平台到Agent工作流接手导出

- 任务 `D-AGENT-HANDOFF-20261018`，沿用用户确认D及必要前端集成范围；分支 `feat/D/agent-handoff-export-20261018`，基线 `7a9e69a`，实现 `69c0929`。本阶段完成固定版本导出/接手，不扩大为自动双向同步。
- 新接口 `GET /api/repos/{repo_id}/handoff?commit_id={commit_id}&format=aica|bundle`；已有MCP ZIP拉取获取相同接手资料，原上传回执及默认原档JSON下载保持兼容。纯UI新建库也可导出。
- 当前Graph正式保留平台节点增删、提示词（含清空）、模型、参数及明确依赖，原始定义入历史，不伪造新Run。中文/不符合协议的UI ID稳定映射并保留原ID。候选关系另列，删除输入依赖列出未决输入。
- `aica.agent-handoff`扩展引用接手说明、结构化workflow和已保存operations差异文件；给出recompute/rebuild/reuse/skip建议、Agent/Harness和图片路径。操作记录包含保存作者/时间、字段前后值、关系、封面/保存的预览历史，不冒充逐键输入或未保存操作。
- 修改图片、参考原图、局部编辑选区与生成输入打包为可校验本地附件；已保存未确认预览单独标记。目标提示词后改时仍保留历史图片实际请求，并标记重算；Key不进入交接内容。缺失附件/适配器明确列出，不抓客户端文件或外链。
- 前端主导出改为先保存，再下载新提交的`.aica`或ZIP；返回新commitId/archiveCommit，避免把新版本下载成旧原档。离线示例仍可导出原界面记录，不冒充正式aica。
- 最终实现提交验证：aica环境 `python -m pytest -q -c apps/gateway/pytest.ini apps/gateway/tests`：81 passed，81.87s；`python -m ruff check apps/gateway`通过。apps/web `npm test`：53 passed；`npm run build`通过；`git diff --check`通过。模型供应商为Mock，不发生收费AI调用。
- 往返证据：修改原图后的aica经现有MCP import_archive恢复到新工作区，图片字节、参考图、选区、当前定义和接手文件完整；executed/recording_started_by_import为false。已有download_archive兼容、旧提交字节不变、重复导出哈希稳定、分支固定、Fork继承及跨库/账号隔离通过。
- 在线只读检查发现纯网页新增依赖边的tuple序列化边界，修复并补中文ID/依赖回归后，公开6节点作品便携JSON和ZIP分别经共享读取器及verify_bundle通过，head不变。部署前无活跃任务，SQLite备份`before-agent-handoff-20261018-142303.sqlite3`；最终Gateway PID16612，HTTP8000/HTTPS8443健康成功。
- 文件范围：gateway agent_handoff/archive_export/frontend_v2/repo_media及测试、gateway README、web Studio/platform/workspace及测试、本任务与状态；MCP采集源码、共享Schema、既定环境和锁文件未改。
- 下一步：非作者评审后按既有PR流程合入integration_MVP；真实用户使用新的导出按钮，将aica导入Agent后按交接说明继续，或经现有MCP拉取固定版本。当前不保证未知执行适配器可以无配置直接运行，也未增加无确认自动同步。

## 2026-10-18 integration_MVP 合入记录

- 用户明确要求将当前修改合入 `integration_MVP`，本阶段只做 Git 集成和记录，不新增功能。沿用 D 角色及用户授权的必要前端范围。
- PR https://github.com/AiCA-Team/AiCA/pull/16 已于 2026-10-18T07:15:13Z 合并；合并提交 `51fbdd2cda1dca2f86e7388c5c89631eba47373a`，原目标 `320c1ae`，来源 `4a2ce5b`。包含 MCP 上传入口、Run/Agent/用户历史字段映射，以及当前 Graph/图片/操作记录的 Agent 接手导出。
- 命令：`git fetch origin`、`git merge-base --is-ancestor origin/integration_MVP HEAD`（合并前）、`git diff --check origin/integration_MVP...HEAD`、`gh pr create`、`gh pr view 16`、`gh pr merge 16 --merge --match-head-commit 4a2ce5bcd93ef9fba0d4fb5e7526947e7cbcc28b`、`git fetch origin integration_MVP`；远端返回 MERGED、MERGEABLE/CLEAN，无冲突。GitHub 未配置强制检查/评审；本次按用户明确合入指令执行，未宣称额外非作者人工评审。
- 验证：`git diff --exit-code 69c0929 origin/integration_MVP -- apps packages` 通过，合并后的应用代码与此前 Gateway 81 passed、前端 53 passed、Ruff 和 build 通过的实现提交一致。本阶段无代码改动，不重复完整测试。
- 文件：本合入记录及 D-MCP-FIELD-MAPPING-20261018、D-AGENT-HANDOFF-20261018 任务状态/合并证据。记录更新使用独立 docs 任务分支和 PR，不直接推送共享分支。
- 本阶段补充验证：两份任务 JSON Schema 校验通过；LAN HTTP8000/HTTPS8443 的 `/health` 均返回 success。未重启服务。
- 阻塞：无。下一步：同步本地 integration_MVP；用户可继续使用已部署的平台验证导出。此次未改 MCP 上传协议、数据库或部署配置。
