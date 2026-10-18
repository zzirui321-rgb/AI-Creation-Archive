# 本机 bd 最新整合版

源分支：`bd_First_Edition_16.29`，快照 `300a582`。
本地隔离分支：`local/bd-deploy-300a582`。保留原来的本地目录与测试数据。

## 访问与启动

网站与平台 API 同源部署在 http://127.0.0.1:8088/ 。
使用已构建的 `apps/web/dist`，不经过开发 Vite 代理。

从本 checkout 根目录启动：

```powershell
./apps/gateway/src/preview/scripts/start-platform.ps1
```

脚本复用用户现有 Python 及已缓存的兼容 SQLite，默认绑定 loopback。
运行数据和日志在本地应用数据目录的 `AiCA/bd-latest/`，不写进 Git。
已有生图模型服务 http://127.0.0.1:8000 独立运行，不由网站启动或停止。

停止本版网站及其执行 Worker：

```powershell
./apps/gateway/deployment/Stop-AiCA.ps1 -DeploymentDirectory (Join-Path $env:LOCALAPPDATA 'AiCA/bd-latest')
```

## 测试入口

1. 通过网站注册自己的本地平台账号并登录，没有预设的通用演示账号。
2. 在模型设置选择本地 sdcpp，模型 `flux3-image-klein`，接口填写
   `http://127.0.0.1:8000/v1/images/generations`，本地 Key 任意非空值。
   本地模型建议显式选择 180 秒等待；真实生图验收关闭 Local Fallback。
3. 社区作品的单节点预览从已有 Studio 操作；完整工作流从 `#/execute` 导入。
   `#/new` 的原始档案入口按远程最新设计进入材料提炼，不冒充可执行工作流。
4. 本地已生成三个明确标记为合成数据的执行验收输入，位于
   `packages/artifact-renderers/demo/generated/generic-cases/`，可在执行页上传。
5. API Key 和网页登录会话仅在页面内存中保留，刷新后重新填写/登录。

账号、仓库、语义解析与自动保存采用最新 D 服务。
`/runtime-api` 使用平台账号认证，由 D 为不同用户启动独立 Worker、数据目录和内部令牌；
不再注入旧的共享本地开发令牌。旧 Runtime 仍为固定兼容执行依赖，由 D 新代理管理。
执行结果自动合并回社区仓库仍不在这版实现中。

## 验证范围

前端 C 31 Vitest、B 33 Vitest、类型检查与应用生产构建通过。
A Schema、D Runtime、Graph 自动保存、MCP 上传和平台专项回归共 177 通过、1 失败。
失败用例为旧 `test_login_throttling_and_body_limits` 仍预期 6 MiB 无效仓库请求返回 413；
最新服务取消该大小限制后返回 JSON 校验错误 422。没有削弱或跳过该断言。

平台账号隔离实测：未登录访问 Runtime 返回 401；另一个账号不能列出或读取第一个账号
的执行项目，读取返回 404。测试使用真实动态 Worker，没有模型调用。
浏览器登录、注册、真实本地模型单节点生成和鉴权图片桌面/手机显示已通过。
完整工作流验收和 B 回归结果以本轮运行记录为准，不混用旧版截图或账号。

本轮 B 完整回归 68/68 通过。真实本地模型的完整网页验收已通过：Original
Draft/Final、修改主体后的 Fork Draft/Final、重复 Final。修改后复用 3/5，
重复执行复用 5/5，模型来源及图片哈希/Seed/宽高比均检查通过；Key 刷新清除。
报告与桌面/手机截图在 `packages/artifact-renderers/.c-integration/live-provider/`。
最终停止并重启平台后，重新登录同一验收账号、选择相同模型配置，仍能显示实际图片，
Final 计划核验为 5/5 缓存命中，无新增模型调用。

本地增加的启动脚本和浏览器验收脚本修改仅为本轮部署；尚未提交到远程。
