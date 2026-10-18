# T011：接入 agent_coop 的决定

用户已确认 Alvintxh 负责 A，并选择方案 1：保留现有业务目录，在独立分支接入协作规范、任务记录和模块映射。

## 来源与范围

1. 规范来源：`agent_coop@0161b4997673c21e4e41e093805c1a2c83f4b6d8`。
2. 初始工程基线：`feature/a-protocol-mcp@3bbde7c`；初始迁移分支：`feature/a-agent-coop-migration`。后续按用户授权归并回 `feature/a-protocol-mcp`，原分支名仅保留为历史来源。
3. 仅导入协作资料并适配当前工程，不合并来源分支对业务文件和环境配置的删除。原业务目录、协议实现、测试、环境文件与已授权档案保留。
4. 用户明确授权允许 T011 同时建立初始映射和执行迁移，无需先合入尚不存在的认领机制。此 bootstrap 例外只适用于 T011；其他任务执行正常认领与评审流程，不从模板推导额外权限。

位置与命令集中在 [collaboration.json](../collaboration.json)。公共规则、角色提示词和任务用稳定 ID 引用资源；位置变更时同步 resource 与 locator，并运行检查。

## 绑定与任务

A 的 archive-contract、capture-reconstruction 已有实现和用户角色确认。coordination-docs 的接受仅覆盖本次公共规范接入，不代表 A 接管 E 的全部工作。其他业务模块只存在候选映射或目录占位，状态为 proposed，等待 B/C/D/E 负责人确认；D 的 API 范围排除 B 的预览子资源。

T002、T004 保留 planned，记录 PR #1 的既有实现作为候选交付，不要求重写，也不伪造历史认领或合并证据。D 的运行时/API 入口为 T005/T006，开始前需确认执行者、资源及契约。T011 检查完成后进入 in_review，未合入不记 integrated。

## 环境、样本与检查边界

沿用现有 Conda 环境和依赖；环境资料引用 setup-guide，保护 conda-environment、python-requirements、python-tooling。迁移不安装或升级依赖。dev/build 尚无可运行平台命令，保留 null。

根档案和十个引用产物此前已获用户授权用于私有仓库评估，其中历史配置可能包含原机器路径。它们是不可变历史证据，不能作为团队启动配置；本次不改写内容或哈希。迁移时没有提交数据库、会话凭证、endpoint、outbox 和本地配置；后续用户在获知凭证内容后明确要求 `.aica/` 原始目录全量上传，单次分享范围及原始快照清单见 self-archive-guide 资源，不扩展到其他目录或后续自动上传。

本地检查器使用已有 jsonschema，检查结构、资源/命令引用及任务依赖 DAG；不会执行清单命令，也不实施权限隔离、认领锁、GitHub 评审或范围拦截。远端 CI 和分支保护本次未开启或核实。实际结果见 [交付记录](DELIVERY-CHECK.md)。

## 评审与回退

[PR #1](https://github.com/AiCA-Team/AiCA/pull/1) 尚未合入 dev。用户后续明确授权收敛 A 的分支：原协议、T011 协作迁移和 T012 远程传输的全部提交统一保留在 `feature/a-protocol-mcp`，使用 PR #1 面向 dev 评审，旧 PR #2/#4 停止使用。此操作只整理 A 的候选交付，不直接推送或合入共享分支，不改变其他成员分支与后续独立任务流程。

回退只 revert 本次迁移提交，保留 A 的实现、既有录制及其他成员改动，不使用 reset/clean 清除工作区。
