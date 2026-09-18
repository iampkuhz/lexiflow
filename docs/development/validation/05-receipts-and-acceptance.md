# 步骤 5：核对正式收据与验收

正式验收通过同一个 Gate CLI，但三层责任不同：**验证执行检查，审查审阅冻结证据，目录验证依赖与收据链**。先核实上下文；没有真实证据包和受信身份时，后面的命令只能作为条件模板。

[三层验收的 PlantUML 图源](../quality-gate-layering.md#diagram-gate-layers) 在架构正文中直接定义，本手册引用同一图源。

图展示合法成功路径；具体拒绝条件看 [质量分层](../quality-gate-layering.md) 与 [Gate 设计](../gate-control-plane-design.md)。不能通过手改收据或伪造身份补齐前提。

<a id="1-先取得真实-context"></a>

## 1. 先取得真实上下文

由受信运行器 / 物化器提供 **当前任务的证据包、签发者证据包，以及它们真实的运行时与授权依据**。核对任务/变更版本、来源快照/差异、范围、生产者/签发者独立性、目录/注册表/策略当前哈希。

证据包定位必须是仓库相对路径。CLI 参数与 `LEXIFLOW_GATE_EVIDENCE_PACKET` / `LEXIFLOW_GATE_ISSUER_PACKET` 同时存在且不相同时会拒绝；在执行前确认实际来源，不输出其中敏感载荷。

本仓库没有供人工从零随意签签发者的公共 CLI；物化器是受信运行时调用的代码接口。没有上下文就记录正式验收 `BLOCKED`。如果实际运行 CLI，保留它返回的 `FAIL/missing-evidence-context`，不要改写成 PASS，也不要随机生成执行者/会话。

<a id="2-先编译计划再执行一次-validation"></a>

## 2. 先编译计划，再执行一次验证

以下变量都要替换为运行器给出的真实相对定位；这是**条件命令**。

```bash
EVIDENCE_PACKET='<actual-evidence-packet-relative-locator>'
ISSUER_PACKET='<actual-issuer-packet-relative-locator>'
python3 scripts/gates/cli.py plan --mode incremental \
  --receipt-kind TASK_VALIDATION \
  --evidence-packet "$EVIDENCE_PACKET" --issuer-packet "$ISSUER_PACKET"
```

看计划的 `task`、`receipt_kind`、`scope`、`consumed_inputs`、`checks`、`execution`、`content_fingerprint`。应选当前任务的冻结注册表检查；不得包含自由 argv、重复选择或其他任务的输入。`plan` 不执行检查、不生成 PASS 收据，退出 0 只说明计划编译成功。

计划正确且输入未变时执行：

```bash
python3 scripts/gates/cli.py run --mode incremental \
  --receipt-kind TASK_VALIDATION \
  --evidence-packet "$EVIDENCE_PACKET" --issuer-packet "$ISSUER_PACKET"
```

只有正式选定完整时，两处 `--mode` 都换成 `full`；不能增量成功后再以补证明为由重复完整。`run` 会重新编译并验证当前绑定，不复用人工编辑的计划。stderr 的 START 提供新的 **Gate 运行 id**，它与生产者的工作包运行 id 不同。

## 3. 查看本次 Gate 产物

使用 START 里给出的真实 Gate 运行 id 查询一次：

```bash
GATE_RUN_ID='<actual-gate-run-id>'
python3 scripts/gates/cli.py status --run-id "$GATE_RUN_ID"
```

`RUNNING` 没有最终结论；`FINALIZED` 要继续看 `result` 与返回收据描述符。状态校验固定运行的 start / 计划 / 清单绑定，**不等于完整当前输入哈希 DAG / 签发者 / 目录验收**，退出 0 也不代替结果。

| 文件 | 在哪里 | 应查看什么 |
|---|---|---|
| START | `tmp/quality/runs/<gate-run-id>/start.json` | Gate 运行、进程身份、计划 / 签发者绑定 |
| 冻结计划 | 同目录 `plan.json` | 任务版本、消费输入、检查选择、层级、指纹 |
| 产物索引 | 同目录 `artifact-manifest.json` | 实际证据定位与 SHA-256；按引用查文件，不猜文件名 |
| 最终收据 | 同目录 `receipt.json` | 种类、任务、签发者、当前输入、完整性、结果、原因，以及该种类的证据字段 |

验证收据还看 `validation.checks`：必需、固定参数序列、类型化结果、实际进程与证据；`validation.execution_aggregation` 应完整覆盖选择。缺必需检查、跳过、结果空缺、只给零退出码都不通过。编译上下文阶段拒绝时可能没有 Gate 运行或上述产物，不能伪造它们。

<a id="4-独立-review-与-catalog-使用各自的新-context"></a>

## 4. 独立审查与目录使用各自的新上下文

审阅者必须与生产者、验证签发者满足共享独立身份规则；提供本次 **审查证据/签发者**，不能沿用生产者或验证的变量。其计划应为 `INDEPENDENT_REVIEW` 且 `checks=[]`，真实审阅者写入集合为零，复核冻结差异、验证证据和当前输入。

同样先 `plan` 再 `run`，将步骤 2 的种类改为 `INDEPENDENT_REVIEW`，并将两个定位换为审阅者的真实证据包。不要再次运行 Java 或交付工具来发布审查。

目录另用其真实 **决定证据/签发者**，种类改为 `CATALOG_DECISION`。其计划也必须 `checks=[]`，校验当前验证、独立审查、必需依赖收据、授权与 DAG。两层零检查是其合同要求，不是未运行验证的理由。

<a id="5-对已有-receipt-做只读-hash-dag-复核"></a>

## 5. 对已有收据做只读哈希 DAG 复核

这是诊断命令，不签收据、不修改旧文件。根定位和 SHA 必须来自**已保存的受信收据引用**；不要先对被改写收据重新算哈希再把它当原引用。

```bash
RECEIPT_LOCATOR='<actual-root-receipt-relative-locator>'
RECEIPT_SHA256='<sha256-from-the-recorded-trusted-reference>'
python3 - "$RECEIPT_LOCATOR" "$RECEIPT_SHA256" <<'PY'
import json
import sys
from scripts.gates.hash_dag import verify_hash_dag_result
result = verify_hash_dag_result('.', {'locator': sys.argv[1], 'sha256': sys.argv[2]})
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result['result'] == 'PASS' else 1)
PY
```

看结果、完整 / 有限的 / 无环的、节点与边计数、原因 / 细节。DAG PASS 只证明该 API 的图完整性与绑定；正式目录还检查当前任务、授权、独立性与依赖要求，不能只凭 DAG PASS 宣布 G1。

输入失配时定位实际改变的负责人 / 任务，保留旧收据，准备新版本和新证据；不刷新历史哈希、不全量重签无关任务。架构搬移后的合同还需要纳入正式冻结输入，文档链接不能代替绑定。

## 判断与下一步

单份收据只证明其种类和任务。正式任务完成要有当前要求的验证 / 审查 / 目录链；前置链齐全后才进入 [G1 决定](06-phase1-decision.md)。目前 Java Gradle 适配器与真实上下文缺口见 [手册入口](README.md)，保持实际 FAIL / BLOCKED。
