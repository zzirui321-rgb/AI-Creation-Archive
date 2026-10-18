## 变更目的

让 B 的通用双阶段预览真正使用用户选择的模型服务，并提供可供 A/C/D 审阅的交接补丁。核心按正式 .aica 节点、变量、产物与 DAG 工作，不固定漫画剧情或节点数量。

当前 A/C/D 对齐目标统一为用户确认的 **bd_First_Edition_16.29@f0fc294**。Schema、前端类型及传输验证直接消费该快照；不将 A/D 独立功能分支冒充该整合分支已包含的能力。专项记录：[BD_ALIGNMENT.md](https://github.com/AiCA-Team/AiCA/blob/feature/b-generic-preview/packages/artifact-renderers/handoff/BD_ALIGNMENT.md)。

本 PR 实际变更严格限于：
- `apps/gateway/src/preview/`
- `packages/artifact-renderers/`

C/D 装配以这两个目录内的补丁文件交付，没有直接修改队友源码。未提交本机绝对路径、密钥、生成图片、缓存或 node_modules。

## B 已完成的能力

- `PreviewEngine.execute_node()` / `execute_plan()`：单节点及拓扑执行链；D 继续负责 Fork、脏传播与调度。
- `request_config.py`：按任务传入供应商、URL、模型、Draft/HQ 模型、超时与会话密钥。每个任务使用独立 registry；密钥不进入 Archive、任务数据库、Harness 或浏览器持久存储。
- `user_previews.py`：接通 C 现有页面的单节点生成按钮，返回真实落盘文件与来源；不宣称单节点预览等于全图 Replay。
- 图像适配器 v4：OpenAI 兼容、SiliconFlow、Fal、千问原生同步图像协议、本地 sdcpp。支持本地真实参考图；实际 Prompt 包含生效画风和变量。
- Draft/Final 同 Seed、保持宽高比，缓存按阶段隔离；预热缓存优先，API 与 Local Fallback 来源分别标记。
- 默认 API 时限仍为 10 秒；用户显式配置可延长至 300 秒。关闭 fallback 后，模型失败会明确报错。
- 真实 API 产物可复用；兜底产物及包含兜底的合成结果不会污染真实模型的 Runtime 缓存。
- 通用合成器输出独立 PNG、真实 SHA256、子区域及引用关系；支持文本/JSON/代码/HTML 内容落盘。
- ArtifactRenderer、ArchiveDiffViewer、ExploreArchiveCard、ProvenanceAndLinksBar，以及动态变量、真实字节校验、可注入 URI 与缓存统计。

## 验证

- 本轮新增 2 项统一基线检查，B 完整 pytest **68/68 通过**。检查将真实 B Run/PNG ZIP 交给该分支原版 A/D，验证注册登录、档案导入、仓库版本提交、逐产物 SHA256、私有访问权限及语义输入准备；另验证 C 补丁可以直接应用。未调用外部模型。
- 纯 B 分支前端 typecheck、**33 Vitest**、构建、Ruff check/format 通过。独立 bd 基线加 C 装配补丁的前端构建和 **31 Vitest** 通过。完整 B 回归结果见 BD_ALIGNMENT.md。
- 使用用户本地 FLUX 真实验证 Original Draft/Final、变量 Fork Draft/Final、参考图编辑、C 单节点生成、桌面/手机展示与重启恢复。其他付费供应商仅完成协议测试，未宣称真实账号已验证。
- 真实五节点工作流：修改主体后 **3/5 复用**，只重算主体图并重建排版；再次 Final **5/5 命中**。主体图片哈希变化，背景图片哈希保持不变。
- 三种不同结构的离线验收档案明确标记 Local Fallback；不是实时模型生成。
- 此前多分支整合的历史 A/D 回归：360 通过、6 失败；这不是本轮 bd 基线全仓测试结果。4 项受 Windows 符号链接权限限制，2 项在协调器退出后的 shutdown 清理时连接拒绝。未削弱 A 检查，不宣称全仓全绿。

## A/C/D 交接

完整中文讲义：[B_STATUS_AND_HANDOFF_ZH.md](https://github.com/AiCA-Team/AiCA/blob/feature/b-generic-preview/packages/artifact-renderers/handoff/B_STATUS_AND_HANDOFF_ZH.md)。实现及验收边界：[PROVIDER_COMPLETION.md](https://github.com/AiCA-Team/AiCA/blob/feature/b-generic-preview/packages/artifact-renderers/handoff/PROVIDER_COMPLETION.md)。

| Owner | 接收内容与待确认事项 |
| --- | --- |
| A | 复核 Run/Artifact、SHA256、派生修订和 aica-preview/1 sidecar；当前以 bd 内的 A 为准；A 独立分支的后台上传功能需另行合入与联调 |
| C | 接收 c-provider-settings.patch；将既有 API 设置、真实单节点预览、通用组件与可执行工作区装配到正式页面；真实 Run 回执不能被本地状态推演替代 |
| D | 在 test@6b98aa4 上依次接收 d-runtime.patch 和 d-request-providers.patch；接入按任务 registry、配置化 plan/execution 及 cache_allowed 条件缓存；保留密钥的临时生命周期 |

A/C/D 的平台与协议基线均为 `bd_First_Edition_16.29@f0fc294`。该分支没有原生 executions API；旧 `test@6b98aa4` Runtime 及其 A 兼容依赖 a0cc735 仅用于隔离执行 Worker。两份 D 补丁不能直接应用到 bd 的同名包。不依赖 journal 分支；C/D 补丁仍需对应 Owner 审阅。

## 尚需团队收口

- 社区账号/仓库 Fork 与 Runtime 执行 Fork 尚未统一；D/C 需要绑定 repository/branch/commit 和 project/archive/job，并把 Replay 结果写回可发布的新版本。
- bd 的 D 语义解析与旧执行 Runtime 分进程运行；正式合并或服务划分由 D 决定。本地代理令牌不是生产多用户执行权限系统。
- 语义图及变量建议仍为待审核、不可执行；不自动补造缺失工具和执行绑定。
- 执行限于已注册、版本匹配且符合当前单输出约定的工具。音视频生成、任意脚本/Agent 执行尚未交付；支持展示或内容落盘不等于支持生成。
- 正式 Showcase、真实完整 Self-Archive、预热资产包、公网部署与路演彩排仍需团队完成。

本 PR 的 Ready for review 表示 B 模块及交接材料可评审，不表示整个平台已完成或已合并。
