# T011 交付校验记录

2026-10-16，在 macOS Apple Silicon 的既有 Conda aica 环境（Python 3.12.15）执行。被验证提交为 `58f8932a93940fbc3c00d0bf23a98585ae8e71e3`；后续仅补任务证据、文档及原有修改约束，最终结构检查另行重跑。来源与授权见 [迁移决定](COOP-MIGRATION.md)。

| command_ref | 结果 |
| --- | --- |
| collaboration-validation | 11 项任务、24 个绑定，Schema、资源/命令引用及依赖 DAG 通过 |
| coordination-style、lint-a | 本地协作检查器及 A 两个包的 Ruff 通过 |
| tests-all | 71 passed；含本地真实 MCP/进程操作和离线 fixture，不代表生产平台或模型演示 |
| dependency-check | No broken requirements found |
| validate-real-sample | 已授权 Self-Archive 结构校验通过，没有重放工具 |
| validate-protocol-fixture | 合成协议样例通过 |
| diff-check | git diff --check 通过 |

命令按 collaboration.json 的 argv 执行，使用当前 Conda 环境的解释器和 aica 入口；未安装或升级依赖。

额外人工脚本检查：在临时目录复制公开清单、Schema 和任务，对每个反例单独修改后调用同一 validate 入口，确认拒绝未知命令、重复绑定、accepted 但 evidence 为空、缺失资源和任务自依赖循环。临时目录先 resolve，避免 macOS /var 别名影响路径边界检查。它们是结构反例，不是权限/并发锁测试。

相对 `3bbde7c` 逐字节比较：除五个明确更新的入口/交接 Markdown 外，其余 46 个既有文件一致，包含全部业务源码、测试、环境文件、根档案及已授权引用产物。检查迁移差异中的 39 个相对 Markdown 链接均可定位。

T011 进入 in_review，尚未合入。B/C/D/E 候选绑定待各自确认。未执行远端 CI、跨平台安装、跨 Agent 恢复演练、平台端到端 Replay 或真实模型生成；未开启 GitHub 范围拦截、CODEOWNERS 或分支保护。
