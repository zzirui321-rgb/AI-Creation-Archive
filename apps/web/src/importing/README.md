# 上传解析与原平台接入

界面入口：`http://127.0.0.1:5173/#/creations` → 上传我的创作记录。

原有首页、社区、收藏、详情和工作台继续使用。`ImportPage.tsx` 通过本目录服务上传文件，解析结果经 `adaptAnalysis` 转换为现有 `Workflow`，保存到原有创作空间后进入 `Studio` 的 Creation Graph。不会跳转到独立实验室页面。

## integration_MVP 启动

先构建 apps/web，再运行本仓库 `apps/gateway/deployment/Start-AiCA.ps1`。不再启动外部 Lab 或 8766 服务。开发时 Vite 转发 `/api` 到 Gateway 8000。

登录后上传通过 `/api/creation-parser/imports` 创建私有作品；回执中的 repository 指向已有作品，前端复用其 ID，不重复建立卡片。此入口展示确定的原始记录，不收费提炼；MCP 继续走原来的 `/api/projects/{id}/imports`。

## 数据与执行边界

- 解析器代码保持在 `<legacy-local-path>`；这里仅负责上传、查询解析状态和前端数据适配。
- 文件以 File 直接发送，无固定上传字节上限，不在浏览器先读取完整 JSON。磁盘、内存和浏览器存储仍受设备资源限制。
- 当前创作项目使用原有浏览器存储，只保存图与附件引用，不把大型附件 Base64 写入 localStorage。解析服务缓存完成的结果和附件，可在服务重启后恢复访问。不要删除 `.cache` 中仍被创作引用的记录。
- 原有 Remix、A/B/C、保存、Fork、发布、预览和增量复用保留。语义或候选关系用虚线展示，并从增量影响传播和执行依赖布局中排除。
- 原始文件 SHA256、任务 ID、大小保存在 `workflow.importSource`。文件中的图片通过哈希验证后，在原有预览区的「原始作品」中查看。
- 保留此前前端备份 `backup/frontend-before-parser-lab-20261017` 与 `<legacy-local-path>`。本次没有推送代码。

验证命令：`npm test`、`npm run build`；后端测试：设置 `PYTHONPATH=<legacy-local-path>` 后运行 `python -m unittest discover -s <legacy-local-path>`。
