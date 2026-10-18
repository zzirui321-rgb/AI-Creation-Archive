# AiCA 本地开发环境

每位成员使用独立的 `aica` Conda 环境。Python 依赖由根目录 `requirements.txt` 固定；生图供应商尚未确定。

## 创建与安装

推荐在项目根目录使用共享环境文件：

```sh
conda env create --file environment.yml
conda activate aica
pip install --no-build-isolation -e packages/schema -e packages/mcp-adapters
pip check
```

Conda 先安装基础工具，再通过环境内的 pip 安装 `requirements.txt`。这两份文件需要同时保留。

`environment.yml` 使用清华 TUNA 的 conda-forge 镜像，并通过 `nodefaults` 排除默认频道；本项目不修改全局 `.condarc`。配置依据 [清华镜像站说明](https://mirrors.tuna.tsinghua.edu.cn/help/anaconda/)。此设置只影响 Conda 包，pip 软件源独立配置。

也可以分步安装，先安装 Conda 包，再安装 pip 包：

```sh
conda create -n aica --override-channels -c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge python=3.12.15 pip nodejs=24.21.0 git 'libsqlite>=3.51.3'
conda activate aica
pip install --requirement requirements.txt
pip install --no-build-isolation -e packages/schema -e packages/mcp-adapters
npm install --global --prefix "$CONDA_PREFIX" pnpm@10.34.7
pip check
```

上面的 npm 命令适用于 macOS/Linux；PowerShell 中将 `"$CONDA_PREFIX"` 替换为 `"$env:CONDA_PREFIX"`。pnpm 安装到当前 Conda 环境，不使用其他环境的 npm 全局目录。

Node.js 与 pnpm 是前端工具链。React、React Flow、TypeScript 等依赖在前端开发开始时管理，当前没有初始化前端应用。FFmpeg 在需要本地视频合成时再安装，生图仅用于 Demo 展示。

## 日常使用

```sh
conda activate aica
python3 --version
node --version
pnpm --version
pip check
```

Windows 可使用 `conda run -n aica pip check` 检查环境；Python 可执行文件名称以当地 Conda 安装提供的命令为准。

## 当前目录

```text
AiCA/
├── AGENTS.md
├── README.md
├── environment.yml
├── requirements.txt
├── apps/
│   ├── web/
│   └── gateway/src/preview/
├── packages/
│   ├── schema/
│   ├── mcp-adapters/
│   ├── runtime/
│   └── artifact-renderers/
├── examples/
│   ├── ai-comic/
│   └── self-archive/
└── docs/
    └── SETUP.md
```

目录职责与产品边界见根目录 `AGENTS.md`；A 由 Alvintxh 负责，其他成员与角色的对应关系待确认。协议与本地 MCP 使用方式见 `packages/schema/README.md` 和 `packages/mcp-adapters/README.md`。

## 安装状态与排查

2026-10-16 已在本机 macOS Apple Silicon 完成安装：Python 3.12.15、Node.js 24.21.0、pnpm 10.34.7、Git 2.56.0，均位于 `aica` 环境。Conda 包来自清华镜像，Python 包通过环境内 pip 从本地下载缓存安装。`pip check`、主要依赖导入和 SQLite 内存连接检查通过；其他操作系统仍需实际安装验证。

如果创建失败，先用 `conda env list` 确认是否已有 `aica`。若环境不存在，可重新执行创建命令；若基础环境已存在但 pip 安装失败，可激活后重新执行 `pip install --requirement requirements.txt` 和 `pip check`。不要删除或修改其他项目的环境。
