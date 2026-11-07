# 1. 共用 Check 责任索引

> 位置：[Verify](../verification.md) → 共用 Check。实际命令、场景资格、触发、输入与超时均以 [module-checks.yaml](../../../config/engineering/module-checks.yaml) 为准；本页不维护第二套命令清单或固定执行顺序。

## 1.1. 哪些行为由谁证明

| 证明对象 | 保留的实际执行者 | 不能替代的保障 |
| --- | --- | --- |
| Java 业务、架构、静态质量、SQLite 集成和 runtime smoke | `eng.backend.delivery` 的 Gradle `deliveryFull` | 不证明浏览器和正式发行候选 |
| Gradle 任务、生成输入、配置缓存与前置故障 | `eng.backend.build-contract`，入口 [gradle_build_contract.py](../../../tests/verification/gradle_build_contract.py) | 独立入口，不在 `test_*.py` discovery 内，也不重复 Java 业务测试 |
| 扩展构建、单元测试与真实 API／浏览器集成 | `eng.extension.quality`，入口 [run-extension-checks.mjs](../../../extension/scripts/checks/run-extension-checks.mjs) | E2E 的 `:api:bootRun` 自行编译与启动 API；后端完整质量测试不是扩展运行前置 |
| 选择、冻结、执行、报告、Hook 与 CI | `eng.verification.module-tests` 的 `verification` suite | 不再混入发行晋升、进程监督和源码归档的行为回归 |
| 原生身份、工作包与交接事实 | `eng.agents.module-tests` 的 `agents` suite | 不是被派发 Task 的验收 |
| 正式验收工具的证据、独立性与消费合同 | `eng.delivery-gate.module-tests` 的 `acceptance` suite | 测试工具自身，不签发 Task 正式验收；库源码变化仍分别触发库和消费者测试 |
| 文档、Catalog、源码归属和 Python 静态检查工具 | `eng.repository.module-tests` 的 `repository` suite；实际工具由对应 docs／planning／python-quality Check 执行 | 工具回归与对当前仓库的实际静态检查是不同证明，不能互删 |
| 发行晋升、daemon 预检、进程监督、源码归档和运行适配器 | `eng.release.python-tests` 的 `release` suite，源码 [tests/release](../../../tests/release) | 保留完整行为断言；不代表真实发行候选已验收 |
| 发行身份、清单、构建与安装 fixture | 对应 `eng.release.*` Node Check，入口 [run-release-tool-tests.mjs](../../../ops/release/test_tools/run-release-tool-tests.mjs) | fixture 不代替真实候选运行；不同对象的 smoke 不是重复测试 |
| Docker／Podman／数据工具合同 | 完整基线的 `eng.release.docker-contract`；开发场景的四个不重叠分组 | 分组前审计完整测试库存与实际源码消费；新增、删除或消费缺口不得静默遗漏 |
| 真实源码发行和精确候选安装、升级、恢复 | `eng.release.lifecycle-runtime`、`eng.release.candidate-runtime` | 正式专属，不能以工具单元测试、开发报告或历史 PASS 代替 |

五个 Python suite 共用 [run_python_tool_tests.py](../../../scripts/checks/tool_tests/run_python_tool_tests.py) 和标准 `unittest` discovery。零测试、提前停止、失败、异常或 skip 均不能 PASS；原始断言、异常与跳过原因写到本机 stderr 日志，JSON 只保留结构化摘要和诊断摘要，不复制 traceback。

## 1.2. 从直接反馈到正式验收

| 使用层 | 证明与执行责任 | 不重复做什么 |
| --- | --- | --- |
| 实现者直接自检 | 必要静态、编译与精准回归，先定位实际失败层 | 不惯例性先跑全仓或签发独立验收 |
| 独立开发检查 | 不同真实 actor 在最终输入上按完整 diff、显式触发及真实依赖闭包执行 CLI 或 Stop | 两入口择一；不惯例叠跑同一检查 |
| 正式 `TASK_VALIDATION` | 独立 validator 执行风险所需冻结范围，并绑定真实证据 | 开发报告不是 Formal PASS，不能跨交付复用 |
| `INDEPENDENT_REVIEW` 与 `CATALOG_DECISION` | 审查者只读冻结差异和证据；裁决者核 receipt/hash DAG | 不重新执行测试，不代替真实 actor 或人类授权 |

## 1.3. 成本与诊断

计算等待时间应以当次实际物理执行和窗口为准：父入口与子任务不重复相加，同事务合法复用不再次累加。单次旧样本不是最新执行时间，也不是所有 diff 的预算；没有对本次输入执行就不能宣称通过或加速。

静态选择可解释将执行哪些检查，但不等于这些检查已运行。耗时比较须统一解释器、输入范围、缓存与服务条件，并标明是否包含构建、下载和环境准备。失败先读当次 Check 的 stdout／stderr locator；不以摘要里的名称代替断言原因，也不为了取 traceback 再跑完整套件。
