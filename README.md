# LexiFlow

LexiFlow 在 YouTube 英文字幕上提供少量、结合语境且因人而异的中文词语提示。英文先显示；规则决定是否提示；模型提供语境含义；服务端维护跨设备个人词汇档案。

阶段进度与验收边界统一查看[第一阶段状态](docs/roadmap/phase-1-status.md)，不由首页维护第二份状态。

## 从这里理解产品和架构

先读 [产品目标](docs/product/product-brief.md)，再进入 [架构阅读入口](docs/architecture/README.md)。架构按“总览 → 专题解释 → 详细合同”展开，穿插上下文、组件、部署、活动、时序、状态和概念关系等 PlantUML 图。

[文档目录](docs/README.md) 按产品、架构、开发、验收、路线、参考和评审导航，只维护最新版。[长期计划](docs/roadmap/master-plan.md) 描述阶段路线，[G1 决策包](docs/reviews/g1-decision-package.md) 提交第一阶段架构决定。

## 开发与验证

[校验指导手册](docs/development/validation/README.md) 按设计、Java、Harness、正式收据和 G1 决定分层说明：每一步运行什么命令、看哪些文件、如何判断结果。按改动选择路线，不需要每次执行下面所有入口。

后端固定 Java 25；Python 只承载 Harness、Gate 和工程脚本。唯一工程与调度规则在 [AGENTS.md](AGENTS.md) 与 [harness](harness/README.md)。

```bash
python3 -m scripts.gates.planning --root .
python3 -m unittest discover -s tests/harness -p 'test_*.py'
python3 scripts/toolchain/java_gradle.py --no-daemon clean deliveryFull
```

公开 Gate 的三层验收消费冻结合同和收据；Java 直接工程验证与正式任务收据分别记录，操作见[校验手册](docs/development/validation/README.md)。

```bash
python3 scripts/gates/cli.py run --mode incremental
```

这个入口要求可信证据包与签发者上下文，缺失如实 FAIL；旧收据不能证明修改后的输入。

文档维护和本机 skill 接入命令见 [Harness 使用说明](harness/README.md)。
