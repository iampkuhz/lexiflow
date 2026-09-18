# 1. Gate 控制面：交付边界

## 1.1. 三层职责

| 层 | 做什么 | 不做什么 |
| --- | --- | --- |
| `TASK_VALIDATION` | 执行冻结的交付检查并签发 validation receipt | 不独立审查或作目录决定 |
| `INDEPENDENT_REVIEW` | 复核冻结 diff 与 validation evidence | 不重跑交付命令 |
| `CATALOG_DECISION` | 核验 validation/review/dependency receipt 的哈希 DAG | 不执行交付或审查命令 |

层间必须串行且每层使用当前授权上下文。任务完成、runner 回调或进程退出只表示一个事件，不表示任一层验收通过。

## 1.2. 实现边界

Gate 负责计划、固定检查执行、收据与证据消费；规划目录负责任务 DAG 与 owner，派发预检负责并发写入声明，runner 负责运行身份和完成记录。实现新能力必须明确归属、依赖和 OpenSpec 变更，不能因为脚本同目录形成隐式调用。

## 1.3. 交付核对

交付前先核对冻结差异、任务范围、注册表输入和证据包；执行后读取实际 receipt，而非只看命令输出。缺上下文或必需证据为 `BLOCKED`；输入、身份、范围或哈希不一致为 `FAIL`。所有需要的收据均为 `PASS` 才可交给下一层。
