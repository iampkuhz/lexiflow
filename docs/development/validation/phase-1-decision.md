# 1. 校验：第一阶段决定

第一阶段出口只由当前依赖闭包、有效收据和明确用户决定共同证明；没有“构建成功即可进入下一阶段”的捷径。本页是出口核对流程，不包含产品实现细节。

## 1.1. 核对评审对象

阅读[产品简介](../../product/product-brief.md)、[架构决策](../../architecture/decisions.md)、[长期计划](../../roadmap/master-plan.md)和[第一阶段状态页](../../roadmap/phase-1-status.md)。确认目标、模块边界、失败与一致性合同、假设和复审条件能够追溯到明确责任人。

## 1.2. 核对当前前置链

以[工作流目录](../../../planning/workstreams.yaml)中 `LF-TSK-ARCH-0008` 的依赖闭包为准，而不是统计历史 `PASS` 文本。任务、变更、注册表、来源、策略和依赖收据必须匹配当前输入。正式证据的读取与哈希复核见[收据与验收](receipts-and-acceptance.md)。

## 1.3. 请求用户决定

已有明确批准且批准对象未改变时，复用该批准，不因收据过期反复要求批准。没有批准时，前置齐备后，提交已经确定的架构结论、取舍理由、待验证假设、当前收据索引和公开 Gate 的实际结果，请用户明确接受、修改或拒绝。不得自行写入 `APPROVED`，也不得将文档提交、工程构建成功或用户要求继续优化解释为批准。

## 1.4. 检查出口合同

出口文档合同的只读诊断命令为：

```bash
python3 -m scripts.repository.planning_check --root .
python3 -m scripts.acceptance status --submission-id <submission-id>
```

它不签发收据。缺少精确批准标记时应记为 `BLOCKED`；实际检查失败保留 `FAIL`。只有用户决定与当前出口收据均有效，才具备后续准入。

## 1.5. 复现边界

工具链的干净环境复现由后续明确任务执行；已有 Java 底座不证明双环境复现或产品业务完成。流程见[工具链复现](../toolchain-reproduction.md)。

## 1.6. 执行入口

按 [Harness 验收流程](../../../harness/README.md) 先持久化 PASS 日常报告并运行 `submit`，再由不同真实任务执行 `validate`、`review` 和 `check`。输入或来源漂移后要针对当前闭包重新送验；runtime authority 由本机 adapter 在签发时验证，无需用户编造身份文件。
