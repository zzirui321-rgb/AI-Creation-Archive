# 跨 Agent 加载公共规范

公共行为只有一份 AGENTS.md。适配文件只引导读取，不重复复制角色权限和业务路径。不同版本客户端可能有开关、上下文与权限差异，启动时核对是否真正加载。

Codex 使用 AGENTS 入口；Claude Code 可由 CLAUDE.md 导入公共规范；Cursor 和 Copilot 可用各自仓库指令入口指向同一文件。接入时保留现有配置，不直接覆盖用户已有规则。其他工具显式附上公共规范和清单，前提是具备仓库读取能力。

可复制给各工具的短指令：

> 读取公共 Agent 规范和协作清单；发现当前仓库与既定环境，确认逻辑模块/资源绑定，再恢复指定任务。禁止凭目录名字假设职责，禁止改开发环境。交接写回同一任务记录。

来源分支列出的官方参考入口（本次迁移未重新核对客户端行为）：

- [OpenAI AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Claude Code Memory](https://code.claude.com/docs/en/memory)
- [Cursor Rules](https://cursor.com/docs/rules)
- [Copilot repository instructions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions)
- [GitHub CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) 与 [保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
