# 单图节点的受控局部编辑

本轮仅本地开发，等待用户明确指令后才能提交或推送。
角色 B 来自本会话的明确分工；实际实现资源为 preview-service 和 artifact-components。
本地任务分支：`feature/b-single-image-local-edit`。沿用既有 Python、依赖和部署。

## Graph 与像素编辑的边界

一个 Graph 节点可以输出一张完整图片，不要求额外拆出背景、人物、细节节点。
D 根据变量依赖决定这个节点 RECOMPUTE；B 在节点内部执行原图参考编辑及受限合成。
节点缓存命中与图片内部的像素复用是两种不同粒度，不能把像素保护计为额外 CACHE_HIT 节点。

输入必须有明确的原图及编辑边界；缺失边界时返回错误，不自动整图重画。
边界可由用户框选或外部分割工具提供，不能从“换人物”几个字推断准确 Mask。

## D 的调用方式

已有 `execute_node()` 不改变签名。继续使用同一节点 ID 和输出端口：

```python
node["execution"]["params"].update(edit_mode="masked_replace")
node["execution"]["inputs"].update({
    "original_image": {"kind": "artifact", "artifact_id": original_id},
    "edit_mask": {"kind": "artifact", "artifact_id": mask_id},
})
result = await engine.execute_node(node, artifacts, context=context)
```

Mask 必须是与原图尺寸完全一致的 PNG/WebP 等受支持的栅格图片。
灰度 0 保护，255 替换，中间值混合；透明像素也保护，实际权重为亮度乘透明度。
PNG 的每个通道，包括透明区域的 RGBA 值，在权重为 0 时逐字节保持原图值。
原图和 Mask 按真实 SHA256 校验，空 Mask、缺失附件、尺寸不符会在调用模型前失败。

简单框选可以省略 `edit_mask`，改用：

```python
node["execution"]["params"]["edit_region"] = [0.3, 0.15, 0.4, 0.7]
```

这是相对图片自身的 `[x, y, width, height]`，不是浏览器容器坐标。
Mask 和矩形必须二选一。矩形边界本身不代表语义分割，矩形内的背景也允许被修改。

非 Runtime 场景提供便捷入口，节点和调用方字典不会被修改：

```python
result = await engine.execute_image_edit(
    node, artifacts, context=context,
    original_artifact_id=original_id,
    region=[0.3, 0.15, 0.4, 0.7],
    # 或 mask_artifact_id=mask_id，与 region 二选一
)
```

便捷入口返回标准 Artifact 和元数据，不自行修改 Graph 或写回档案。
正式可重放归档应由 D 持久化上述 execution 参数、Artifact 输入绑定和原图/Mask 附件。
旧图是已有历史 Artifact 引用；不要为相同节点添加依赖自己的 node_output 边。

## 两阶段与模型调用

- 局部编辑的 Draft/Final 都保持原图尺寸；切换步数、模型及 `:draft`/`:hq` 缓存。
  不能缩放保护区又宣称保护区的原像素不变。普通全图生成继续使用原有分辨率策略。
- sdcpp 自动向 `/v1/img2img` 传入原图作为唯一参考，再由 B 按 Mask 合成。
  这不是宣称本地模型支持原生 Inpainting，模型可能仍生成全幅候选图。
- OpenAI 兼容编辑模式使用 `/images/edits` multipart，发送原图和反转透明度后的 Mask。
  具体模型仍须支持编辑及对应尺寸；本轮用协议测试校验请求，没有调用付费云服务。
- 不支持参考图片的 Provider 不能冒充局部编辑。按用户配置报错或转入真实标注的 Local Fallback。
- Local Fallback 是程序化预览，不声称有模型的语义编辑质量；同样严格遵守保护区。

`execution.params.edit_reference=false` 显式选择生成全新候选内容，再由 B 仅替换 Mask 区域。
这适用于原图参考过强、需要全新环境等场景，元数据记录 `generation_then_masked_blend`。
默认仍是参考原图编辑；此选项不允许候选图覆盖保护区。

`execution.params.edit_operation="overlay"` 用于纯追加对象：模型只生成白底独立对象，
B 抠出对象后按选区等比放置，使用对象 alpha 与用户 Mask 的交集叠加到旧图。
这个模式自动关闭模型参考图，但仍以原图为最终底图。原人物、旧道具和背景不会整图重绘。
Local Fallback 也支持透明追加层。自动白底抠图的限制与原有图层模式相同；复杂透明材质
应提供透明素材或使用受支持的编辑模型，不承诺任意对象都能完美抠图。
追加的 Prompt 应只描述新增对象，不把旧场景人物再次写进新增对象描述。

