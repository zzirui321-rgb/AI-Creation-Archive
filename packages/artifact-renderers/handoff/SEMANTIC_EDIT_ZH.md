# B 自动目标定位与 Mask 提案

本地任务：`B-semantic-edit-targets`，角色 B 来自用户在本会话的明确分工。
分支：`feature/b-semantic-edit-targets`，HEAD 仍为 `300a582`，既有未提交工作已保留。
资源：preview-service / artifact-components。可写位置仅为 `apps/gateway/src/preview/`
及 `packages/artifact-renderers/`。本轮没有提交、推送或更新 GitHub PR。

## 本轮实现

单个 Graph 节点仍可输出一张完整图片。D 控制节点级 Dirty / Cache / Replay，B 在
节点内部用原图、语义 Mask 和确定性合成保护像素，不要求拆分图层节点。

1. Grounding DINO 根据目标描述定位候选，SAM 2 生成原尺寸轮廓 Mask。
2. 提案绑定原图 SHA256、Mask SHA256、目标、操作、模型及固定 revision。
3. 一个满足尺寸和检测分数策略的候选可直接交给执行；多个目标或低检测分数要求确认。
   检测分数不是正确率或经过校准的可信概率。
4. 背景替换会分别检测所有要求保护的目标，合并 Mask 后取反。任何一个目标未找到则阻止执行。
5. 追加使用独立对象 alpha 叠加；摆放提案按纹理密度和已有保护对象筛选，明确标为
   placement_proposal，不冒充语义分割，并要求确认位置。
6. 提案过期、Mask 损坏、来源不一致、服务超时或目标未找到时不调用生图服务，不回退整图重画。

## Python 接口

```python
from preview import EditIntent

proposal = await engine.propose_image_edit(original_artifact, EditIntent(
    operation="replace",       # replace / attribute / background / append
    instruction="Replace the fox with a mechanical robot",
    targets=["fox"],
))

result = await engine.execute_proposed_edit(
    node, artifacts, context=context,
    original_artifact_id=original_artifact["id"],
    proposal_id=proposal["id"],
    candidate_id=selected_id,   # ready 时可省略；needs_confirmation 时必填
)
```

`background` 的 targets 是要保留的前景对象；`append` 的 targets 是不能覆盖的已有对象。
追加的 instruction 只描述新增对象。没有 targets 时追加仅给出几何摆放候选，仍必须确认。
`replace` 的轮廓会扩张原图短边 2%（最多 31 像素）以预留替换空间，实际范围可在预览中核对；
`attribute` 保持紧贴检测目标，目标描述应具体到衣服、配饰等需要编辑的部分。

提案状态：ready / needs_confirmation / needs_target / not_found / unavailable。
候选 Mask 是标准 A Artifact，可计算真实 SHA256；图上方框只是候选选择控件，
绿色覆盖层来自真实 Mask，不能把检测框当成分割轮廓。

`result.metadata.edit_proposal` 保存本次提案与确认记录，`metadata.edit_target` 保存
可以进入标准节点 execution.params 的稳定执行依据。缓存不使用随机提案 ID：
同内容提案可以复用生成结果，模型 revision 或 Mask 内容变化会使缓存失效。

## A / C / D 交接

### A

没有新增 `.aica` 协议版本。原图及 Mask 使用现有 Artifact，语义执行依据使用
execution.params 和已有 B 扩展 / sidecar。候选定位是模型推断，不作为人工标注真值。

### C

新增通用组件 `EditTargetPicker`，支持真实 Mask 覆盖、候选框和列表选择、重试、
原图变更校验、图片加载失败保护和桌面 / 手机布局。

```tsx
<EditTargetPicker
  artifact={originalArtifact}
  proposal={proposal}
  resolveUri={resolveAuthenticatedArtifactUri}
  onConfirm={({ proposalId, candidateId, sourceSha256 }) => replay({
    proposalId, candidateId, sourceSha256,
  })}
/>
```

组件只返回不可变选择身份，不接收任意客户端文件路径。API Key 不进入提案或组件存储。
主平台 C 页面本轮未改；独立 B 验证页不代表已经装配进主站。

### D

生产路由必须沿用账号、项目和 Artifact 权限校验。给 B 的原图应先通过授权读取并落入
该用户的 ArtifactStore；提案与 Mask 也应按用户 / 项目隔离，不能共享本地验证服务的数据目录。

正式归档时捕获 Mask 的真实字节，生成 D 的 Artifact ID，按内容 Hash 重绑：

```json
{
  "execution": {
    "tool": "image_gen",
    "version": "b-generic-3",
    "inputs": {
      "original_image": {"kind":"artifact", "artifact_id":"historical-image-id"},
      "edit_mask": {"kind":"artifact", "artifact_id":"captured-mask-id"}
    },
    "params": {
      "edit_mode":"masked_replace",
      "edit_operation":"replace",
      "edit_target":"使用 result.metadata.edit_target 的完整对象替换此说明字符串"
    }
  }
}
```

