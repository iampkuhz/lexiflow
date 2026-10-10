# 1. Delivery Gate 命令与记录核对

> 上级：[交付验收体系](../system/delivery-acceptance.md)；整体关系见[工程治理三体系](../system/engineering.md)。本页只供核对公开命令与阶段特有参数；机器合同以 [Harness](../../harness/README.md) 为准。

## 1.1. 唯一命令入口

所有子命令都经 `python3 -m scripts.acceptance` 分派。`submit.py`、`validate.py`、`review.py`、`check.py` 是内部实现文件，不是独立 CLI。

| 操作 | 命令 | 主要输入 |
| --- | --- | --- |
| 送验并冻结范围 | `python3 -m scripts.acceptance submit --task-id <id> --change-report-id <uuid> [--producer-run-id <id>]` | 当前 Task、PASS 开发报告；producer run 仅用于核对真实来源 |
| 独立验证 | `python3 -m scripts.acceptance validate --submission-id <uuid>` | 冻结 submission；需验证的 actor 与 producer/submitter 不同 |
| 有界批量验证 | 重复 `--submission-id <uuid>` 参数，最多 16 个唯一 submission | 各 Task 分别保留计划、报告和 validation record |
| 独立审查 | `python3 -m scripts.acceptance review --validation-id <uuid> [--findings-json <path>]` | 仅验收计划要求时使用；由 findings 派生状态，不接受自报 decision；审查者与实现者及 validator 不同 |
| 核对验收条件 | `python3 -m scripts.acceptance check --submission-id <uuid>` | 已发布证据、当前依赖与精确绑定的批准（若要求） |
| 只读观察 | `python3 -m scripts.acceptance status --submission-id <uuid>` | 指定 submission；不推断 latest，也不推进链路 |
| 消费既有 PASS | `python3 -m scripts.acceptance consume-existing --submission-id <uuid>` | 只读重核唯一、完整且当前的 Gate 链 |
| 消费候选产物 | `python3 -m scripts.acceptance consume-candidate --submission-id <uuid> --candidate-directory <absolute-path>` | 既有 PASS 链、完整发行基线证据及与报告绑定的候选字节 |

尖括号项是调用者替换的值。命令返回 `PASS`、`BLOCKED` 或 `FAIL`；退出码或 stdout 单独不能证明验收通过。`status` 仅观察指定记录，`consume-*` 只读消费既有证据，不补签 receipt、不重跑测试。

## 1.2. 阶段核对要点

| 阶段 | 核对要点 |
| --- | --- |
| `submit` | Task 主体由完整真实 diff 与 Catalog scope 派生，绑定风险计划、检查要求、冻结输入、文件快照和 producer；不得由调用者自选风险或过滤路径。 |
| `validate` | 只由独立 actor 对冻结计划调用 Verification；正式验收复用[质量检查能力](../system/quality-checks.md)，不是第二套检查实现。输入漂移、缺项或未执行不能 PASS。 |
| `review` | 仅高风险工程与正式发行计划要求独立 review；只读 frozen diff/evidence 并形成真实意见，不运行检查。机械或局部风险不造伪 review。 |
| `check` | 只核对计划要求的 validation/review、receipt/hash DAG、当前依赖和精确批准；不重跑命令。缺少前置条件返回 `BLOCKED`。 |

风险计划由当前 policy 和 Task 必需检查派生：机械／局部功能使用 `development-change`，高风险工程使用 `development-baseline` 并要求 review；正式发行使用 `repository-baseline` 并要求 review。日常开发报告不等于 Formal validation，局部验收链也不代表完整工作区验收。

唯一活动 schema 为 submission.v6、validation/review/check.v5；不接受 legacy flags、scope confirmation 或自报决策。原 ignored 旧记录保留原始字节，不自动消费。

每层记录位于 ignored `tmp/quality/delivery-gate/current/`，并绑定 Task/version、输入、身份与内容哈希。只删除同一冻结父中可完整派生的镜像/子 hash；外部 runtime metadata 与附件 raw hashes 仍独立核对。后续读取会重新核对来源和当前输入；历史 PASS 不覆盖漂移、损坏、歧义依赖或缺失批准。实现细节见 [submit](../../scripts/acceptance/task_operations/submit_task_for_acceptance.py)、[validate](../../scripts/acceptance/task_operations/run_independent_task_validation.py)、[review](../../scripts/acceptance/task_operations/record_independent_task_review.py)、[check](../../scripts/acceptance/task_operations/evaluate_task_acceptance.py)；执行步骤见[编码后执行门禁](../scenarios/run-repository-gates.md)。

候选消费只执行一次固定 Node candidate integrity proof，最后仍重读 Gate 与附件；每次实际 publish 前再次消费并认证新鲜证据。共享的纯 candidate runtime 环境合同不代替消费者两侧真实来源认证。
