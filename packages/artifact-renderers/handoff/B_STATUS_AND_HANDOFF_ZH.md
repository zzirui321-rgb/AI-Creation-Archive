# AiCA B 模块完成情况与团队交接

本说明依据《AiCA v1.0 正式项目计划书》及新版角色调整方案，核对当前本地整合代码与验收记录。B 的职责是预览执行和产物展示；项目书旧角色矩阵中的 Extraction、Golden Path 分工不作为本轮归属依据。

当前 A/C/D 对齐目标统一为用户确认的 `bd_First_Edition_16.29@f0fc294`，专项验证见 `BD_ALIGNMENT.md`。下文原 66 项测试及多分支部署记录保留为历史证据；旧执行 Runtime 是独立兼容依赖，不代表该整合分支原生提供 Replay。

本轮直接对齐验收：B 完整 pytest 68/68、B 前端 33 Vitest、bd 前端应用补丁后的 31 Vitest、构建与 Ruff 均通过。

结论：B 的图像生成、双阶段预览、通用合成、真实文件与多模态展示主链路已经实现，并用用户本地模型验收。整个项目仍未达到项目书全部功能完成、统一上云和最终路演验收的状态。

## 一 项目书覆盖情况

| 项目书 | 当前可用内容 | 仍需完成或确认 |
| --- | --- | --- |
| 02 通用创作档案 | 由正式 .aica 的变量、工具与 DAG 驱动；没有固定漫画剧情或节点数量 | 执行仅限已注册适配器，不等于任意工具、脚本和所有模态均可运行 |
| 03 授权记录与提炼 | 目标分支提供授权记录、日志/Git 提取及 ZIP 传输；本地执行结果通过 A 记录 | A 独立分支的步骤登记与后台上传功能未包含在该整合基线；完整 ComfyUI/SD 元数据重建未确认 |
| 04 Branch 与 Golden Path | 执行档案保留试错分支；D 计算成功历史的推荐路径；C 展示图和分支 | 发布时 Hide/Prune/Trim 的完整权限与导出流程未在本整合版端到端验收 |
| 05 协议 | 使用 A 最新 Schema；实际生成的 Archive 经 A CLI 校验通过；真实文件与哈希可导出 | preview sidecar 是当前版本的桥接约定，后续稳定公开契约需 A/D 接受 |
| 06 Fork 与三态变量 | 可执行档案工作区已跑通 Keep/Override/Append、Fork、脏节点执行与结果读取 | 社区仓库 Fork 和 Runtime 执行 Fork 尚未成为同一个统一对象；语义变量建议不能自动视为执行绑定 |
| 07 缓存与双阶段预览 | 已真实验证 CACHE HIT/RECOMPUTE/REBUILD、Draft/Final、同 Seed、阶段缓存隔离、混合新旧产物合成 | 各供应商能力仍有差异；不保证跨模型人物/构图完全一致，不承诺固定延迟 |
| 08 社区与分发 | 有账号、仓库、分支、公开/私有、社区页面及 B 展示组件和外链 | 最新 Replay 结果自动成为社区仓库的新 commit 并可发布，尚未统一接通；生产部署与账号执行权限未完成 |
| 09 标杆演示与三个 Showcase | 三类不同结构的合成档案已验收；另有真实生图工作流与参考图结果 | E 仍需准备有来源和许可的正式漫画、开源 Workflow/Skill 与完整真实 Self-Archive；合成样例不能冒充团队真实历史 |
| 10 路演 | 真实来源标识、离线兜底与预热缓存机制已提供；真实模型路径已有截图和回执 | 正式主 Demo 的预热资产包、录屏备份和完整三分钟彩排仍需团队完成 |
| 11 MVP | 图像类的变量修改、局部重放、真实 Draft 与 Final 已可本地运行 | 最终应使用同一套正式数据，把采集、上传、社区 Fork、执行、发布连续验收一次 |
| 12 协作交付 | B 源版本通过 feature/b-generic-preview 的 PR #11 交付，并按用户明确要求直接同步 B 目录到 bd_First_Edition_16.29；C/D 装配修改另有补丁 | 需 Owner 审阅装配补丁和 CI 验收；同步 B 目录不等于已应用 C/D 补丁或 PR 已合并 |

