# integration_MVP 部署说明（2026-10-18）

本分支已合入 C 的 `feat/frontend-v2`（978a513）。界面保留 C 的布局和结果节点交互；账号、作品、Fork、分支、版本、原始归档与附件统一使用现有 Gateway。下方本地实验模式说明仅为来源历史，不代表当前集成部署。

- 生产入口：Gateway 8000；既有 HTTPS 代理 8443。`Start-Platform.ps1` 调用本仓库 Gateway 启动脚本。
- 开发：已有依赖环境中运行 `npm run dev`，`/api`、`/runtime-api` 代理到 127.0.0.1:8000。
- 不依赖外部 `AiCA-graph-lab/backend/aica_graph` 或 F 盘；替代实现在 Gateway 的 `frontend_v2.py`，路由 `/api/creation-parser`。
- 导入只整理明确的原始记录，自动私有入库，不调用 AI。支持 AiCA、模块记录以及明确 messages/events；不是任意 JSON 的推断解析器。
- 图片预览/选区编辑复用现有 B 供应商适配器；结果持久化，确认后随作品版本保存。千问文生视频支持异步任务及 MP4 Range 读取；其他视频协议需团队网关。
- 浏览器“停止等待”不保证供应商取消或停止计费。服务重启将进行中任务标为中断，不自动重试收费请求。
- 作品、附件、任务均校验权限；供应商 Key 仅随请求使用，不写后端任务记录。浏览器模型设置仍沿用 C 的本地保存行为。
- MCP 上传路径、回执 Schema、项目令牌和拉取接口保持原契约；作品联动继续由既有导入事务处理。

---

# AiCA Frontend

Owner C，修改范围为 `apps/web/`。React、TypeScript、Vite 与 React Flow；默认在浏览器本地运行，不需要 API Key。

## 启动与验证

```sh
npm ci
npm run dev
npm test
npm run build
```

作品卡片按从左到右、填满一行再换行排列，宽屏最多四列，窄屏按三／二／一列适配。

本地默认地址 http://127.0.0.1:5173。使用独立 npm lockfile；团队建立 pnpm workspace 时再统一。

## 路由与操作

- `#/`：品牌首页、六类社区筛选、搜索、收藏。示例作者和互动数据不代表真实在线数据；本机发布作品会加入发现页。
- `#/creations`：本机原创、Fork 与已发布作品。可上传自己的 MCP 解析记录，也可前往发现页 Fork。支持同一原作品创建多份独立副本。
- `#/new`：拖放或选择 `.aica` / `.json`，检查标题、分类、模块和依赖后创建原创草稿。前端不设置文件大小上限；通过独立解析服务处理创作记录，并投影为结果节点图。
- `#/works/:id`：作品详情与模块式 Graph。默认只显示卡片；点击在当前视口弹出创作细节，提示词位于最上方，模型、模式、Harness、参数、输入输出、依赖和来源证据位于其后。
- `#/settings`：头像菜单中的「我的设置」。显示名称、自动保存、提示词跟随高亮、生图／生视频模型 ID、API Base、持久化 API Key、团队预览网关。
- `#/saved`：本机收藏。`#/login`：演示账号 `creator@aica.demo` / `aica2026`，并非真实认证；OAuth 未配置时明确提示。

主流程：查看作品 → Fork → 点击节点弹出详情 → 在卡片内 Remix 提示词 → 生成 Draft → 确认最终版本 → 保存／发布。Remix 与节点弹窗融合，不在 Graph 下方另设变量面板。自己的原创可直接编辑或「再创作」为新副本。Fork 保留父项目关联，原作品不变。

画布上的普通滚轮继续滚动页面；按住 Ctrl（macOS 为 ⌘）再滚轮才缩放。保留缩放按钮、拖动平移与双指捏合，关闭双击缩放，减少误触。

提示词阅读按浏览器中文分词跟随鼠标／键盘高亮同词，并标出包含该词的模块；不宣称进行语义分析。编辑支持 A 保留、B 覆盖、C 追加。修改提示词或模型后，旧结果失效，已发布作品转回草稿，重新发布后更新发现页。

## 本分支上传范围与服务依赖

本分支只提交 `apps/web/` 前端，不包含独立的 `AiCA-graph-lab` 后端。直接运行 `npm ci`、`npm run dev` 即可启动前端；首页、示例图、收藏、本机草稿与发布使用浏览器本地数据。

JSON 导入解析、原生千问生成、选区编辑合成与服务端分享快照需要另行启动兼容服务。Vite 将 `/api/creation-parser/*` 代理至 `http://127.0.0.1:8766/api/*`；生产部署需提供等效代理。服务缺失时相关操作会报错，不代表前端已独立实现解析或模型生成。`Start-Platform.ps1` 是原本地配套启动脚本，要求仓库旁存在 `AiCA-graph-lab`；只检出本分支时请使用 `npm run dev`。

前端使用的接口入口包括 `GET /health`、`POST /imports`、`GET /jobs/:id`、`GET /jobs/:id/result`、解析产物读取、`POST /previews`、预览任务查询/取消/产物和原图读取，以及分享快照创建/查询。具体请求形状以 `src/importing/parser.ts`、`src/preview-service.ts` 和 `src/local-sharing.ts` 为准，这些适配接口不替代团队正式协议。

当前普通提示词生成仍保留“参考原图生成”选项；连续编辑的自动底图绑定方案尚未实施。选区编辑的几何、交互与请求在前端，实际遮罩合成在独立后端。详见 `LOCAL-EDIT-SHAPES.md`。

生成流程为节点内“生成修改预览” → 结果对比 → “确认并保存为新节点”。确认不重复调用模型；原节点可查看已有修改版本。模拟预览明确标注，不伪装成真实结果节点。

