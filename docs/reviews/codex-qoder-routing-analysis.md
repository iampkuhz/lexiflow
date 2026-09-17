# Codex Sub-Agent 与 Qoder 路由分析

## 统计口径

本审计只读取当前 LexiFlow 主线程 `01a0a5da-3abb-7733-bc2b-c8e379349c04`
对应的 Codex session metadata：

- `/Users/zhehan/.codex/thread_history_1.sqlite`
- `/Users/zhehan/.codex/sessions/2026/09/16/`

共 50 个唯一 Codex child thread，49 个有明确启动事件，另有 1 个嵌套 child；
49 个 child 写出了 provider token usage。Qoder run 不计入本表。

## 模型与 token

50 个 child 的记录模型全部为 `gpt-5.6-sol`。49 个有 usage 的 child 合计：

- `total_tokens`: 257,485,620
- `input_tokens`: 255,673,902
- `cached_input_tokens`: 245,569,536（为 input 的子集）
- `output_tokens`: 1,811,718
- `reasoning_output_tokens`: 756,685

该历史结果不代表新调度的模型策略；新 Codex dispatch 由本变更改为显式
`gpt-5.6-terra` 默认。

## 适合 Qoder 的工作形态

| 历史形态 | turn | 活跃分钟 | total tokens | command executions | 结论 |
|---|---:|---:|---:|---:|---|
| `qlt_0014_review` | 6 | 23.98 | 8,765,438 | 102 | **适合**：只读证据审查，使用 `quality-verifier`，回传 compact signal |
| `qlt_0007_review` | 6 | 40.25 | 15,974,972 | 120 | **适合但需拆边界**：审查上下文密集，禁止承担最终 Gate 决策 |
| `qlt_0008_review` | 3 | 11.01 | 4,530,036 | 84 | **适合**：固定命令、scope 和 receipt 审查 |
| `g1_validation_migration_audit` | 1 | 19.65 | 10,056,255 | 62 | **适合**：确定性验证和迁移审计，结果天然可压缩 |
| `qlt_0014_issuer_impl` | 5 | 43.24 | 18,969,213 | 119 | **条件适合**：实现与直接测试，必须绑定单一写入 owner |
| `qlt_0007_takeover` | 2 | 81.10 | 40,575,324 | 190 | **条件适合**：大实现包，但不能包含跨域集成或最终验收 |
| `qlt_0007_takeover/final_code_review` | 5 | 119.25 | 57,671,191 | 313 | **不建议直接转 Qoder**：嵌套、高风险、最终审查性质，保留 Main/Codex |
| `qlt_15_16_migration_design` | 2 | 22.93 | 7,424,859 | 91 | **不建议直接转 Qoder**：核心迁移设计决策应由 Main Agent 保留 |

### 推荐的 Qoder package

未来可以把以下类型组成 180–360 分钟、至少两个连续 Task 的 package：

1. 多个同 owner 的只读 Gate/receipt/runner verifier Task；
2. 一个实现 Task 加其直接测试和确定性回归 Task；
3. 多个同写入边界的 Harness/脚本质量检查 Task。

每个 package 必须继续满足精确 `work_package_id`、有序 `task_ids[]`、同 owner、
同 contract、完全兼容的 allowed/forbidden scope、逐 Task outcome 和
`result.json`。本分析不自动派发 Qoder。

## Compact signal 规则

适合 Qoder 的共同特征是：

- 内部可读取大量 bounded context、源码、测试和命令输出；
- 结果只需 `PASS|BLOCKED|FAIL`、changed-file locator、validation command 和最多
  3 条 blocking findings；
- 中间脚本、日志、读取过的文件和模型 payload 留在 run/session 目录；
- Main Agent 独立执行声明命令并审阅必要 diff，不把 Qoder signal 当作验收证据。

## 保留给 Main Agent/Codex 的工作

- 跨域架构与依赖方向决定；
- 安全、迁移、恢复和数据完整性高风险决策；
- 最终 Gate/catalog 决策；
- 需要扩大写入范围或重写多个 owner contract 的工作；
- Qoder 自己无法通过验证后的第三方独立复核。
