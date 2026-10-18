# 契约先于并行实现

archive-contract 是 .aica 唯一协议来源，通过 bindings 找到真实实现，不指定目录。原计划的示例 JSON 不视为完整 Schema。

A 冻结版本、ID、节点/依赖/产物/分支/变量、provenance 与 evidence、consent、Keep/Override/Append 的语义和兼容策略，交付有效/无效 fixture。公共类型由生产者和消费者共同引用。

D 定义 Cache Key、Dirty 和 Fork 的执行语义：工具/模型版本、参数、prompt、输入内容 hash、seed 与 Draft/Final 配置中实际影响输出的部分进入缓存判断；随机或不可缓存工具明确处理。Golden Path 是从已有成功历史总结的推荐路径，不承诺全局最优。

平台 API 与预览服务约定输入输出、错误、取消、超时、重试与幂等；D 装配外层，B 实现预览。B 对展示组件声明输入、事件、loading/error/cache 来源状态，C 按同一契约组装。

E 的标准 Demo 数据由 A 验证，素材保留来源许可和 hash。H4 前提供共用业务 fixture，降低开发者各自找图和拼 JSON 的成本。

跨模块变更先提出需求 → 接口 Owner 接受契约/版本/fixture → 合入 → 各方实现与验证。破坏兼容的变更记录影响、迁移顺序和回退方式；H4 后避免增加 Demo 无关字段。所有执行沿用既定环境。
