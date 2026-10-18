# AI Creation Archive · AiCA

记录创作过程，理解作品来路，并从已有版本继续创造。

这是 AiCA 团队代码的独立整合副本，汇集 MCP 采集、创作图谱、账号与作品版本、预览与局部编辑，以及本机 3D 扩展。原作者与来源见 [NOTICE](NOTICE.md) 和 [整合来源](docs/INTEGRATION_SOURCES.md)。本仓库从经过筛选的代码快照建立，不包含团队原始 Git 历史、私人录制档案或运行数据库。

## 已收录模块

| 模块 | 内容 |
|---|---|
| MCP 与协议 | 25 个工具；记录、导入导出、本机项目连接与持续授权、可选双向同步 |
| Web 工作台 | 发现、账号、作品图谱、节点详情、Fork、版本保存、Agent 交接导出 |
| Gateway | FastAPI、账号/私有权限、不可变提交、分支、产物读取与任务持久化 |
| B 预览与展示 | Draft/Final、模型适配、局部编辑/Mask 提案、多模态产物与 Original/Fork 对比 |
| 执行 Worker | 独立兼容 Runtime，负责 Fork/依赖/计划/缓存/执行；与语义解析包隔离 |
| 3D 扩展 | Hyper3D Rodin 任务适配、GLB 查看、保存与交接导出；需要配置供应商凭据 |

语义目标定位需要独立 ML 环境与 DINO/SAM2 权重；本库提供接口和验证页，不自动下载模型。供应商适配存在不代表每项真实收费模型已验收；社区图谱也不自动成为可执行 DAG。最新验证和限制见 [集成验收](docs/INTEGRATION_VALIDATION.md)。

## 本地运行

环境：Python 3.12，SQLite ≥3.51.3，以及项目环境清单中的 Node.js。沿用锁文件安装，不修改全局配置。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e packages/schema -e packages/mcp-adapters -e packages/runtime -e apps/gateway
cd packages/artifact-renderers
npm ci
cd ../../apps/web
npm ci
npm run build
cd ../..
.venv\Scripts\python scripts/serve.py --port 8088
```

macOS/Linux 对应使用 `.venv/bin/python`。若当前 npm 限制锁文件中的镜像地址，可按锁文件所使用的来源指定 registry；不要关闭全局安装保护。

默认只监听 `127.0.0.1`，数据保存在工作区外的当前用户应用数据目录。端口占用时使用其他 `--port`。使用 `--data-dir` 指定独立数据位置。服务不会自动注册开机启动。

## 使用 MCP

在真实创作目录启动，而不是本源码仓库：

```sh
aica serve --project /absolute/path/to/creation --per-turn --client codex
```

没有项目授权时按逐轮策略确认。`aica connect --server https://your-platform.example` 通过本机页面登录并明确授权当前项目；持续授权、可选双向同步、暂停和断开的含义见 [MCP 使用说明](packages/mcp-adapters/USAGE.md)。安装本库不代表授权采集、上传或公开作品。

## 目录

```text
apps/web/                         Web 工作台
apps/gateway/src/aica_gateway/     平台 API、权限与版本
apps/gateway/src/preview/          B 预览、编辑和模型适配
packages/schema/                  共享归档与工具契约
packages/mcp-adapters/             本地 MCP 与 Host 接入
packages/runtime/                  语义分析与图解析
packages/artifact-renderers/        产物、编辑目标和对比组件
compat/execution-worker/           隔离的可执行 Runtime
scripts/serve.py                   统一本地启动入口
```

历史模块文档保留了原交接背景；当前可运行入口、来源和验证以本 README 及 `docs/INTEGRATION_*.md` 为准。完整运行需要安装依赖；本库不附带密钥、模型权重、私人数据或预先授权的账号。
