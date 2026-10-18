# 单份记录完成交接

每个任务的同一份记录兼作进度与交接；不另建个人版全局看板。最低包含以下信息。

当前迁移记录为 T011。A 的接口指南由 backend-handoff 定位；D 接手先读 T005/T006，确认执行者和 proposed 绑定后再认领。接口指南解释如何消费 A 的交付，不替代任务状态或授予实现权限。

| 信息 | 需要回答 |
| --- | --- |
| task_id、role、module_ids、resource_ids | 做什么，归谁，能改哪些逻辑资源 |
| owner、agent_session、role_confirmation、status | 谁在执行，角色由谁确认，处于哪个阶段 |
| branch、base_commit、checkpoint_commit | 从哪恢复，实际基于什么 |
| acceptance、required_check_refs、depends_on | 怎样完成，依赖谁 |
| completed、remaining、summary | 已有结果和缺口 |
| evidence | 哪条现有命令在哪个 commit 执行，真实结果是什么 |
| artifacts、risks、blockers | 可访问产物、风险与恢复条件 |
| next_steps、history | 下一步具体操作及状态变化原因 |

next_steps 指向模块/资源 ID 和 command_ref；可以附当次解析出的文件位置作为证据，但接手者必须重新解析而不能直接沿用本机路径。时间使用带时区的 ISO 8601，仓库引用相对当前根目录，外部产物用稳定 URI 与 hash。

真实模型执行、离线 fixture 与缓存回放分别标记。passed/failed/not_run 如实填写；保存输出摘要或可访问报告，不把临时本机文件当唯一交接证据。

接手者只读公共入口、当前任务及相关契约，核对位置映射、分支与 HEAD，恢复依赖并重跑一项最小验证，再继续下一步。任务记录能让未参与者指出下一步，才算可交接。

团队试运行先记录恢复用时，以 5 分钟内为改进目标；模板没有替团队完成这次实测，也不承诺零成本。
