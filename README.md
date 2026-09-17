# LexiFlow

LexiFlow 在 YouTube 英文字幕上提供少量、结合语境且因人而异的中文词语提示。英文先显示；规则决定是否提示；模型提供语境含义；服务端维护跨设备 Vocabulary Profile。

当前处于 **Phase 1 — Architecture / Java Foundation**。Java 25 + Spring Boot 构建与质量工具已有工程验证，业务领域、Extension 和真实用户旅程仍是待实现设计；第二阶段未激活。

## 从这里理解产品和架构

先读 [产品目标](docs/product/product-brief.md)，再进入 [架构阅读入口](docs/architecture/README.md)。架构按“总览 → 专题解释 → 详细合同”展开，穿插上下文、组件、部署、活动、时序、状态和概念关系等 PlantUML 图。

[文档目录](docs/README.md) 区分架构、当前工程证据、阶段决定和历史记录。[长期计划](docs/roadmap/master-plan.md) 描述阶段路线，[G1 决策包](docs/reviews/g1-decision-package.md) 记录第一阶段出口条件。

## 开发与验证

后端固定 Java 25；Python 只承载 Harness、Gate 和工程脚本。唯一工程与调度规则在 [AGENTS.md](AGENTS.md) 与 [harness](harness/README.md)。

```bash
python3 -m scripts.gates.planning --root .
python3 -m unittest discover -s tests/harness -p 'test_*.py'
python3 scripts/toolchain/java_gradle.py --no-daemon clean deliveryFull
```

[确定性工具审查](docs/reviews/phase-1-deterministic-tools-audit.md) 保存最近一次 Java 工程验证及其边界。公开 Gate 的三层验收消费冻结合同和收据，目前没有 Gradle adapter；Java 直接工程验证与正式 Task receipt 分别记录。

```bash
python3 scripts/gates/cli.py run --mode incremental
```

这个入口要求可信 evidence/issuer context，缺失如实 FAIL。本次文档结构与历史收据的影响见 [重构审查](docs/reviews/architecture-documentation-restructure.md)。
