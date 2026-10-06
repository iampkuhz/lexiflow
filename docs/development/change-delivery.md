# 1. 开发交付：从实现到可信验收

> 位置：[文档首页](../README.md) → [工程地图](overview.md) → 开发交付。先读本页建立阶段关系，再进入 Verify 或 Delivery Gate；精确参数到 Reference 查阅。

日常开发自检按真实改动风险选择检查；完整 Change/Repository Verify 是显式完整基线原语，正式 Delivery Gate 回答“同一冻结输入是否经过独立验证、必要审查及依赖/批准核对”。它们复用检查能力，不共享身份结论。一次 Agent 执行完成不等于验收通过。

## 1.1. 能力边界与入口

本页只说明证明层次，不重复执行活动流程。直接自检是实现者针对局部改动的静态、编译或定点回归；开发 Stop 按真实 diff 风险生成开发报告；完整 Change/Repository Verify 是显式完整基线；Formal Gate 冻结 Task 与输入，由独立身份验证，并按计划审查、核对证据。普通 CI quick 与正式发行 CI 的边界见[CI 说明](operations/github-actions.md)。这些证明不可互相替代。

| 能力 | 核心作用 | 入口链接 |
| --- | --- | --- |
| 直接自检 | 实现者先发现并修复局部问题 | [自检选择](change-delivery/verification.md) |
| 开发 Stop | 以真实风险闭包产出开发报告；不签 Formal receipt | [Hook 机制](change-delivery/hooks.md) |
| 完整 Verify | 显式运行完整变化或仓库基线 | [Verify 入口](change-delivery/verification.md) |
| Formal Gate | 冻结送验输入、独立执行、按需审查并核对 receipt/DAG | [Delivery Gate](change-delivery/delivery-gate.md) |
| CI | 普通分支/PR 使用 quick；发行标签执行完整基线 | [GitHub Actions](operations/github-actions.md) |

各层详细执行活动统一见[编码后执行仓库门禁场景](scenarios/run-repository-gates.md)。本页只提供导航，不把人工交接画成自动串联。

## 1.2. 哪些不是主干阶段

文档链接、格式和 policy projection 是模块检查里的 standalone checker，无需把每项都画成新的阶段。缺环境、身份冲突、输入漂移和缺批准会阻止交接，因此必须从主干进入[排障页](troubleshooting.md)。

`status` 是已有 submission 的观察入口，不是 S7。Hook 安装和 skill 链接是有副作用的本机维护，不是编辑或提交的许可步骤。日常 Verify 不读取 Task identity，不签发正式 receipt，也不阻断 commit。

## 1.3. 从本次工作开始

先确认最终 diff、他人修改与本次负责范围，不在编辑前预锁文件。日常完成时运行风险驱动开发入口并阅读风险/范围结论；只有需要完整 Change/Repository 基线或正式发行时才运行对应完整入口。自检不能代替正式独立验证。

完整原生检查被聚合入口包含时，不再机械补跑每个子任务。正式 validate 由独立身份执行；机械/局部风险无需 review，高风险工程与正式发行要求独立 review。review/check 不运行交付命令。

下一步：进入 [Verify](change-delivery/verification.md)。需要准备环境时进入[隔离验证环境](operations/verification-environment.md)；只查某个工具时进入 [Reference](reference.md)。