缓存身份包含原图、Mask 内容、选区、修改输入、Provider、模型、阶段及适配器版本。
更换任一编辑条件会改变身份，兜底结果仍与真实 API 缓存隔离。
元数据记录 base_sha256、edit_mask_sha256、protected_pixel_count、实际分辨率和生成来源。

## 主体、背景与连续 Append

换主体：Mask 覆盖旧主体和新主体允许出现的区域。
换背景：Mask 只覆盖背景，主体保护区为 0。
追加细节：以刚完成的图片作为新 original_image，使用新的局部 Mask；保护旧人物及旧细节。
因此连续追加不要求重新生成之前的图层，也不要求 Graph 新增图层节点。

换背景时，矩形保护区域会留下矩形内的旧背景，这是正确遵守 Mask 的结果，不能视为无缝分割。
真实场景须提供贴合主体轮廓的 Mask；验收脚本可接收明确的人工作图 `--protected-outline`，
不把人工轮廓标注伪装成自动语义识别。

边界以内的语义质量仍依赖编辑模型与选区。替换人物遮挡区域时，旧人物背后的背景
并未包含在原图中；需要模型补全，不能承诺恢复不可见的真实背景。
自动语义分割、复杂毛发/玻璃抠图、跨阶段无损升分辨率没有在本轮实现。

## C 的装配

由 C 提供框选/Mask 编辑和确认操作，将归一化选区或 Mask Artifact 交给 D。
当前平台页不自动获得画笔或语义分割能力；本轮不修改 C 的页面和 D 的路由。
前端读取扩展元数据时，应区分“保护像素数量”和“Graph 复用节点数量”。

## 本地验收

`tests/test_image_edit.py` 覆盖单节点 Draft/Final、同 Seed、原尺寸、矩形、软 Mask、
透明 Mask、连续编辑、更换背景、缓存复用、错误尺寸、模型协议及真实 D 的单节点
Fork/Plan/Replay。输出继续经过 A 的 Schema 与档案校验。

执行 B 既有 pytest、Ruff 检查；真实模型验收脚本为
`python -m preview.scripts.verify_single_image_edit --output <本地忽略目录>`。
其生成图片与报告不加入 Git，报告不含凭据。

OpenAI 编辑上传及 Mask 格式依据官方文档核对：
https://developers.openai.com/api/docs/guides/image-generation
https://developers.openai.com/api/reference/resources/images/methods/edit

## 本轮本地检查点

- 本轮完整 B 回归 94 项通过；追加 overlay 实现后，受影响的引擎、Provider、单图编辑、
  图层和请求配置专项 74 项通过（两组有重叠，不相加）。Ruff check、format --check、
  git diff --check 通过。未修改前端代码，不重复运行本轮未触及的前端测试。
- 真实 D 的单图片节点档案已通过导入、Fork、Plan、Draft/Final Replay、Artifact 获取
  和缓存计划检查；全过程 Graph 只有一个节点，档案通过 A 校验。
- 本机模型真实生成替换主体、独立追加对象、全新背景候选；Local Fallback 已关闭。
  输出图片与报告在本地忽略目录 `.c-integration/single-image-edit/`。
- 替换主体保护 247,876 像素，追加测试的边界外保护 343,766 像素，换背景保护 26,157
  像素，所有保护区 RGBA 最大误差为 0。保护区断言和肉眼语义检查分开记录。
- 手工检查实际图片：机器人换成狐狸，追加红气球，换成雪景后保留旧狐狸和旧气球。
  背景验收使用人工轮廓标注；粗糙轮廓仍有少量旧背景边缘残留，不承诺无缝抠图。
- 新增能力在 B 中可调用；C 的框选/Mask 编辑页面与 D 的持久化装配仍需按上文接口接入。
  没有将当前网站宣称为已具备自动选区或任意自然语言的精准局部编辑。
- 当前 HEAD 仍是既有本地基线 `300a582`，本轮和上轮工作以未提交差异保留。
  没有 git commit、git push、PR 更新或对 A/C/D 文件的写入。
