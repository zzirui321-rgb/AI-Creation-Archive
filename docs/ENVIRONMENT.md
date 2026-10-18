# 沿用既定开发环境

沿用现有 Conda aica 环境。版本、清华源和安装步骤见清单 setup-guide；environment 引用该资源，并保护 conda-environment、python-requirements、python-tooling。本次不改变这些文件，不安装或升级依赖。

reference 指向已有环境规范的资源 ID；commands 指向已有命令定义；protected_resource_ids 标记不可改动的环境资源。既有密钥注入、网络权限、费用限制和容器启动方式全部沿用。

Agent 不得自行安装/升级运行时，切换包管理器，重建锁文件，改基础镜像，修改现有 CI 环境或替换模型供应商。环境问题保存原命令、错误、差异和恢复条件，按原维护流程处理。只继续不依赖缺失信息的工作。

清单留空不表示环境可自由选择。将来用户明确授权环境变更时另立任务；不能把一次业务任务解释成改环境授权。commands 已接入现有 A 测试、Ruff、依赖及档案检查，并增加使用现有 jsonschema 的本地协作结构检查。先激活既有 Conda 环境再执行。平台 dev/build 尚未实现，保持 null；本地检查不会开启远端工作流。