项目书的音视频、Code/Web、Agent/Skill 跨领域定位不能解读成所有执行器均已交付。当前支持这些产物的展示或内容落盘，不代表已经实现视频生成、音频生成、LLM 生成代码或任意 Agent/脚本执行。

## 二 B 实际完成了什么

### 后端预览底座

位置：`apps/gateway/src/preview/`。

1. `PreviewEngine.execute_node()` 执行单节点；`execute_plan()` 消费 D 排好的执行链，衔接前序产物与下游合成。D 继续负责 DAG、Fork、脏传播与缓存决策。
2. 按 `execution.tool` 路由图像、排版、文本/JSON 适配器；未知工具会阻塞，不冒充成功执行。
3. Draft/Final 使用独立参数与缓存，同一输入保持 Seed，高清升级保持宽高比。
4. 实现校验后的预热缓存、HTTP 模型调用及明确标注的 Local Fallback；API 失败时可选择明确报错。
5. 用户设置的供应商、URL、模型、阶段模型、超时与密钥按任务传递；不同任务使用独立 registry，不修改全局配置。
6. 密钥不进入任务持久化、Archive、Harness、缓存身份或浏览器持久存储；刷新页面需重新填写会话密钥。
7. 实现 OpenAI 兼容、SiliconFlow、Fal、千问原生同步图像协议，以及本地 sdcpp 方言。真实模型验收使用本地 FLUX；其他厂商只有协议测试，未声称真实付费账号已验证。
8. 本地模型支持实际参考图编辑；不支持参考图的供应商会明确报错或产生标注的兜底，不静默丢掉参考图。
9. 画风、变量等实际生效值会进入真实 API Prompt，不仅用来计算缓存。
10. 合成器将新旧图片和文本合成为独立 PNG，计算真实 SHA256，保留子区域、引用关系与附件。
11. 真实 API 产物可被 D 缓存；临时兜底及包含兜底的合成产物不会阻止后续恢复真实 API。
12. 默认超时仍为 10 秒；显式用户配置可调至 300 秒。本地模型验收选择 180 秒。

### 前端产物组件

位置：`packages/artifact-renderers/`。

- `ArtifactRenderer`：图像、复合排版、文本/Markdown、代码/JSON、音视频及受限 HTML 展示，包含阶段、实际来源、状态与下载。
- `ArchiveDiffViewer`：Original/Fork 产物和变量操作对比、真实复用统计。
- `ExploreArchiveCard` 与 `ProvenanceAndLinksBar`：项目展示、工具与外链、Recorded/Extracted/Inferred 信息。
- `archiveViewModel` / `cIntegration`：消费 A 类型、动态变量、D 请求、产物字节与哈希校验、预览 sidecar 对齐、URI 解析与 Blob 回收。

### 本地对齐与真实验收

当前统一基线的独立副本为 `bd-first-aligned`，本地分支为 `local/bd-first-aligned`。原 `local-live-preview` 保留历史部署与真实模型验收数据。

| 部分 | 采用来源 |
| --- | --- |
| A 协议与采集 | bd_First_Edition_16.29 @ f0fc294 |
| C 前端及 D 平台/语义解析 | bd_First_Edition_16.29 @ f0fc294 |
| D 可执行兼容 Worker | test @ 6b98aa4 加独立装配补丁及其所需 A 兼容依赖 a0cc735；不是整合分支的原生功能 |
| B 起点 | feature/b-generic-preview @ a7e26f9 加本轮本地修改 |

排除了 journal 分支，没有把另一个社区展示分支覆盖到真实平台接线上。旧本地目录保留。

用户本地 `flux3-image-klein` 已验证：Original Draft/Final，修改绘本主体后 Fork Draft/Final，再次 Final。五个节点中修改后复用三个，只重算一张图并重建布局；再次 Final 为五个缓存命中。改变的主体图片哈希变化，未改变的背景图片哈希不变。单节点 C 预览与实际参考图调用也成功。