上例仅为字段说明，不是完整可导入档案。保留原有 kind、模型、输出端口、Seed 等字段。
Prompt 及变量绑定由 D 根据实际 Fork 操作维护；B 不猜变量名或改写原 Graph。
params.edit_target 的 source_sha256 和 mask_sha256 必须与输入字节一致。
准备好标准输入后，现有注册的 image_gen 执行器即可执行，无需在重放时再次调用分割模型。
不要给节点添加依赖自己的 node_output 边，应引用已有历史 Artifact。
只保存本地提案 ID、不保存 Mask 文件和上述绑定，不能算可移植重放。

## 本地服务

验证页：http://127.0.0.1:5177/semantic-edit.html
独立 B 验证 API：127.0.0.1:8102；分割服务：127.0.0.1:8101。
现有主站 8088 和生图模型 8000 没有被替换或关闭。

服务使用既有 Conda Python；分割使用既有 ml 环境的 Torch 2.11.0+cu130 和
torchvision 0.26.0+cu130，在 CPU 上运行，不占用生图模型的 GPU 显存。
该环境默认用户级 Torch 覆盖了 Conda 包；启动采用 `-s` 和 `--user-site-last`
使现有 Conda 包优先、缺失的已有依赖从用户目录补充，没有安装或升级包。

模型权重在本地应用数据的 `AiCA/segmentation-models`，不在 Git 中。
本次 Hugging Face 原站连接超时，从可达的 hf-mirror 镜像下载了固定 revision 的快照；
运行服务使用缓存和 local_files_only，不远程加载 Python 代码。

- IDEA-Research/grounding-dino-tiny：a2bb814dd30d776dcf7e30523b00659f4f141c71
- facebook/sam2.1-hiera-tiny：de431c4043854a71d8101e17995dfe596bf101a5

使用支持这些模型的已有 Python，从本 checkout 启动分割服务：

```powershell
# 仅当既有环境有相同用户包覆盖问题时使用 -s / --user-site-last。
& $MLPython -s apps/gateway/src/preview/scripts/segmentation_service.py --user-site-last --cache-dir $ModelCache --port 8101
```

B 配置 `AICA_SEGMENT_API_URL=http://127.0.0.1:8101/segment`。
可选 `AICA_SEGMENT_API_KEY` 不写入档案；分割单次请求默认总时限 120 秒，和生图的
默认 10 秒保护分别配置。该配置不会自动给主平台已有进程热更新环境。

验证 API 及页面：

```powershell
$env:PYTHONPATH="$PWD/apps/gateway/src;$PWD/packages/schema/src"
$env:AICA_SEGMENT_API_URL='http://127.0.0.1:8101/segment'
& $Python -m preview.scripts.run_edit_review --data-dir $ReviewData --port 8102
# 另一个终端，在 packages/artifact-renderers 中：
npm.cmd run dev -- --port 5177 --strictPort
```

本地验证 API 是 loopback 独立工具，只有 Origin 限制，没有平台账号系统，不能公开部署。
实际运行日志和服务状态位于本地应用数据 `AiCA/semantic-edit/`，不写入仓库。

## 能力边界

目前接收结构化 EditIntent。D 可利用已有 Override / Append 和变量信息形成意图；
本轮没有实现任意长指令的自动拆解，也没有私自调用云端文本模型翻译。
Grounding DINO 的目标短语以英文表现更可靠；中文等目标不支持时会返回未找到，
不会把固定人物或模糊几何 Mask 伪装成识别结果。生图 instruction 原文独立保留。

自动 Mask 的语义正确性仍取决于模型，多个实例、复杂指代、遮挡、毛发和透明物体可能失败。
保护区像素不变不等于目标识别一定正确，也不等于编辑模型一定生成了正确身份 / 构图。
旧主体背后的不可见背景需要模型补全；扩张范围内允许变化。不能承诺任意输入无缝编辑。
用户框选、精细画笔修正仍可作为 C 后续补充入口，本轮没有冒充已经实现。

## 验收证据

- 新增 Python 专项覆盖单目标、多目标确认、背景保护、目标缺失、追加摆放、过期提案、
  损坏 Mask、模型版本缓存隔离、响应大小 / 超时和本地 API 边界。
- B 全量 pytest 113 项通过；最终本地验证路由缓存修正后，语义编辑专项 17 项再次通过。
  Ruff check、format --check 和 git diff --check 通过。
- 新增 9 项前端测试；B 前端共 43 Vitest、typecheck 和包含新页面的生产构建通过。
- 真实 Grounding DINO + SAM 2 在单狐狸图上输出一个目标，在双狐狸图上输出两个目标，
  后者要求确认，没有自动生图。没有使用人工描边或白底抠图来冒充这些语义 Mask。
- 实际本地 FLUX 编辑后，381,694 个保护像素 RGBA 最大误差为 0。
  自动保护狐狸和气球后换雪景，24,290 个前景保护像素 RGBA 最大误差为 0。
- 桌面 / 手机截图、真实 PNG、Mask 和无凭据报告位于本地忽略目录
  `.c-integration/semantic-edit/` 及 `.c-integration/semantic-background/`。
- 最终重启验证 API 后，浏览器再次生成同内容提案，Replay 为 CACHE_HIT；
  原图和 Mask 均实际加载，双目标必须先选择，桌面 / 手机无横向溢出、无页面异常。
- 本轮未更改 A/C/D 目录、环境清单、锁文件，也未上传权重、截图、缓存、密钥或个人路径。
