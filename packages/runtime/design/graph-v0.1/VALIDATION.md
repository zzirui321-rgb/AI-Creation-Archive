# 本阶段验证证据与限制

日期：2026-10-17，Asia/Shanghai。验证对象是 Graph 解析技术基线、格式、离线组装和设计契约，不是完整生产解析服务。

## 已执行检查

沿用已有 Conda aica / Python 3.12.15 和已安装依赖。没有安装或升级环境。已有 semantic-tests 运行 Runtime/Gateway 测试，包含本阶段新增契约检查；semantic-style 检查相同两个模块。

| 检查 | 实际命令（在仓库根目录） | 结果 |
| --- | --- | --- |
| 生成 Schema、TS、样例 | `conda run --no-capture-output -n aica python packages/runtime/tests/graph_contract_design/export.py` | 生成 19 个文件，含离线规范化的真实模型提议图 |
| 专项 | `conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests` | `a2ed3a889f11392ed6d80e00c5acc2bfc162e0d5`：101 passed in 14.37s；含原有 51 项及新增 50 项 |
| 静态检查 | `conda run --no-capture-output -n aica python -m ruff check packages/runtime apps/gateway` | 通过 |
| TS 严格编译 | 现有 C checkout 的 TypeScript CLI：`tsc --strict --noEmit --skipLibCheck --target ES2022`，输入 `graph.generated.ts` 和 `examples/contract.typecheck.ts` | 通过；同时检查三份图、编辑和布局的字面量类型，无输出文件 |
| 协作结构/引用/DAG | `conda run --no-capture-output -n aica python scripts/validate_collaboration.py` | 16 个任务、27 个绑定通过；最终交付元数据同样检查 |
| 差异 | `git diff --check` / 暂存差异检查 | 通过，最终交付元数据同样检查 |
| 凭证检查 | `conda run --no-capture-output -n aica python packages/runtime/tests/graph_contract_design/audit.py` | 最后检查 37 个文件，实际 API 凭证匹配为 0，不输出密钥 |

TS 编译器从既有 C checkout 只读使用；没有写入 C 工程、安装新工具或改变锁文件。完整 A 录制回归未重跑：本阶段未改 A/生产源码，既有 Windows 符号链接权限失败的历史状态仍保留。

TS 编译通过时的检查点为 `943cfed1402b4c400e8f05ef8d0b06e0c6fca101`；后续修改没有改变生成 TS 或类型样例，已用 Git 差异核对。最新专项/静态检查后的改动仅为说明和任务/状态元数据。

Windows 上并行 Conda 激活曾发生共享临时文件冲突，改为串行后检查通过。一次 Python `-c` 的引号被 Conda/Windows 传参移除，凭证检查未运行，随后改用具名 audit.py 成功；失败输出不含实际密钥。早期两处 Ruff import 排序已修正。

## 正反样例覆盖

- 多轮目标、Agent 提议、用户修订、失败重试的分组与原始映射。
- 并行音频与图像调用，不因时间相邻生成调度依赖。
- 不完整录制、未归组节点和子图边界不隐瞒。
- 证据未知、指针不存在、附件/档案哈希不匹配、错误事件行或身份、快照边界错误。
- 将 Agent 消息归给用户、编造 actor、用时间戳支持用户意图、LLM 自我审核或冒充人工。
- 同一个原始节点被多个步骤拥有、Run/产物归错步骤。
- 历史定义中的执行不静默消失，也不绑定到同名当前定义；明确报告 HISTORICAL_RUN_SCOPE，旧定义缺失仍保留原始 Run。
- 变量实际值/类型伪造、编辑非字面量绑定、非法 append。
- 伪造/删除显式依赖、错误端口、聚合引入执行环；语义环可以保留。
- 生成 Schema 有效、TS/JSON 来源一致、样例与导出内容无漂移；编辑与布局各自带版本。
- 磁盘上的三份原始样例及事件文件实际字节与图中声明的 SHA256 一致；Git text 属性为 unset，避免 Windows 换行转换改变证据。
- 真实模型输出的离线重组与指标复核，不在普通 pytest 中调用模型。

校验能确认引用、类型和部分归因条件，不证明自然语言解释与证据完全蕴含一致。没有将这些检查计为实际语义准确率。

## 两次真实模型探测

使用现有仓库外 DeepSeek 配置，调用官方 HTTPS、校验证书、JSON Output、thinking disabled。每条命令恰好调用一次，自动重试为 0，模型为 deepseek-flash。输入全部为本阶段构造的合成事实，不发送用户私有项目记录，也没有执行图像/音频生成工具。

命令分别为：

```text
conda run --no-capture-output -n aica python packages/runtime/tests/graph_contract_design/probe.py --confirm-external --variant hybrid --output packages/runtime/design/graph-v0.1/evidence/hybrid.json
conda run --no-capture-output -n aica python packages/runtime/tests/graph_contract_design/probe.py --confirm-external --variant hybrid --output packages/runtime/design/graph-v0.1/evidence/hybrid-v2.json
```

| 实际探测 | 原始结果 | 用量 |
| --- | --- | --- |
| [第一次](evidence/hybrid.json) | 4 个步骤，分组与人工期望一致；data/control 错带 semantic 的 relation 字段，组装拒绝。保留失败原文，不计为通过 | prompt 3900 / completion 1722 / total 5622，5.688 秒 |
| [第二次](evidence/hybrid-v2.json) | 将依赖与语义提议改为不同对象形状后，4 个步骤，1 条由 A 明确绑定验证的数据依赖，0 条候选执行依赖，4 个变量，无未归组原始节点；契约通过 | prompt 4179 / completion 1670 / total 5849，5.609 秒 |

共两次调用、11471 tokens，不把第一次说成成功。没有继续付费抽样，也没有进行“直接 LLM vs 混合系统”的消融比较。

原始请求（不含凭证）、响应 content、用量、完成原因和指标均保留。第二次保留的最初组装图把 parse.model 写成了探测器标识 live-probe；探测器已修正为使用实际配置模型，并在不再次调用模型的前提下输出 [规范化派生图](evidence/hybrid-v2.normalized.graph.json)。原始探测记录未修改，离线测试核验派生图及其实际模型元数据。

冻结前也修正了合成样例的时序：用户修改发生在修订执行之前，工具汇总消息发生在对应执行之后。第一次与第二次输入并非完全相同；两次记录只支持格式迭代的可行性，不支持性能提升或语义准确率结论。

## 未验证范围

真实复杂长对话、跨多个工作流定义修订的自动合并、不同项目/用户的授权服务、全量自然语言蕴含校验、生产任务队列/重启/幂等事务、浏览器实接新增端点、运行时重放和机器间访问均不属于本阶段完成项。C 的接口接受与非作者评审仍待进行。
