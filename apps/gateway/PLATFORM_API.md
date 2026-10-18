最新部署、HTTPS、MCP项目令牌与便携JSON支持见 [部署交接](deployment/README.md)。

# Graph 仓库平台 MVP — D / C 接入契约

原始 `.aica` / `aica.json` 现可经鉴权 `POST /api/projects/{p}/archive-json` 上传（application/json，不设原网站5MiB/16MiB限制），返回 `import_id`、`archive_sha256`、`missing_artifact_ids`；原字节保存、同项目同内容幂等。随后复用 semantic-parse-input/semantic-parses，再经 semantic-commits 上库。JSON支持A的内嵌附件扩展，未携带的引用附件如实报告；不会抓取外部URL或读取另一台电脑。前端原始A档案入口已统一走用户审阅确认后的AI提炼，只有已提炼Graph直接展示。

后续本机部署阶段已在本 checkout 的 C 工作副本接入页面，见 [部署与数据库说明](deployment/README.md)；以下保留平台 API 阶段契约及当时的前端接入边界。

本阶段提供平台账号、仓库、不可变 Graph 版本、分支和 Fork。D 实现位于 `src/aica_gateway/platform.py`，复用原 Gateway 的 SQLite 事务、数据目录锁、A 档案校验和产物存储。C 的下载快照保持不变；本文件和 `integration/platform-client.mjs` / `.d.mts` 是交给 C 的接口与客户端，不代表现有 C 页面已经切换到服务端。

## 运行

使用既有 Conda `aica` 环境及已安装的 Gateway，不安装新依赖。仓库根目录：

```powershell
conda activate aica
$env:AICA_DATA_DIR = Join-Path $env:LOCALAPPDATA 'AiCA/graph-platform'
$env:AICA_API_TOKENS = '{}'
$env:AICA_CORS_ORIGINS = '["http://127.0.0.1:5173","http://localhost:5173"]'
aica-gateway --host 127.0.0.1 --port 8000
```

数据目录必须在 checkout 外；一个目录只允许单实例、单 worker。新账户不需要预置开发令牌；原有令牌仍能访问原 `/api/projects` 接口，但账号仓库接口要求注册账号。`user:` 主体前缀保留给账号，禁止配置同前缀开发主体。OpenAPI：`http://127.0.0.1:8000/docs`。

这套启动方式面向本地开发验收。本阶段没有公网部署、OAuth、邮件验证/密码找回、协作者邀请、PR/Merge、真实工具执行或 B 的生图预览实现。公开部署前需独立验收 HTTPS、部署层限流、运维与账号恢复机制。

## 数据与权限

- `users`：平台 UUID、唯一小写 username、显示名、随机盐及 scrypt 哈希。username 是 3–32 字符的小写字母/数字/连字符，首字符为字母；密码 12–128 字符。没有默认演示账号。
- `sessions`：随机 Bearer 会话，仅保存 SHA-256 摘要；7 天过期、单会话注销、重启后仍有效。客户端令牌只保存在内存，刷新后重新登录；不能使用模型供应商 API Key 登录平台。
- `repositories`：账号下唯一、不区分大小写的 name、title、description、private/public 可见性、创建时间及 Fork 的父仓库/父版本。默认私有。
- `repo_commits`：不可变完整文档、内容哈希、作者、消息、父版本、类型及可选档案快照引用。
- `repo_branches`：仓库内唯一的分支名和 head。默认 `main`；支持 `experiment/cat` 形式。分支是平台 Graph 版本指针，不创建本机 Git 分支，也不修改档案内部 `graph.branches`。

私有库只有所有者读写，其他主体得到 404；公开库可匿名读，写入仍仅所有者。公开列表不泄露私有 Fork，`forks` 只统计公开子库。账号资料不包含密码、哈希或令牌。

提交必须显式携带 `expected_head`：空分支用 null，其他分支用当前 head。数据库写事务原子校验并移动指针，过期版本返回 409，不覆盖他人刚完成的提交。失败不会发布候选版本。

Fork 复制所选分支 head 的文档和不可变附件引用到自己的新仓库 `main`，记录父仓库、父版本和 source_commit。Fork 后两库的分支与提交互不影响。它继承所选快照，不复制所有源分支/历史；源库后续设为私有不会撤回已被合法 Fork 的副本。公开任意分支/历史或 Fork 到公开库意味着分享其中全部记录和附件，用户需事先检查隐私。

## API

所有路径以 `/api` 开头，写操作和 `scope=mine` 需要 `Authorization: Bearer <平台会话>`。公开读取可省略认证，提供无效令牌时返回 401。所有 API 响应禁止缓存。

