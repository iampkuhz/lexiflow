# LexiFlow

LexiFlow 在英文内容上提供低打扰、上下文感知、因人而异的中文词语提示。首个入口是 YouTube Chrome Extension；长期核心资产是跨设备共享的 Vocabulary Profile。

当前处于 **Phase 1 — Architecture / Java Foundation**。产品后端固定为 Java 25 + Spring Boot，Python 只承载 Harness、Gate 和工程脚本。当前开始落地可运行的 Java 构建骨架，但不进入 SQL、具体业务 API、Prompt 或业务 Domain 实现。

- [第一阶段工具审查与当前证据](docs/reviews/phase-1-deterministic-tools-audit.md)
- [Phase 1 架构](docs/architecture/phase-1.md)
- [Phase 1 横切合同](docs/architecture/phase-1-cross-cutting-contracts.md)
- [关键决策与替代方案](docs/architecture/decisions.md)
- [G1 决策包](docs/reviews/g1-decision-package.md)
- [长期交付路线](docs/roadmap/master-plan.md)
- [Phase 1 历史状态记录](docs/roadmap/phase-1-status.md)
- [当前 OpenSpec change](openspec/changes/establish-lexiflow-foundation/proposal.md)
- [Java 产品基础 change](openspec/changes/bootstrap-java-product-foundation/proposal.md)
- [Agent 子任务协议](docs/development/agent-subtasks.md)

当前执行、验收边界和活动调度以工具审查页为准。旧状态页和历史工作包的“current/活动”描述只适用于原记录时点。

规划与 runner 的定向验证入口：

```bash
python3 -m scripts.gates.planning --root .
python3 -m unittest discover -s tests/harness -p 'test_*.py'
python3 scripts/toolchain/java_gradle.py --no-daemon clean deliveryFull
```

统一 Gate 已实现三层验收：Task validation 执行冻结的工程合同，independent review 和 catalog decision 消费收据。它当前没有 Gradle adapter，Java 交付使用上面的确定性 launcher；两种结果不能互相替代。下列入口要求可信的 evidence/issuer context，缺失时如实失败：

```bash
python3 scripts/gates/cli.py run --mode incremental
```
