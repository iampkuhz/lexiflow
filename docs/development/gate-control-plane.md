# 1. Gate 控制面

Gate 控制面的核心是把“这次检查了什么、由谁检查、基于哪些输入、得到什么结论”固定为可复核的链，而不是把检查命令拼成一次临时脚本。它采用**唯一 CLI、纯计划编译、显式注册表和不可变收据链**；这是已确定的方案。

## 1.1. 核心执行模型

`plan` 与 `run` 使用同一个纯编译器：对相同模式、任务、目录、策略和证据包，得到相同的规范计划及其 `content_fingerprint`。`run` 只在计划冻结后执行，生成新的 `run_id`，保存实际计划、开始事件和最终收据。`status` 只读取该运行的固定产物，不从目录扫描、时间或进程状态猜测结果。

注册表是可执行检查的唯一来源。没有登记的命令不能被 Gate 调用；输入、检查选择、签发者与收据哈希都必须显式记录，因此调用者不能以“latest”、环境推断或 Qoder 输出替代本次证据。

## 1.2. 分层与责任

`TASK_VALIDATION` 执行冻结的交付检查；`INDEPENDENT_REVIEW` 只复核冻结 diff 和 validation evidence；`CATALOG_DECISION` 只验证收据与依赖哈希 DAG。三层严格串行，后两层不得重跑交付命令。三态只有 `PASS`、`BLOCKED`、`FAIL`；零退出码、完成信号或旧收据不能跨层解释为 `PASS`。

Gate 不承担任务目录结构验证、Qoder 生命周期或派发重叠判断；这些分别归 planning、runner 和[派发预检](dispatch-preflight.md)。

## 1.3. 详细合同入口

- [CLI、冻结计划、注册表与命令安全](gate-control-plane/interface-and-plan.md)
- [三态、收据、签发者与当前输入链](gate-control-plane/receipts-and-trust.md)
- [实现归属、验收场景与执行顺序](gate-control-plane/delivery-boundaries.md)

## 1.4. 边界

本页定义控制面的核心语义，不重复 JSON 字段、收据示例和逐任务操作。变更 Gate 前先更新对应 OpenSpec 变更；交付前运行公开 incremental Gate。
