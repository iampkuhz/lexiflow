# LexiFlow Qoder 入口

先读仓库根 AGENTS.md、.qoder/AGENTS.md、任务绑定的 harness_manifest 及其完整 required_context、.qoder/agents/<agent_profile>.md，以及 harness/agent-policy.manifest.yaml 的 subagent_protocol 与 qoder_delegation；工作包、身份、结果和回调均从这些共享合同读取。

文档与本机 skill 接入读取 harness/documentation-policy.yaml；按其声明显式读取本机链接的 SKILL.md，不假定 Qoder 自动发现 Codex skill。
