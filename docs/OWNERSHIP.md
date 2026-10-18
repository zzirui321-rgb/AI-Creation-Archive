# 职责和接口边界

角色依据新版分工表，目录仅视为原方案示例。本规范用能力和 module_id 约束交付；实际位置从 bindings 发现与确认。

| 角色 | module_id | 交付责任 |
| --- | --- | --- |
| A | archive-contract | 归档 Schema、类型、版本、正反 fixture；数据唯一来源 |
| A | capture-reconstruction | MCP Y/N 授权记录；Git/Agent 日志提取；标明证据与推断 |
| B | preview-execution | Stage 1 Draft 与 Stage 2 高质量执行；超时、费用、来源标识 |
| B | artifact-presentation | 漫画排版、Original/Fork、Explore 卡片、外链组件 |
| C | graph-application | 应用壳、画布、分支、Golden Path、状态动画与变量问答 |
| D | graph-runtime | Cache Key、Dirty、Golden Path、Fork 三态变换和局部执行 |
| D | platform-api | REST 契约、路由装配、平台后端、调用 B 的服务 |
| E | demo-assets | H4 标准业务数据与漫画素材、来源许可与内容 hash |
| E | integration-demo | H12/H24 联调验收、问题闭环、真实 Demo 与 Self-Archive |
| E | coordination-docs | 产品资料、协作接入、进度协调、路演内容 |

A 的协议单元 fixture 与 E 的业务 Demo 数据分别交付；E 的 JSON 由 A 的契约检查验证。Golden Path 的计算归 D，画布呈现归 C。B 的组件实现归 B，应用组装归 C。B 的预览实现归 B，外层 REST 装配归 D。

同一物理工程内可包含多个逻辑模块。映射明确子资源归属后，父模块任务不能改子资源。未经接受的新映射不能在实现 PR 中自动扩大权限。共享配置涉及多个角色时拆任务或由 E 协调受影响 Owner；开发环境仍由既有权限与流程管理。

角色是责任与评审归属，不是指定模型。Owner 为自己使用的 Agent 执行负责；非作者评审仍由团队明确安排。