| 方法 / 路径 | 输入 / 行为 |
| --- | --- |
| POST `/auth/register` | `{username,password,name}`；201，返回 user，不自动登录 |
| POST `/auth/login` | `{username,password}`；返回 access_token、token_type、expires_in、user |
| GET `/auth/me` | 当前账号 |
| POST `/auth/logout` | 注销请求中的会话 |
| POST `/repos` | `{name,title,description?,visibility?}`；201，空 `main` |
| GET `/repos?scope=public&q=&offset=0&limit=50` | 公开发现页；scope=mine 为自己的库；limit 1–200 |
| GET `/repos/{r}` | 仓库信息与 branches，包括 owner_username、owner_name、default_branch、forks |
| PATCH `/repos/{r}/visibility` | `{visibility:"public"或"private"}`；发布/取消公开 |
| GET `/repos/{r}/branches` | 分支列表 |
| POST `/repos/{r}/branches` | `{name,from_branch?:"main",from_commit?:UUID}`；201，源版本必须属于本库 |
| POST `/repos/{r}/commits` | `{branch?:"main",expected_head:UUID或null,message,document}`；201 |
| POST `/repos/{r}/archive-commits` | `{branch?,expected_head,message,project_id,import_id}`；仅引用自己已导入的档案 |
| GET `/repos/{r}/commits?branch=main&offset=0&limit=50` | 当前分支父链历史，最近在前 |
| GET `/repos/{r}/graph?branch=main` | `{status,commit,document}`；空分支 404 |
| GET `/repos/{r}/graph?commit_id=UUID` | 固定版本，必须属于本库 |
| GET `/repos/{r}/artifacts/{a}?commit_id=UUID` | 该版本的真实打包产物，按仓库权限读取 |
| POST `/repos/{r}/forks` | `{name,title,description?,visibility?:"private",branch?:"main"}`；201，返回新库及 head |

响应通常包含 `status:"success"` 和对应对象；错误沿用 `{status:"error",code,message}`。主要状态：401 会话错误、404 不可访问/不存在、409 名称重复/版本冲突/空库 Fork、413 超限、422 校验失败、429 认证限流、503 存储失败。429 携带 Retry-After。15 分钟内每来源最多 10 次注册、50 次登录，每用户名最多 10 次登录尝试；仅识别直连来源，不信任客户端 X-Forwarded-For。

## 上库格式

1. C 的 `aica-ui-module-record` 导出：顶层 steps、edges、golden_path 原样提交。也支持当前 `Workflow` 的 modules、edges、goldenPath。后端检查 1–200 模块、ID 唯一、依赖引用和 DAG、提示词长度及 Golden Path 引用。类型为 `ui-module-record`，不能据此执行工具或宣称完整正式协议。
2. 正式 `.aica` JSON：只要提供 aica_version 就必须通过 A 的 `require_archive`，类型为 `aica`；不能用不完整档案绕过协议校验。JSON 里的本机 URI 不会自动变成平台可读取文件。
3. 正式档案和附件：先复用 POST `/api/projects`、POST `/api/projects/{p}/imports` 的 A ZIP 契约，再用 `/archive-commits` 上库。项目/快照必须属于登录者。仓库产物接口只能读取固定 commit 引用的快照，不授予读原私有项目的权限。

已取消文档总大小5MiB限制，嵌套仍最多40层，拒绝常见凭证字段；这不能识别自由文本里的所有秘密，发布前仍需审阅。服务不会抓取远程 URL、执行上传代码或发起模型调用。图片按安全 MIME 展示，其余下载；浏览器通过带认证头的 Blob 请求加载私有图片，不能把会话放到 URI。

## C 客户端接入

将 `integration/platform-client.mjs` 和 `.d.mts` 一起接入 C 的 API 层，不替换 A 的协议类型。此 SDK 无第三方依赖，可在 Vite 或 Node 运行，拒绝重定向，内存保存令牌，401 清除会话，409 交由 UI 提示重新读取/比较。

```javascript
import { PlatformClient } from './platform-client.mjs';
const platform = new PlatformClient('http://127.0.0.1:8000');
await platform.register({ username: 'creator', name: 'Creator', password: userEnteredPassword });
await platform.login('creator', userEnteredPassword);
const { repository } = await platform.createRepository({ name: 'my-creation', title: '我的创作' });
const { commit } = await platform.commit(repository.id, exportedRecord, { expectedHead: null });
const loaded = await platform.graph(repository.id);
const workflow = parseWorkflow(JSON.stringify(loaded.document)); // C 现有展示适配函数
await platform.createBranch(repository.id, 'experiment/style');
await platform.commit(repository.id, updatedRecord, { branch: 'experiment/style', expectedHead: commit.id });
await platform.setVisibility(repository.id, 'public');
```

C 接入位置：`auth.ts` 用账号会话替换演示登录；`workspace.ts` 的保存/读取改为 SDK 的库/分支/提交；`community.ts` 的发现页用公开库列表；`Studio.tsx` 的发布调用 setVisibility，Fork 调用 fork，编辑保存携带加载时的 commit.id；Graph 继续使用 `workflow.ts` 解析返回的 document。SDK 返回的仓库 UUID 替代 local-* ID，不自动把原 localStorage 数据上库。

读取 ZIP 产物：`platform.artifactBlob(repoId,commitId,artifactId)` → `URL.createObjectURL(blob)` → 写入 C 展示模块的 artifact.uri，组件卸载时 revokeObjectURL。原档案 URI 保留溯源，不直接用于另一台电脑的媒体加载。OAuth 按钮继续明确未配置；供应商 API Key 仅属于 B 预览流程，与平台令牌分开。

## 验证

```sh
conda run --no-capture-output -n aica python -m pytest -q -c apps/gateway/pytest.ini packages/runtime/tests apps/gateway/tests
conda run --no-capture-output -n aica python -m ruff check packages/runtime apps/gateway
conda run --no-capture-output -n aica node --test apps/gateway/integration/platform-client.test.mjs
```

`test_platform_client_roundtrip.py` 启动临时本机 Uvicorn，运行 `platform-roundtrip.mjs` 完成真实 HTTP 注册、登录、JSON 上库、公开/私有查询、分支、Fork、冲突、ZIP 上库、真实图片读取、注销。临时服务结束后退出；没有供应商调用或部署。SDK 单测中的 fetch 拦截另行标记。最终结果、命令和阶段边界见 `docs/STATUS.md`。
