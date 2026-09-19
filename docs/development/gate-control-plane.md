# 1. Gate 控制面

Gate 以唯一 CLI、纯计划编译、显式注册表和不可变收据链固定“检查内容、输入、身份和结论”。
`plan` 与 `run` 对相同模式、任务、目录、策略和证据包生成相同规范计划及 `content_fingerprint`；
`run` 在计划冻结后执行并写入新的 `run_id`，`status` 只读取该运行的固定产物。

## 1.1. 唯一入口与计划

```text
python3 scripts/gates/cli.py plan --mode incremental|full --evidence-packet <repo-relative-path> --issuer-packet <repo-relative-path>
python3 scripts/gates/cli.py run  --mode incremental|full --evidence-packet <repo-relative-path> [--issuer-packet <repo-relative-path>]
python3 scripts/gates/cli.py doctor
python3 scripts/gates/cli.py prepare-issuer --evidence-packet <repo-relative-path> --receipt-kind TASK_VALIDATION
python3 scripts/gates/cli.py status --run-id <uuid>
```

必需路径缺失、冲突或不是单一仓库相对路径为 `FAIL`。只有注册表中的检查可执行；冻结输入漂移为
`FAIL/input-drift`。`plan` 零写入，`doctor` 只诊断 runtime readiness。

## 1.2. 分层、收据与边界

| 层 | 做什么 | 不做什么 |
|---|---|---|
| `TASK_VALIDATION` | 执行冻结交付检查并签发 validation receipt | 不独立审查或作目录决定。 |
| `INDEPENDENT_REVIEW` | 复核冻结 diff 与 validation evidence | 不重跑交付命令。 |
| `CATALOG_DECISION` | 核验 validation/review/dependency receipt 的哈希 DAG | 不执行交付或审查命令。 |

三层严格串行，结果只有 `PASS`、`BLOCKED`、`FAIL`。收据绑定运行、任务、变更、冻结计划、输入/证据哈希、
实际检查、签发者和时间；退出码、回调或旧收据不代表 `PASS`。任一定位、身份、哈希或依赖不一致为 `FAIL`，
必需证据缺失为 `BLOCKED`。

Gate 不负责任务目录、Qoder 生命周期或派发并发；这些归 planning、runner 与[派发预检](dispatch-preflight.md)。
