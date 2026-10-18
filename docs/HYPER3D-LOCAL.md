# 图片 Fork 到 3D

任务 LOCAL-3D：由用户在当前会话直接授权跨前后端本地集成。范围为 graph-application/web-application、platform-api/gateway-application；不修改 archive-contract/capture-package 或共享协议。基线 integration_MVP@320c1ae，独立分支 codex/hyper3d-image-fork。此本地任务不代表团队主干认领或已合入。

用户另行明确允许新增并锁定 @google/model-viewer，以及最多一次基础档真实 Hyper3D 建模验收。密钥仅后端本地配置，不能进入浏览器、源码、导出档案或日志。真实收费提交不自动重试。

流程：MCP 图片档案导入/上架 → Fork 为私有版本 → 修改图片并确认 → 在图片结果旁生成 3D → 保存独立 GLB 节点与来源 → 可发布、继续 Fork。人像只是可用示例；不会从成品图片推断不存在的 MCP 执行记录。

状态：本地实现、部署和真实基础档验收完成。未推送远端或合入团队主干。

## 使用

工作台上方、图片或模型结果区都提供“生成3D”和“重新生成3D”按钮。生成使用选中图片；重新生成沿用所选模型的来源图和建模描述，可在弹窗中修改描述。未选择节点时使用首张图片或最新模型；缺少来源图或已有模型时禁用对应操作。社区作品先登录/Fork。点击入口只打开确认窗口，勾选图片发送和费用确认后才提交；离开页面不会取消供应商任务。生成后可旋转、缩放、下载GLB，点击“保存模型到创作图谱”建立独立节点，再发布作品。

模型保留来源仓库、不可变提交、图片节点、产物ID、实际输入PNG的SHA256、提示词、档位与参数。重新生成会调用新的建模任务，旧模型保留，新模型按版本编号另存；不把3D文件交给原生图接口。若要改变衣服或表情，应先修改并确认来源图片。重复保存同一模型不会重复添加节点或调用模型。

支持原生 aica.json/MCP 档案与已保存的前端图片结果；示意封面、未上传文件或任意外部URL不能直接建模。完整版本包会带上已保存GLB及生成节点。现有 MCP 接入与共享协议不变。

## 后端配置与接口

配置 `AICA_HYPER3D_KEY_FILE` 指向只允许服务账号读取的UTF-8密钥文件，或使用后端环境变量 `HYPER3D_API_KEY`。变量不加 VITE_ 前缀，不写入浏览器设置或版本库。当前本地包装启动脚本读取工作区 outputs/aica-local-platform/hyper3d.key，并将文件路径传给后台进程；密钥和数据不属于可移植源码。

- `GET /api/3d/capabilities`：仅返回已配置状态、供应商和固定基础档。
- `POST /api/3d/repos/{repo_id}/jobs`：所有者、当前head、登记图片来源、明确费用同意和幂等request_id必需。
- `GET /api/3d/repos/{repo_id}/jobs`、`GET /api/3d/jobs/{id}`：任务恢复与查询。
- `POST /api/3d/jobs/{id}/resume`：只恢复已提交任务的查询/下载，不重复建模。
- `GET /api/3d/jobs/{id}/model?repo_id=...`：校验仓库可见性或Fork继承、文件哈希后提供GLB。

任务和模型位于服务数据目录的 rodin_jobs 表及 rodin-models/；前端锁定 @google/model-viewer 4.4.0，仅在展示模型时加载。

## 费用与失败处理

本次固定 Gen-2、Raw、medium、PBR、GLB；[官方Gen-2说明](https://docs.hyper3d.ai/en/api-specification/rodin-gen2)列出基础费用0.5 credits。未使用额外收费选项。真实验证前后余额223→222.5，恰好0.5 credits，仅一次供应商生成。

HTTP201不单独作为提交成功证据，需无error、有uuid和jobs.subscription_key。状态查询使用顶层subscription_key，下载使用task_uuid；依据[状态接口](https://docs.hyper3d.ai/en/api-specification/check-status)和[下载接口](https://docs.hyper3d.ai/en/api-specification/download-results)。异步查询有截止时间，可恢复；收费POST不自动重试。提交结果不确定时标为unknown，并阻止该账号的新任务，需人工核对供应商后处理。服务重启后，已知供应商任务可继续查询，未知提交不会自动再发。

本版本用于本机集中验证，不包含多租户计费、额度分配或公网运营配置。

## 实测

- 后端26项（新增Hyper3D与原平台回归）通过；覆盖幂等冲突、私有权限、Fork继承、应用层拒绝、网络不确定、新请求锁、原生MCP档案和重启只查询。
- 前端完整52项通过；新增来源/模型节点/序列化/重复保存检查。最终TypeScript和生产构建通过。
- 原生生图工具实际生成蓝衣虚构人物并改成红衣；从可见结果事后提取aica.json，MCP aica_validate_archive返回valid=true。明确缺少自动录制的原始调用日志，没有编造未观察到的模型版本或时间。
- 本地真实Hyper3D任务成功，GLB v2为27,719,916字节，哈希验证通过；浏览器预览、图谱保存、发布、刷新、完整包包含模型、第二账号Fork读取及私有Fork匿名拒绝均实测通过。
- 本地验收材料（非团队源码）：工作区 outputs/aica-3d-demo/ 的live-verification.json、billing-check.json、portrait-portable.aica.json、explorer-red.glb、explorer-complete.aica.zip。演示凭据单独受限存放，不随源码/档案分享。
- 按钮追加验收：52项前端测试及构建通过；浏览器确认两个入口、独立弹窗和社区Fork权限提示。测试覆盖选定图片、多图片身份区分、原模型来源继承、缺图禁用与版本2保留旧模型。本次未新增真实收费调用。截图为 outputs/aica-3d-demo/web-3d-buttons.png。

## 改动范围

gateway-application：rodin.py、app.py路由/生命周期、archive_export.py，以及test_rodin.py。web-application：ThreeDPanel、ModelViewer、来源节点装配和展示/媒体读取，新增锁定前端依赖。不修改原始方案文档、MCP录制状态、全局环境或远端仓库。