## MCP / 后端解析结果接入

`src/workflow.ts` 是展示适配层，不另定义正式 `.aica` Schema。读取了 A 分支 `origin/feature/a-protocol-mcp` 的 v0.1 样例；正式协议仍以 `packages/schema/` 为准。

支持两种输入：

1. `.aica` v0.1 的 `metadata`、`graph.nodes/edges/branches/golden_path`、`variables`、`runs`、`harnesses`、`artifacts`。支持 literal、variable、template 提示词绑定；Harness 可经 run.harness_id 查找。
2. 后端已解析的 `steps` 或 `modules` 数组，每项包含 `id`、`title`、`prompt`、`model`、`mode`、`harness`、`params`、`inputs`、`outputs`、`provenance`、`evidence`、`artifacts`，通过 `depends_on` 或根级 `edges` 提供依赖。

示例：`public/examples/module-record.json`，可从上传页面下载。示例明确标记为测试展示，非真实录制。

前端和解析适配层检查 JSON、节点标识与依赖引用；没有依赖时不编造顺序，缺少模型／Harness／提示词时明确提示。缺失快照会提示记录不完整。展示校验不等于正式 Schema 或执行语义校验。未知字段不作为可执行命令使用。

读取及导出会移除常见的凭证字段（API Key、Authorization、password、credentials 等）；这不替代用户检查自由文本中的隐私。

## 预览与模型配置

默认生图配置为千问 `qwen-image-2.0`，API Base 为 `https://dashscope.aliyuncs.com/api/v1`；可按实际账号地区与模型权限修改。参考[阿里云千问生图说明](https://www.alibabacloud.com/help/en/model-studio/qwen-image-api)。生视频模型 ID 和地址默认留空。

`src/preview-service.ts` 提供可替换的 `ModulePreviewService`。网关 URL 与模型 Key 均留空时运行明确标记的本地模拟；配置 Key 后通过独立本地服务调用模型；示意 SVG 不会根据任意 Prompt 生成真实图片。原始产物可从导入文件引用，预览支持图片、视频和原始／修改对比。

配置网关后，点击生成会向用户填写的完整 URL 发起 POST。此为前端待联调的适配约定，不代表团队后端已实现该路由；后续以 D / B 确认的协议替换。

```json
{
  "project_id": "local-…",
  "node_id": "step2",
  "prompt": "用户编辑后的提示词",
  "model": "qwen-image-2.0",
  "kind": "image",
  "stage": "draft",
  "mode": "text-to-image",
  "harness": "…",
  "params": {},
  "provider": "qwen",
  "api_base": "https://dashscope.aliyuncs.com/api/v1"
}
```

API Key 仅作为请求的 `Authorization: Bearer …` 传给所配置的网关；独立保存在当前浏览器的 `aica.model-credentials.v1` 中，刷新后继续使用；清空并保存可删除。密钥不进入创作记录、导出或日志。用户点击保存后才应用设置，不会自动测试网络或产生模型费用。

当前适配器要求同步返回 `{"artifact":{"url":"https://…/result.png","mime":"image/png"}}`，视频使用 `video/*`；错误或无效产物会显示失败，不降级伪造真实生成成功。异步队列、SSE、签名 URL 刷新及供应商协议由后端适配。自动化验收使用拦截响应和模拟供应商，不使用真实凭证触发付费调用。本地服务处理原生供应商异步任务与产物缓存；团队网关仍需按其约定联调。

Graph 下方保留备份版横向增量状态栏，显示「只重算真正改变的部分」或复用步骤数；节点卡片上的 CACHE HIT / RECOMPUTE / REBUILD 根据本地依赖推演，仅用于解释修改影响；真实缓存、Dirty Propagation 和 Partial Replay 来自 Runtime。目前生成入口只请求选中的模块预览，不宣称已经重放整图。

## 本机数据、发布与分享

- `src/workspace.ts` 持久化原创／Fork、来源关系、提示词、预览和发布状态。旧 `aica.local.creations.v1` 副本会迁移读取，原存储不删除，旧变量和结果作为模拟档案保留。
- 自动保存默认开启；关闭后显示未保存状态，使用「保存草稿」。发布会保存当前内容。本机存储不足时明确报告。
- 发布只加入当前浏览器的发现页；尚未实现服务端发布、账号数据隔离和跨设备同步。
- 导出为 `aica-ui-module-record` 展示记录 JSON，可再次导入；不是官方完整 `.aica` 导出，不包含所有原始事件历史。
- 作品分享支持复制本机链接、浏览器内生成 1080 × 1500 PNG 卡片。链接不会把草稿同步给其他人；卡片使用本地示意封面并标记本地演示。
- Orbit 宠物是本地规则助手，未连接 LLM／MCP 工具执行。

沿用 apple-design 的系统字体、透明材质、即时反馈和分段控件弹簧；支持减少动态效果、减少透明度、高对比度。首页保留柔和鼠标光晕与星点连线。

## 改版前本地回滚点

- 提交：`3b05b2a484cc8a4e8adc577fc3c7176415efb1cf`。
- 本地标签：`backup/frontend-before-modules-20261017`（未上传）。
- 源码备份：`<legacy-local-path>`。
- SHA256：`C7D81FE197F6DFC9458FFC21E446A06D0AF9F93287A320734FE2080286400E8E`。

备份内附还原说明。Git 恢复旧提交中的前端文件不会自动移除本次新增的未跟踪文件，回滚时需一并核对；源码备份不包含浏览器本机数据或模型密钥。仅在用户明确要求时提交并推送代码。
