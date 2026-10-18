# C / D 交接说明（B 分支）

本文件只描述 B 分支中已经提供的 C/D 对接代码和交接边界。C、D 的源目录没有复制到本分支；这样可以保持 Owner 目录隔离，并避免把平台账号、数据库或前端路由的实现混进 B。

## 已核对的远程来源

- C：`feature/c-frontend-mvp@baa6908`，负责动态 Archive 导入、Graph 展示、Fork 变量操作、Provider 设置和预览界面。
- D：`feature/d-backend@78ff5fa`，负责平台 Archive 导入、快照和语义分析；它不是带执行 Worker 的 Runtime 分支。
- D Runtime 执行 patch 的基线仍是 `test@6b98aa4`。B 目录中的 `handoff/integration/d-runtime.patch` 和 `d-request-providers.patch` 是可审阅的具体修改，不会自动修改 D 源码。

## B 已提供的具体代码

### 给 C 的代码

`packages/artifact-renderers/src/cIntegration.ts` 提供协议驱动客户端和类型：

- `ArchiveClient.snapshot()` 读取带 revision 的完整 Archive；
- `ArchiveClient.fork()` 提交 Keep / Override / Append 操作；
- `ArchiveClient.plan()` 读取指定 `draft` / `final` 阶段的真实缓存计划；
- `ArchiveClient.execute()` 创建明确授权的执行任务；
- `ArchiveClient.job()` 轮询任务，`cancel()` 取消任务；
- `ArchiveClient.bytes()` 下载并校验 Artifact SHA256 和大小；
- `loadArchiveAssets()` 加载媒体并解析 `aica-preview/1` sidecar。

C 的页面只需要把平台认证后的 `fetch` 注入 `ArchiveClient`，并给 `ArtifactRenderer` / `ArchiveDiffViewer` 传入授权 URI resolver。不要把 API Key 写入 Archive、localStorage、Run、缓存或组件状态。

图片局部编辑使用 B 的 `EditTargetPicker`：C 展示候选 Mask，用户确认后把 `proposalId`、`candidateId` 和 `sourceSha256` 交给 D 的执行入口。组件不会接收本地文件路径，也不会自行调用模型。

### 给 D 的代码

`apps/gateway/src/preview/runtime_bridge.py` 中的 `register_preview_tools()` 把 B 适配器注册到 D 的默认 Registry：

```python
register_preview_tools(default_registry(), settings)
```

注册工具包括：

- `image_gen` / `image-provider`
- `layout_compose` / `compose` / `layout`
- `text_materialize`
- `json_materialize`

`PreviewEngine.execute_plan()` 接收 D 已经排序的 `PlanStep`、初始缓存 Artifact、缓存输出和缓存哈希。D 决定 `CACHE_HIT / RECOMPUTE / REBUILD`；B 不从 Prompt 猜脏传播。

每个 `ExecutionContext` 至少应提供：

```python
ExecutionContext(
    run_id="...",
    cache_key="...",
    mode="draft" or "high_quality",
    decision="RECOMPUTE" or "REBUILD",
    resolved_inputs={...},
    seed=stable_seed,
    node_states={...},
)
```

漫画 Panel 若需要跨节点一致性，D 还要传 `reference_artifact_ids` 和 `consistency_context`。B 会验证引用 Artifact 的 MIME、SHA256、角色映射和 Seed 冲突，并把上下文纳入指纹。

D 的执行器负责：

1. 计算真实 DAG Dirty Plan；
2. 对 `CACHE_HIT` 直接复用旧 Artifact，不调用 B；
3. 调用 B 执行 `RECOMPUTE` / `REBUILD`；
4. 通过 A 的 capture/Run/Artifact 协议保存返回字节和 sidecar；
5. 将新的 Run 决策、Artifact ID、stage 和 revision 写回不可变快照。

B 不持有 D 的项目权限、Job 状态或数据库连接。

## Provider 和执行请求

C 传给 D 的 `image_config` 结构与 B 的 `request_config.ImageConfig` 对齐：

```json
{
  "provider": "sdcpp",
  "api_base": "http://127.0.0.1:8000/v1/images/generations",
  "api_key": "session-only-key",
  "model": "local-model",
  "draft_model": "draft-model",
  "hq_model": "final-model",
  "timeout_seconds": 10,
  "allow_fallback": false
}
```

Key 只在请求和进程内存中存在。D 应将其转换为独立 Registry，不能写入 Job payload 或 Archive。B 的 Provider cache 会区分真实 API 与 Local Fallback，兜底产物不能污染真实模型缓存。

## 可审阅的具体 patch

以下文件已经位于本 B 分支的 `packages/artifact-renderers/handoff/integration/`，只作为 C/D 负责人审阅和应用的代码补丁：

- `c-assembly.patch`：C 通用 Archive 执行工作区装配；
- `c-provider-settings.patch`：C Provider 设置、Draft/Final 请求和会话 Key；
- `d-runtime.patch`：D Runtime 计划、阶段缓存和 composition `REBUILD`；
- `d-request-providers.patch`：D 每请求 Provider Registry 和兜底缓存隔离；
- `acceptance-examples.patch`：三类通用 `.aica` 验收输入。

应用前必须先确认目标分支和基线：

```powershell
git apply --check packages/artifact-renderers/handoff/integration/c-assembly.patch
git apply --check packages/artifact-renderers/handoff/integration/d-runtime.patch
```

这些 patch 不应直接应用到 `feature/d-backend@78ff5fa` 的平台导入代码。D 若要接入执行 Worker，应使用与 `d-runtime.patch` 声明一致的 Runtime 基线，或由 D 提供等价接口后再更新 B 的适配层。

## 验收边界

在 B 分支中已验证：Python 预览测试 118 项通过，Artifact Renderer 的 Vitest 43 项通过，TypeScript 类型检查通过。C/D 合并后还必须验证真实 `/forks -> /plan -> /executions -> /archive` 流程、A Artifact hash 校验、Draft/Final 两阶段和浏览器授权 URI。
