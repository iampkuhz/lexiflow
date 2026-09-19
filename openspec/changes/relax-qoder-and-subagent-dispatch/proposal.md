# Proposal: relax-qoder-and-subagent-dispatch

## Why

现有 Qoder 派发把工作包绑定到至少两个 Catalog Task、180 分钟估时和完整
Catalog 上下文哈希。小于该阈值但超过主线程十分钟的实现无法派发；无关的
Catalog 或文件范围变更也会阻断已具备核心目标、范围、验收和验证链路的任务。

## Scope

- Qoder 和 Codex 子任务均以超过十分钟的独立工作为主动派发评估阈值。
- Qoder 允许一个自包含任务；Catalog 在存在且一致时提供额外核对，但不再是启动前提。
- 保留身份、受限写入范围、验收、验证命令、agent profile、核心规则上下文、单活跃运行和回调确认。
- 让 schema、runner、测试、运行时投影和文档使用同一阈值与可选 Catalog 语义。

## Non-goals

- 不降低密钥、身份绑定、路径安全、并发锁、结果结构或独立验收要求。
- 不让 Qoder 自行递归派发或自动提交 Git。
