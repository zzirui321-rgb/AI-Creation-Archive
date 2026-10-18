# 漫画 Panel 增量复用与一致性

本轮在 B 中补齐了 Panel 一致性上下文，适用于“一个 Graph 节点对应漫画一张图”。
B 不替 D 计算 Dirty Plan；D 仍决定 `CACHE_HIT / RECOMPUTE / REBUILD`，B 负责执行
受影响 Panel 和最终 Layout。

每个受影响 Panel 的 resolved inputs 应包含：

```json
{
  "reference_artifact_ids": ["character-v2", "style-v1", "environment-v1"],
  "consistency_context": {
    "identity_hash": "character-v2-style-v1-environment-v1",
    "seed": 4202,
    "camera": "medium-shot",
    "composition": "character-left-environment-right",
    "character_ref": "character-v2",
    "style_ref": "style-v1",
    "background_ref": "environment-v1"
  }
}
```

B 会验证引用存在且为图片，记录角色映射、context hash 和参考 Artifact 内容 Hash。
角色、画风、背景任何一个 Artifact 变化都会改变 Panel 指纹；仅修改 Panel 局部时，
原 Panel 与 Mask 也进入指纹。Seed 在 Draft/Final 之间保持一致；如果上下文 Seed
和 Runtime Seed 冲突，B 会拒绝执行。

## 完整执行闭环

```text
D 修改 character_bible
  -> D 传播到依赖 character_ref 的 Panel
  -> 未受影响 Panel: CACHE_HIT
  -> 受影响 Panel: RECOMPUTE + consistency_context(character-v2)
  -> 下游 layout: REBUILD
  -> B 返回新的 Panel/Composite Artifact 与 Hash
```

局部修改沿用现有 `original_image + edit_mask`；Panel 内 Mask 外像素从原图复制。
换人物、换背景、追加道具分别使用对应 Mask。完整图片节点无需拆成多个 Graph 节点。

## D 调用约定

D 不应只把新 Prompt 传给 B。必须同时解析 Graph 的真实 Artifact 依赖，构建上述上下文，
并在 `PlanStep.context.resolved_inputs` 中传入。`reference_artifact_ids` 不能引用本地路径，
必须是当前项目可读取、具有 SHA256 的 Artifact ID。D 负责决定哪些 Panel 是脏节点；B 不从
自然语言猜测传播范围。

`PreviewEngine.execute_plan()` 已验证以下测试样例：旧 Panel 1 缓存命中，Panel 2 因角色
上下文变化重算，Layout 读取两张 Panel 并重建；返回统计为 1 reused / 1 recomputed / 1 rebuilt。

## 当前边界

同 Seed 不能单独保证人物一致，必须提供角色参考 Artifact 和稳定 identity hash。模型本身仍
可能生成视觉偏差；B 只能保证参考输入、缓存身份和未编辑像素的契约。D/C 需要在用户确认
Draft 后决定是否执行 Final，B 不自动替用户确认。
