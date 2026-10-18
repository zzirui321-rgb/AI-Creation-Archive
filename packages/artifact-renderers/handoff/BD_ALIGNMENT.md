# bd First Edition 对齐记录

## 当前基线

用户确认本次统一以 `bd_First_Edition_16.29` 为 A/C/D 来源。
远程实际提交为 `f0fc2942d517c793447140d93670cf5428164a83`；不存在另一个名为
`bd_First_Edition` 的远程分支。核对时该分支没有比 f0fc294 更新的提交。

B 的源版本位于 `feature/b-generic-preview` / PR #11。按用户明确要求，
本轮将该源版本的 B 目录直接同步到 `bd_First_Edition_16.29`。
实际提交只包含 `apps/gateway/src/preview/` 和 `packages/artifact-renderers/`，
不改动 A/C/D 源码，不将 C/D 装配补丁自动应用到队友目录。

## 本轮对齐

- B 的协议回退源、传输校验测试和前端生成类型统一指向 f0fc294。
- 重新生成 `schema.generated.ts`，不把 A 独立分支的内嵌产物扩展当作整合基线已定义的强类型。
- `c-provider-settings.patch` 的目标本来就是 f0fc294；新增自动检查证明它可以直接应用到该分支的前端。
- 本地独立副本 `bd-first-aligned` 从这个远程提交建立，A/C/D 源码保持该基线，加入 B 和 C 装配补丁。
- 原 `local-live-preview` 的真实模型证据与历史工作保留，不用旧目录的数据冒充新的统一基线测试。

## 新增直接验证

`tests/test_platform_alignment.py` 将 B 执行生成的真实 Run、文本/JSON 和合成 PNG
导出为完整 ZIP。随后在独立子进程中加载 Git 导出的 f0fc294 A/D 原码，执行：

1. A 的 `verify_bundle` 和 `require_archive` 验证。
2. D 的实际注册、登录和平台项目创建。
3. 导入 B 的 ZIP，核对原始档案哈希。
4. 创建私有仓库，经 `archive-commits` 保存新的不可变版本。
5. 从仓库版本接口读取每个产物，逐一核对 SHA256。
6. 检查匿名用户不能读取私有档案或产物。
7. 准备真实语义解析输入，不提交模型请求。

另一个测试从 Git 导出同一基线的 C 源码，对交接补丁执行 `git apply --check`。
所有 Git 导出、测试数据库、产物及报告均留在临时目录，不提交到 Git。
子进程仅继承运行必需的环境变量，不转发调用者的其他 API 凭证。

这些检查走实际 FastAPI、SQLite、校验器和真实产物字节；传输为进程内 HTTP 验收，
不是公网部署，也不是付费模型联调。

## 必须保留的边界

f0fc294 的 Gateway 有账号、仓库、档案导入、语义解析与证据接口，
但没有 `/api/projects/{project_id}/executions`，也没有旧的执行 registry/engine。
新增检查验证其 OpenAPI 中确实不存在原生 Replay 路由。

因此 `d-runtime.patch` 与 `d-request-providers.patch` 仍只适用于
`test@6b98aa4` 的隔离执行 Worker，不能直接应用到 bd 的同名 Python 包。
这个 Worker 所需的 A 兼容依赖 a0cc735 也单独标识；它不替换 bd 平台的 A。
`prepare_runtime.py` 中的 `RUNTIME_A_BASELINE` 明确表达这一点。

统一的社区账号执行授权、repo/commit 与 Runtime project/archive/job 绑定、
执行结果自动回写和正式服务整合仍需 D/C 收口。
不能因为 B 的产物可以导入并保存到仓库，就声称这些自动步骤已经实现。

## 检查命令

使用已有环境，在 B 提交分支运行：

```sh
python -m pytest -q -c apps/gateway/src/preview/pyproject.toml apps/gateway/src/preview/tests
python -m ruff check apps/gateway/src/preview
python -m ruff format --check apps/gateway/src/preview
```

前端在 `packages/artifact-renderers` 中运行 `npm run generate:contract`、
`npm run typecheck`、`npm test` 和 `npm run build:demo`。

原 66 项测试加本轮 2 项整合契约检查共 68 项。历史真实本地 FLUX 验收和
三类离线档案验收见 `PROVIDER_COMPLETION.md`；本轮平台契约测试不调用模型。

本轮结果：B 完整 pytest **68/68 通过**；B typecheck、33 Vitest、构建与
Ruff check/format 通过。原版 bd 前端应用交接补丁后，构建与 **31 Vitest**
通过。A/D 平台原码只在独立测试进程加载；未修改该远程分支源码。