验证结果：B 66 pytest 通过，C 31 与 B 33 Vitest 通过，类型检查、构建、Ruff 通过，实际 Archive 经 A 校验有效。三类不同结构的离线档案通过浏览器桌面/手机验收；离线结果明确标注 Local Fallback。最终重启后，真实图片与 5/5 缓存计划仍可读取。

全量 A/D 回归是 360 通过、6 失败，不是全仓全绿。4 项需要 Windows 符号链接权限；2 项原生 MCP 用例在功能断言完成后，对已退出的协调器做 shutdown 时连接拒绝。未削弱 A 测试或安全校验。

## 三 交给 A 的内容

1. 复核实际生成的 `final-archive.json` 和便携传输包，确认 Run/Artifact 身份、SHA256、旧定义历史与派生修订符合正式协议。当前 A CLI 已通过。
2. 评审 `application/vnd.aica.preview+json`、`aica-preview/1` 约定。它用于桥接 source/stage/seed、真实主产物哈希、参考图与布局区域；不要把生成来源 Live API 与采集可信度 Recorded 混为一类。
3. 如果团队后续合入 A 独立分支的后台上传客户端，再与 D 验收自动上传闭环。当前 bd 整合基线不包含 `aica_bind_platform`，不能声称该入口已接通。
4. 检查两项协调器退出清理失败，并在有符号链接权限的团队环境复跑另外四项。

交接入口：`BD_ALIGNMENT.md`、`PROVIDER_COMPLETION.md`、B 的 `README.md`、真实验收档案与日志。`PLATFORM_UPLOAD.md` 属于 A 独立功能分支的后续对接资料。

## 四 交给 C 的内容

1. 接收 `c-provider-settings.patch`，其基线为 f0fc294；接入已实现的用户 API 设置、真实单节点预览和可执行档案工作区。
2. 使用 B 四个通用组件和 `ArchiveClient`；保留当前动态变量/DAG，显示真正的 Run 决策、供应商来源和阶段，不用本地推演替代执行回执。
3. 区分展示记录、待审核语义图和完整可执行 Archive。前两者不能仅凭 Prompt 就声称完成了全图增量 Replay。原始 C 按钮现在只完成它明确请求的单节点预览。
4. 与 D 收口一个产品流程：社区作品 → 同一项目的可执行版本 → Fork/变量修改 → Replay → 原仓库分支上的新 commit → 发布。高级节点 Prompt/模型/参数编辑也需要更新真实定义和修订，而不只改展示副本。
5. 保留 API Key 的会话内存策略，并明确用户默认模型、节点覆盖、Draft/HQ 模型的实际生效结果。

## 五 交给 D 的内容

1. 接收 `d-runtime.patch` 和其后的 `d-request-providers.patch`。后者加入临时 ImageConfig、按任务独立 registry、配置参与计划与执行、以及 `cache_allowed` 条件写缓存。
2. 用 `register_preview_tools(registry, settings)` 装配 B。当前工具注册版本为 b-generic-3、kind=model、每节点一个输出；图像适配器内部版本为 4。D 负责声明不兼容工具/版本，不自动把它们转换为生图。
3. 最新语义解析与旧可执行 Runtime 目前仍是两个进程，且 Python 包命名冲突。由 D 决定合并或正式拆分服务的方式，B 不把导出旧源码的本地启动脚本当最终架构。
4. 统一社区账号和 Runtime 执行授权，绑定 repository/branch/commit 与 project/archive/job，完成执行产物写回社区版本。当前 Runtime 使用仅供本机联调的代理令牌，不能直接当多用户生产权限系统。
5. 负责最终 API、并发/取消/恢复、产物权限和远程存储部署；保留正确的缓存身份与 fallback 隔离。模型 Key 不得进入任务数据库。

## 六 完整收口的下一步

建议先由 D 收口身份与执行结果回写，C 把两套工作区合成一条用户流程，A 与 D 验收真正的采集上传，再由 E 注入正式素材、确认来源许可、整理真实 Self-Archive 并完成彩排。

完成这些以后，团队才可以把“本地模块联调通过”升级为“项目书 P0 全链路 Demo Verified”。视频/音频生成、任意代码/Agent 执行及长期社区功能应单独声明范围，不能借通用架构的名字宣称已经支持。
