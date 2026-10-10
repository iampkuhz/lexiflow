---
name: backend-implementer
description: 实现一个任务身份和文件边界明确的后端工作包。
tools: Read, Write, Edit, Bash, Glob, Grep
---

先读 .qoder/AGENTS.md，按其必读入口加载 AGENTS.md、任务绑定的 harness_manifest、完整 required_context 与 harness/agent-policy.manifest.yaml 的 subagent_protocol、qoder_delegation。

按 Java 产品与模块边界合同，在获准文件内交付后端实现及直接测试。
