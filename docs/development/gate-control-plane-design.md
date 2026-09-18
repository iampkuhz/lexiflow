<a id="lexiflow-gate-control-plane-设计合同"></a>

# LexiFlow 验收控制面设计合同

> 合同任务：`LF-TSK-QLT-0003`；版本和依赖以当前目录为准。
> 交付物: 架构 Gate 合同
> 证据: 冻结输入计划与不可变收据示例
> 状态: 当前设计合同; 实现与目录收据分别验证

## 1. 任务边界

本文只定义 G1 所需的 Gate 控制面合同：唯一 CLI、纯计划编译器、显式注册表、`run` 与 `status` 语义、三态聚合、不可覆盖收据、签发者来源证明，以及当前输入证据链。它给实现任务提供稳定接口，不在 `LF-TSK-QLT-0003@4` 内交付 CLI、存储、检查器、审查工作流协调或引导迁移。V3 保持唯一执行层，并通过当前执行证据合同采用实例身份隔离：`TASK_VALIDATION` 执行交付检查，`INDEPENDENT_REVIEW` 与 `CATALOG_DECISION` 只消费不可变证据/收据。

本合同的证明对象是设计语义、冻结输入计划和不可变收据示例；可执行控制面及其正式验收由对应实现任务提供独立证据。

本文不改变 Harness 交接结构定义，不实现 Qoder 运行器，不做派发并发判断，也不进入阶段 2+ 产品设计。现有职责保持：

- `LF-TSK-QLT-0002`：规划目录的结构定义、ID、负责人、类型化依赖与 DAG 验证；
- `LF-TSK-QLT-0004`：验收案例可追溯性合同；
- `LF-TSK-QLT-0005`：派发前候选与活动任务的负责人、文件声明、写入重叠与合同写入者冲突判定；
- `LF-TSK-QLT-0006@3/2.1.0`：运行器身份、原始完成记录生命周期、完成记录先于回调、单 Qoder 运行与 按共享策略执行的守护检查器；它不负责把 Qoder 输出解释为六个结果字段；
- Gate 实现任务：本文定义的编译器、注册表、执行、收据存储、审查链与 G1 集成。

## 2. 唯一 CLI 与控制流

<a id="21-public-surface"></a>

### 2.1 公开接口

唯一公开入口固定为：

```text
python3 scripts/gates/cli.py plan --mode incremental|full [--evidence-packet <repo-relative-path>] [--issuer-packet <repo-relative-path>]
python3 scripts/gates/cli.py run --mode incremental|full [--evidence-packet <repo-relative-path>] [--issuer-packet <repo-relative-path>]
python3 scripts/gates/cli.py status --run-id <uuid>
```

`plan` 与 `run` 必须获得两个显式证据包定位。调用方可以传参数，也可以由受信主代理/CI 启动器各绑定一个环境变量：`LEXIFLOW_GATE_EVIDENCE_PACKET` 与 `LEXIFLOW_GATE_ISSUER_PACKET`。值只能是单个仓库相对路径；参数与环境同时出现时必须逐字相等，否则 `FAIL/evidence-context-conflict`；任一定位缺失时 `FAIL/missing-evidence-context`。不得从 `latest`、目录扫描、文件时间、多个候选或进程身份推断证据包。这样 `harness/manifest.yaml` 与 `AGENTS.md` 已声明的精确 `run --mode incremental` 命令可由启动器注入本次定位后执行，同时 CLI 参数为人工复现保留可见路径。编译器冻结两个证据包的定位/哈希，运行再把同一签发者证据包绑定到进程身份。

`--receipt-kind TASK_VALIDATION|INDEPENDENT_REVIEW|CATALOG_DECISION` 可显式指定；省略时为 `TASK_VALIDATION`。内部检查器不提供第二套交付入口，只能通过显式注册表由 `run` 调用。主代理/CI 先通过 `QLT-0007` 与 `QLT-0014` 物化器得到本次不可变证据包路径，再绑定环境或传入参数；不同收据种类使用对应授权的签发者证据包。

实现按依赖串行扩展同一个入口：`QLT-0010` 先交付稳定的收据种类处理器/存储接口和 `TASK_VALIDATION` 路由，尚未安装的种类明确 `FAIL/unsupported-receipt-kind`；`QLT-0011` 在其当前依赖 PASS 后向同一 `cli.py` 安装 `INDEPENDENT_REVIEW` 路由；`QLT-0013` 最后安装 `CATALOG_DECISION` 路由。三个任务对 `cli.py` 的写入声明因硬性/合同依赖严格串行，派发预检必须拒绝并发声明；不得创建第二个 CLI 或靠导入副作用注册。

| 命令 | 副作用 | 身份 | 输出 |
|---|---|---|---|
| `plan` | 零副作用；不写计划、收据、指针、事件或生命周期文件，不执行检查器 | 不生成 `plan_id` 或 `run_id` | stdout 输出规范计划 JSON 与 `content_fingerprint` |
| `run` | 复用同一个纯计划编译器并持久化实际计划；只有 `TASK_VALIDATION` 执行选定的检查，审查/目录只验证不可变证据/收据 | 每次生成新的 `run_id` | `PASS`、`BLOCKED` 或 `FAIL` |
| `status` | 单次只读；不等待、不重试、不 resume、不重新判定 | 必须显式提供 `run_id` | 根据 start 事件或最终收据返回 `RUNNING` 或 `FINALIZED` |

本合同不保留 `run --plan-file`，因此没有模式/plan-file 歧义，也没有按 ID 复用计划的路径。将来若增加 `--plan-file`，它必须与 `--mode` 互斥，并在执行前验证其 `content_fingerprint` 与当前输入；该扩展需单独版本 CLI 合同。

<a id="22-plan-与-run-使用同一-compiler"></a>

### 2.2 `plan` 与 `run` 使用同一编译器

解析出唯一的 `E`/`P` 后，`plan --mode X` 和 `run --mode X` 必须调用同一个无写入的 `compile_plan(mode, receipt_kind, evidence_packet=E, issuer_packet=P)`。两个显式证据包完整闭合上下文；编译器返回规范载荷与指纹：

```text
same inputs + same registry + same policy -> same canonical plan
same canonical plan                       -> same content_fingerprint
each run                                  -> new run_id
```

`content_fingerprint` 是对 RFC 8785 JSON 规范化方案产生的 UTF-8 载荷计算的 SHA-256。计算投影排除指纹自身、生成时间、输出位置和所有 Gate 运行身份。`run_id` 只标识一次执行，不进入指纹；不得用指纹充当运行身份，也不得用运行 ID 改变相同内容的指纹。

### 2.3 `run` 的可观察顺序

`run --mode X --evidence-packet E --issuer-packet P` 的合同顺序为：

1. 使用纯编译器生成本次实际规范计划；
2. 验证计划与当前输入，生成唯一 `run_id`；
3. 在该运行下持久化实际计划的精确字节；
4. 在启动任何检查器 **之前**，写入并刷新一个结构化 start 事件；
5. 将同一 `START` 事件的 `run_id` 与事件定位以单行 JSON 写到 stderr 并刷新，使调用方在检查器开始前取得 `status --run-id` 的精确地址；
6. 两次刷新均成功后，`TASK_VALIDATION` 才按冻结注册表执行必需检查；`INDEPENDENT_REVIEW` 与 `CATALOG_DECISION` 必须保持零选定的检查，并直接进入证据消费核验器；
7. 聚合交付检查结果或证据复核结果，并创建不可覆盖最终收据。

运行存储使用确定性目录 `tmp/quality/runs/{run_id}/`；实际计划、start 事件与最终收据的唯一定位分别为 `plan.json`、`start.json`、`receipt.json`。`status --run-id` 只把经 UUID 验证的 ID 代入这三个固定路径，不扫描目录、不解析 `latest`、不跟随目录外符号链接。

Start 事件至少包含 `schema_version`、`run_id`、`receipt_kind`、`content_fingerprint`、已持久化的计划定位/哈希、受信签发者证据包定位/哈希、进程身份和 `started_at`。stderr 的 `START` JSON 至少包含 `schema_version`、`event=START`、`run_id`、启动事件定位/哈希；不得输出证据包内容或敏感值。无法持久化/刷新磁盘事件，或无法发出/刷新调用方事件时，均不得启动检查器，命令以 `FAIL/start-event-unavailable` 结束。调用方可以在同步 `run` 尚未返回时，用已收到的 `run_id` 单次查询状态；不需要扫描运行目录。

`status --run-id` 先查最终收据；存在且完整时返回 `FINALIZED` 与三态结果。否则读取已刷新的 start 事件并返回 `RUNNING`，不根据 PID、日志或时间推测结果。找不到、损坏或身份/哈希不一致时 CLI 失败。本文不定义持久化 `PLANNED` 状态。

<a id="3-frozen-input-plan-合同"></a>

## 3. 冻结输入计划合同

### 3.1 V1 必需字段

结构定义名称为 `lexiflow.gate-plan.v1`。规范载荷必须完整冻结：

1. `schema_version`、`mode` 与 `receipt_kind`；
2. 精确 `task_id`、`task_version`、`change_version` 与 `task_source` 定位/哈希；
3. 通用显式证据包定位/哈希，以及它分别绑定的原始任务、原始完成记录、stdout、stderr、差异和测试证据哈希；
4. 证据包中由主代理显式核对的六个结果字段与受验对象身份；
5. 原始调用者 `allowed_files`/`forbidden_files` 字符串、规范化的数组、规范文件声明和变化的/允许范围/禁止范围/声明三方对账；
6. 受信签发者证据包定位/哈希；
7. 已消费来源、策略、代理/运行时清单、任务模板、验收案例注册表与注册表入口定位/哈希；
8. `execution.layer`、`checker_execution` 与 `source`；`TASK_VALIDATION` 还冻结选定的检查的稳定 ID/版本、负责人、必需属性、选择原因、声明的命令、命令 ID、固定参数序列与注册表入口哈希，审查/目录的 `checks` 必须为空；
9. 验收条件/证据、效果检查与风险的逐项预期映射；
10. 对以上规范载荷的 `content_fingerprint`。

路径使用仓库相对 POSIX 形式。输入必须保存存在状态与 SHA-256；目录输入展开为稳定排序的文件清单。编译器必须同时保存调用者原始范围字符串和解析后的规范化的数组，规范声明也单独保存。对账对变化的文件、允许范围/禁止范围数组与声明做三方检查：每个变化的文件必须允许、不得禁止、且必须有声明；每个声明必须完全落在允许范围内且不与禁止范围相交。`claims_outside_allowed` 或 `claims_intersecting_forbidden` 非空就是调用者范围与声明冲突，计划为 FAIL。`run` 针对计划中冻结的字节执行，当前字节与哈希不一致时为 `FAIL/input-drift`。

<a id="32-generic-explicit-evidence-packet"></a>

### 3.2 通用显式证据包

Gate 不改变 Qoder Harness 的 14 个调用者字段。Qoder 调用者结构定义保持：

```text
goal, task_id, task_source, task_version, change_version,
allowed_files, forbidden_files, required_context, expected_output,
acceptance_criteria, acceptance_evidence, validation_command,
failure_policy, parent_client
```

Codex 子代理不伪装成 Qoder 任务。一个工作包仍使用 `harness/agent-policy.manifest.yaml` 已声明的工作包调用者合同；进入 Gate 时，为包内每个目标任务保存一个 `lexiflow.codex-work-package-task-projection.v1` 原始任务。投影顶层精确绑定 `work_package_id`、原顺序且至少两项的 `task_ids[]`、`target_task_id == task_id`、目标任务/版本/来源/范围/验收/验证、完整嵌套 `caller_contract` 和运行器身份。`task_versions`、`change_versions`、`expected_outputs_by_task`、`acceptance_by_task` 与 `validation_commands` 的键集合必须精确等于 `task_ids[]`，目标项必须和顶层投影一致。调用者合同不含运行器身份；`parent_session_id`、`agent_id`、`run_id`、`session_id` 与 `client=codex` 只在运行时绑定。精确字段集拒绝 `permission_mode`、`_resume_mode`、`title` 或其他 Qoder 字段夹带。

物化器接受 `client=qoder|codex` 并拒绝其他客户端。两种客户端都必须通过相同原始任务/完成记录、证据包身份和任务身份对账；Codex 原始任务另外验证稳定工作包身份、有序任务成员关系、目标、调用者/目标映射和范围包含关系。原始运行器 JSON 可以保留运行器的格式化/非规范空白字符，但重复键必须在任何层级失败，实际字节仍由定位/哈希冻结。规划器再按客户端使用互斥的必需/可选字段集合：Qoder 初始/resume 合同不变；Codex 只读取上述版本化投影，并将工作包任务/版本/负责人/输出/验收/验证/工作包范围与当前目录全量对账。计划中的负责人、`discovered_from`、文件声明和目标范围仍只来自当前目录，不能由调用者投影注入。

`LF-TSK-QLT-0006` 只提供原始任务/完成记录生命周期和运行器绑定的身份；其完成记录合同不承诺包含六个结果字段。后续首个 JIT 任务 `LF-TSK-QLT-0007` 提供通用显式证据包物化器。物化器不解释代理自然语言，而是要求主代理显式核对并提交：

- 六个结果字段：`status`、`changed_files`、`validation`、`acceptance_evidence`、`effect_checks`、`risks`；
- 原始任务证据包定位/哈希；
- 原始完成记录定位/哈希；
- 原始 stdout 与 stderr 各自的定位/哈希；
- 经审阅的差异定位/哈希；
- 已执行的测试证据定位/哈希；
- 主代理证明身份、核对时间和逐字段来源绑定。

物化器把这些结构化输入写为一个不可变显式证据包。它禁止从 Qoder 最终回答、回调、stdout/stderr、日志或任何自由文本自动猜测、抽取或补齐六字段。原始产物只被哈希绑定；字段值来自主代理的显式核对。缺任一字段、原始定位/哈希、差异/测试证据、证明或身份对账时为 `FAIL/evidence-packet-incomplete`。

同一个通用证据包合同还必须表达可验证的零写入：只读审查/目录路径使用 `changed_files: []`、空 `snapshot.files` 与零字节差异，三者仍须精确相等，原始任务/完成记录/测试、范围/声明与主代理证明仍完整绑定。规划器只允许 `INDEPENDENT_REVIEW` 或 `CATALOG_DECISION` 消费这种零写入证据包；`TASK_VALIDATION` 必须保留非空受验对象变更文件快照。这样空集合来自哈希绑定证据包，而不是审阅者在运行时自报，也不会放宽验证受验对象的当前字节证明。

因此，已耗尽 Qoder 初始/修正配额的任务可以由一个不同执行者的 Codex 工作包产生零写入审查受验对象：每个任务仍保留自己的证据/计划结果，包身份不会折叠成一个目录结果。审阅者签发者仍必须和生产者独立。`client` 只标识工具类型，两个 Codex 实例可以共享真实会话，但必须具有不同的经核验的执行者；相同执行者/代理/运行/实例或重放身份不能借工作包投影绕过签发者/审查独立性。

`QLT-0007` 只物化通用显式结果证据包，不创建或信任签发者身份。独立的 `QLT-0014` 为人工、Qoder、Codex 审阅者或 CI 执行者物化受信签发者证据包。两项只共享基础身份格式；结构定义、信任来源、负例、定位/哈希和验收结果彼此独立。

`QLT-0014` 只调用 `harness/gate-issuer-authorities.yaml` 中固定注册且由 QLT 负责人管理的核验器；请求载荷不得选择核验器或权限来源。Qoder 核验器核对运行器拥有的任务/完成记录来源证明，Codex 核验器核对桌面端宿主绑定的当前父级/会话上下文，人工核验器核对注册表操作员记录与已认证的证明，CI 核验器核对工作负载注册表入口与证明。未知核验器、权限来源不可用、自报执行者/角色、陈旧/重放的证明或收据种类授权不匹配均为 FAIL。规划器 `QLT-0008` 只冻结已经已物化的的显式证据包和签发者证据包，不直接解释 QLT-0006 完成记录。

运行时身份和本地生成身份采用不同版本约束：来自 Codex、Qoder、人工或 CI 权限来源的 `session_id`、`parent_session_id` 与 Qoder `run_id` 必须是规范、非零、RFC 变体 UUID v1–v8，以兼容当前桌面端/运行器发出的 UUIDv7；LexiFlow 自己生成并负责防重放的 `issuer_instance_id`、`attestation_id`、`nonce` 及重放声明仍必须是规范 UUIDv4。放宽外部来源版本不得放宽证据包命名空间或 nonce 的 UUIDv4 约束。

<a id="33-最小完整-plan-json-示例"></a>

### 3.3 最小完整计划 JSON 示例

下面是 `lexiflow.gate-plan.v1` 的最小结构完整示意测试样例。除明确说明为对显示内容实算的规范哈希外，重复数字形式的 64 位十六进制值只是结构定义占位符；引用的产物不要求在仓库存在，也没有被当前核验器解析。它不是当前仓库的 PASS 证据。

```json
{
  "schema_version": "lexiflow.gate-plan.v1",
  "mode": "incremental",
  "receipt_kind": "TASK_VALIDATION",
  "task": {
    "task_id": "LF-TSK-QLT-0002",
    "task_version": 1,
    "change_version": "1.0.0",
    "task_source": {
      "locator": "planning/workstreams.yaml",
      "sha256": "1111111111111111111111111111111111111111111111111111111111111111"
    }
  },
  "subject": {
    "explicit_evidence_packet": {
      "locator": "tmp/quality/evidence/fixture/evidence-packet.json",
      "sha256": "2222222222222222222222222222222222222222222222222222222222222222"
    },
    "raw_artifacts": {
      "task": {
        "locator": "tmp/harness/tasks/fixture/task.json",
        "sha256": "2323232323232323232323232323232323232323232323232323232323232323"
      },
      "completion": {
        "locator": "tmp/harness/tasks/fixture/completion.json",
        "sha256": "2424242424242424242424242424242424242424242424242424242424242424"
      },
      "stdout": {
        "locator": "tmp/harness/tasks/fixture/stdout.log",
        "sha256": "2525252525252525252525252525252525252525252525252525252525252525"
      },
      "stderr": {
        "locator": "tmp/harness/tasks/fixture/stderr.log",
        "sha256": "2828282828282828282828282828282828282828282828282828282828282828"
      },
      "diff": {
        "locator": "tmp/quality/evidence/fixture/reviewed.diff",
        "sha256": "2626262626262626262626262626262626262626262626262626262626262626"
      },
      "tests": [
        {
          "locator": "tmp/quality/evidence/fixture/tests.json",
          "sha256": "2727272727272727272727272727272727272727272727272727272727272727"
        }
      ]
    },
    "main_agent_attestation": {
      "actor_id": "main_agent_fixture",
      "reviewed_at": "2026-09-16T10:00:00Z",
      "result_fields": {
        "status": "PASS",
        "changed_files": [
          "scripts/gates/planning/__init__.py"
        ],
        "validation": {
          "status": "PASS",
          "evidence_locator": "tmp/quality/evidence/fixture/tests.json"
        },
        "acceptance_evidence": [
          "tmp/quality/evidence/fixture/tests.json"
        ],
        "effect_checks": {
          "behavior": "PASS",
          "regression": "PASS"
        },
        "risks": [
          "QLT-PLAN-001"
        ]
      },
      "field_source_bindings": {
        "status": "explicit-main-agent-review",
        "changed_files": "reviewed-diff",
        "validation": "test-evidence",
        "acceptance_evidence": "test-evidence",
        "effect_checks": "explicit-main-agent-review",
        "risks": "explicit-main-agent-review"
      }
    },
    "identity": {
      "parent_session_id": "11111111-1111-4111-8111-111111111111",
      "agent_id": "agent_fixture",
      "run_id": "22222222-2222-4222-8222-222222222222",
      "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      "client": "qoder",
      "parent_client": "codex"
    }
  },
  "issuer_packet": {
    "locator": "tmp/quality/issuers/33333333-3333-4333-8333-333333333333.json",
    "sha256": "e5dd4057e43534f42372910245da488d40b7dada88499f625e9ea1823584df67"
  },
  "scope": {
    "changed_files": [
      "scripts/gates/planning/__init__.py"
    ],
    "raw_caller_strings": {
      "allowed_files": "scripts/gates/**, tests/gates/**",
      "forbidden_files": "planning/**, harness/**"
    },
    "normalized": {
      "allowed_files": [
        "scripts/gates/**",
        "tests/gates/**"
      ],
      "forbidden_files": [
        "harness/**",
        "planning/**"
      ],
      "canonical_file_claims": [
        "scripts/gates/**",
        "tests/gates/**"
      ]
    },
    "three_way_reconciliation": {
      "changed_outside_allowed": [],
      "changed_matching_forbidden": [],
      "changed_without_claim": [],
      "claims_outside_allowed": [],
      "claims_intersecting_forbidden": [],
      "status": "PASS"
    }
  },
  "consumed_inputs": [
    {
      "locator": "planning/workstreams.yaml",
      "state": "present",
      "sha256": "1111111111111111111111111111111111111111111111111111111111111111"
    },
    {
      "locator": "planning/task-template.yaml",
      "state": "present",
      "sha256": "4444444444444444444444444444444444444444444444444444444444444444"
    },
    {
      "locator": "harness/agent-policy.manifest.yaml",
      "state": "present",
      "sha256": "5555555555555555555555555555555555555555555555555555555555555555"
    },
    {
      "locator": "harness/agent-runtime.manifest.yaml",
      "state": "present",
      "sha256": "6666666666666666666666666666666666666666666666666666666666666666"
    },
    {
      "locator": "harness/manifest.yaml",
      "state": "present",
      "sha256": "7777777777777777777777777777777777777777777777777777777777777777"
    },
    {
      "locator": "docs/acceptance-cases/phase-1.md",
      "state": "present",
      "sha256": "abababababababababababababababababababababababababababababababab"
    },
    {
      "locator": "harness/gate-check-registry.yaml",
      "state": "present",
      "sha256": "8888888888888888888888888888888888888888888888888888888888888888"
    }
  ],
  "checks": [
    {
      "check_id": "qlt.planning.validate",
      "check_version": 1,
      "owner": "LF-WS-QLT",
      "required": true,
      "selection_reasons": [
        "scripts/gates/planning/__init__.py -> scripts/gates/planning/** -> qlt.planning.validate"
      ],
      "declared_validation_command": "python3 -m scripts.gates.planning --root .",
      "command_id": "qlt.planning.validate.v1",
      "fixed_argv": [
        "python3",
        "-m",
        "scripts.gates.planning",
        "--root",
        "."
      ],
      "registry_entry_sha256": "9999999999999999999999999999999999999999999999999999999999999999",
      "acceptance_criterion_ids": [
        "LF-TSK-QLT-0002.acceptance_criteria[0]"
      ],
      "effect_check_ids": [
        "behavior",
        "regression"
      ]
    }
  ],
  "expectations": {
    "acceptance": [
      {
        "criterion_id": "LF-TSK-QLT-0002.acceptance_criteria[0]",
        "required_evidence": "typed checker result and test log hash"
      }
    ],
    "effect_checks": [
      {
        "effect_id": "behavior",
        "required": true
      },
      {
        "effect_id": "regression",
        "required": true
      }
    ],
    "risks": [
      {
        "risk_id": "QLT-PLAN-001",
        "required_fields": [
          "impact",
          "mitigation",
          "fallback",
          "remaining_limitation"
        ]
      }
    ]
  },
  "content_fingerprint": "550bbf557de34bd315685799da793678984e0da571ba29755cc249b7b495c3f0"
}
```

<a id="4-显式-registry-与命令安全"></a>

## 4. 显式注册表与命令安全

注册表是版本化的、显式、有序的声明，不做目录扫描、入口自动发现或导入副作用。每个检查入口至少定义稳定 `check_id`/版本、负责人、模式、有限触发条件模式、必需/建议性、`declared_validation_command`、独立 `command_id`、固定参数序列、仓库相对 cwd、超时、已消费输入、类型化结果合同、验收/效果映射和规范入口哈希。

Harness 的调用者 `validation_command` 保持原有“声明的完整命令字符串”语义。编译器只做一次完整字符串相等比较：调用者值必须与注册表入口的 `declared_validation_command` 精确相等。它不把该字符串当作命令 ID，不分词、不插值，也绝不执行它。

执行时 `run` 使用同一注册表入口中独立的 `command_id` 和固定参数序列。调用者不能提供或覆盖命令 ID/argv。真实规划验证器入口为：

```json
{
  "check_id": "qlt.planning.validate",
  "check_version": 1,
  "declared_validation_command": "python3 -m scripts.gates.planning --root .",
  "command_id": "qlt.planning.validate.v1",
  "fixed_argv": [
    "python3",
    "-m",
    "scripts.gates.planning",
    "--root",
    "."
  ],
  "cwd": ".",
  "consumed_inputs": [
    "planning/workstreams.yaml",
    "planning/task-template.yaml",
    "harness/agent-policy.manifest.yaml",
    "harness/agent-runtime.manifest.yaml"
  ]
}
```

声明字符串不匹配、未知命令 ID、argv 覆盖、注册表入口哈希漂移或尝试 shell 执行均为 `FAIL/registry-mismatch`。需要不同参数时必须新增并版本注册表入口。

<a id="5-三态与-receipt-contracts"></a>

## 5. 三态与收据合同

### 5.1 三态

Gate 验证结果只允许：

| 结果 | 含义 |
|---|---|
| `PASS` | `TASK_VALIDATION` 的必需检查已实际运行且全部通过，或审查/目录的必需不可变证据/收据已完整验证且全部通过 |
| `BLOCKED` | 检查可信完成，但发现被验收内容不满足规则或验收条件 |
| `FAIL` | 无法可信完成验证，例如输入无效、必需检查未运行、输出损坏、身份/哈希不一致或证据不完整 |

聚合固定为 `FAIL > BLOCKED > PASS`。`queued`、`acknowledged`、未触发、未运行、跳过、不可用、回调已投递或退出 `0` 都不是 PASS。`TASK_VALIDATION` 的必需检查缺失或集合为空时为 FAIL；审查/目录反而必须拒绝非空交付检查集合，并以按种类划分的证据完整性决定结果。

<a id="52-所有-receipt-的共同必需字段"></a>

### 5.2 所有收据的共同必需字段

结构定义名称为 `lexiflow.gate-receipt.v1`。每份收据都包含：

- `schema_version`、`receipt_kind`、唯一 `run_id`、计划定位/哈希与 `content_fingerprint`；
- 受信签发者证据包定位/哈希、执行者身份与 Gate 进程身份；
- `started_at`、`finished_at`、任务/变更身份与当前来源指纹；
- 产物清单定位/哈希；
- 按种类划分的完整性结果；
- 规范重跑 argv、最终 `result` 与原因代码。

不同收据种类的必需载荷为：

| 收据种类 | 必需内容 | PASS 条件 |
|---|---|---|
| `TASK_VALIDATION` | 显式证据包定位/哈希及其原始任务/完成记录/输出/差异/测试绑定；主代理六字段证明；受验对象身份；三方范围对账；每个必需检查的声明的命令、命令 ID、固定参数序列注册表哈希、进程事实、类型化结果与证据定位/哈希；逐项验收/效果/风险证据 | 证据包/受验对象身份/哈希一致；受验对象变更文件快照非空；所有必需检查实际执行且 PASS；主代理证明、原始绑定及验收/效果/风险证据完整 |
| `INDEPENDENT_REVIEW` | 被审验证收据定位/哈希；审阅者受信签发者证据包与审阅者身份；零交付检查执行记录；结构化独立性断言；审查范围/来源/差异哈希；审查计划中哈希绑定写入集合对账；重新读取验证证据包绑定的变更文件快照并与仓库当前受验对象字节对账；逐项发现、重跑证据与决定 | 受验对象验证 PASS；审阅者独立性可证明；计划为证据消费且 `checks=[]`；零写入快照/差异/changed-files 来自已验证证据包；受验对象快照在审查与目录闭包时均重新验证当前；经审阅的/当前输入一致；按种类划分的证据完整 |
| `CATALOG_DECISION` | 验证与独立审阅收据定位/哈希；零交付检查执行记录；当前任务/变更/来源/注册表/策略定位/哈希；验收案例注册表定位/哈希及孤立/重复/current-mapping 结果；必需依赖收据定位/哈希；每份前序/依赖收据的受信签发者证据包与当前权限来源注册表核验；审阅者独立性核验；新鲜度对账；目录结果 | 计划为证据消费且 `checks=[]`；两份前序收据 PASS；审查独立；验收注册表完整且当前；全部版本、规范定位与哈希对当前输入一致；每份前序/依赖收据的签发者权限来源重新验证；全部必需依赖为当前输入 PASS |

缺少该种类任一必需字段、定位无法解析、规范定位不一致、产物哈希不匹配、签发者权限来源未重新验证，或只靠自由文本/调用方自报声明时为 `FAIL/evidence-incomplete`。

<a id="6-issuer-identity-与-reviewer-independence"></a>

## 6. 签发者身份与审阅者独立性

<a id="61-trusted-issuer-packet"></a>

### 6.1 受信签发者证据包

受验对象身份和收据签发者身份是两条不同链。每次 `plan`/`run` 都从显式参数或受信启动器的单值环境绑定消费一个不可变受信签发者证据包。`QLT-0014` 签发者物化器核对运行器来源证明，或由主代理把人工、当前 Codex 或 CI 执行者绑定到上述可信身份来源；QLT-0006 本身不产出该证据包，QLT-0007 也不授权签发者。下面的证据包与第 7 节 `CATALOG_DECISION` 收据测试样例是同一个签发者，字段必须逐项一致：

```json
{
  "schema_version": "lexiflow.trusted-issuer-packet.v1",
  "issuer_instance_id": "33333333-3333-4333-8333-333333333333",
  "actor_type": "codex",
  "actor_id": "catalog_issuer_fixture",
  "parent_session_id": "44444444-4444-4444-8444-444444444444",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "client": "codex",
  "role": "gate-receipt-issuer",
  "authorized_receipt_kinds": [
    "TASK_VALIDATION",
    "CATALOG_DECISION"
  ],
  "authority": {
    "registry_locator": "harness/gate-issuer-authorities.yaml",
    "registry_sha256": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
    "verifier_id": "codex.current-session.v1",
    "evidence_locator": "tmp/quality/issuers/33333333-3333-4333-8333-333333333333/authority-evidence.json",
    "evidence_sha256": "cececececececececececececececececececececececececececececececece"
  },
  "materialized_at": "2026-09-16T10:00:00Z"
}
```

收据保存证据包定位/哈希和经验证的执行者字段。每个调用必须获得经宿主/运行器核验器验证的签发者实例、执行者/会话身份与授权收据种类；禁止复制受验对象的 `agent_id`、`run_id` 或签发者实例来填签发者。共享 `client` 或实际宿主会话本身不是复制执行者身份。宿主核验器仍必须认证实际执行者，执行者不因重新签发身份凭证/运行改变。独立审查仍同时排除受验对象生产者与验证签发者。无法取得受信证据包时为 `FAIL/issuer-untrusted`。

Qoder 权限来源来源证明直接绑定运行器实际持久化的 `task.json` 与 `completion.json` 字节。运行器使用稳定格式化 JSON，因此签发者物化器与规划器都按严格 JSON 解析并拒绝重复键、非法数字和身份漂移，同时允许空白字符/非规范序列化；定位/哈希仍冻结原始字节。签发者证据包自身、Codex/人工/CI 证明与正式收据继续要求规范 JSON。

<a id="62-gate-process-identity"></a>

### 6.2 Gate 进程身份

`run` 生成独立进程身份，并在 start 事件与最终收据中一致保存：

```json
{
  "process_instance_id": "55555555-5555-4555-8555-555555555555",
  "gate_run_id": "66666666-6666-4666-8666-666666666666",
  "executable_locator": "scripts/gates/cli.py",
  "executable_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "issuer_packet_sha256": "e5dd4057e43534f42372910245da488d40b7dada88499f625e9ea1823584df67"
}
```

`gate_run_id` 必须等于收据 `run_id`，进程身份必须绑定签发者证据包哈希。Gate 进程身份不冒充执行者，也不复用受验对象运行 ID。

<a id="63-reviewer-independence"></a>

### 6.3 审阅者独立性

`INDEPENDENT_REVIEW` 至少结构化证明：

1. 审阅者执行者/签发者实例与受验对象生产者不同；
2. 审查 Gate 运行 ID 与受验对象运行、验证运行均不同；
3. 审阅者没有写入受验对象变化的文件；
4. 审查只引用已完成且不可覆盖的受验对象收据定位/哈希，不回写该收据；
5. 执行者、进程与受验对象三条身份链可分别追溯，不能只写 `independent=true`。

任一比较缺失或同一生产者自审时为 `FAIL/reviewer-not-independent`。`CATALOG_DECISION` 必须重新验证审查收据的证据包哈希、审阅者身份与独立性证据。

<a id="7-最小完整-immutable-receipt-json-示例"></a>

## 7. 最小完整不可变收据 JSON 示例

下面是 `CATALOG_DECISION` 的最小结构完整示意测试样例。重复数字形式的哈希与前序定位是结构定义占位符；对应产物不要求存在，也没有在当前仓库被核验器解析。另一个 `lexiflow.gate-plan.v1` 代码块的 `content_fingerprint` 和显示的受信签发者证据包对象哈希在文档审阅时根据各自显示内容实算；本收据块内的重复数字哈希仍是占位符。它不是当前仓库的 PASS 证据。

```json
{
  "schema_version": "lexiflow.gate-receipt.v1",
  "receipt_kind": "CATALOG_DECISION",
  "run_id": "66666666-6666-4666-8666-666666666666",
  "plan": {
    "locator": "tmp/quality/runs/66666666-6666-4666-8666-666666666666/plan.json",
    "sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "content_fingerprint": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  },
  "issuer": {
    "trusted_issuer_packet": {
      "locator": "tmp/quality/issuers/33333333-3333-4333-8333-333333333333.json",
      "sha256": "e5dd4057e43534f42372910245da488d40b7dada88499f625e9ea1823584df67"
    },
    "actor_identity": {
      "issuer_instance_id": "33333333-3333-4333-8333-333333333333",
      "actor_type": "codex",
      "actor_id": "catalog_issuer_fixture",
      "parent_session_id": "44444444-4444-4444-8444-444444444444",
      "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      "client": "codex",
      "role": "gate-receipt-issuer"
    },
    "process_identity": {
      "process_instance_id": "55555555-5555-4555-8555-555555555555",
      "gate_run_id": "66666666-6666-4666-8666-666666666666",
      "executable_locator": "scripts/gates/cli.py",
      "executable_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "issuer_packet_sha256": "e5dd4057e43534f42372910245da488d40b7dada88499f625e9ea1823584df67"
    }
  },
  "started_at": "2026-09-16T10:01:00Z",
  "finished_at": "2026-09-16T10:01:02Z",
  "task": {
    "task_id": "LF-TSK-QLT-0002",
    "task_version": 1,
    "change_version": "1.0.0"
  },
  "current_inputs": {
    "source_snapshot_fingerprint": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "registry": {
      "locator": "harness/gate-check-registry.yaml",
      "sha256": "1717171717171717171717171717171717171717171717171717171717171717"
    },
    "policy": {
      "locator": "harness/agent-policy.manifest.yaml",
      "sha256": "1818181818181818181818181818181818181818181818181818181818181818"
    },
    "acceptance_case_registry": {
      "locator": "docs/acceptance-cases/phase-1.md",
      "sha256": "abababababababababababababababababababababababababababababababab",
      "orphan_cases": [],
      "duplicate_cases": [],
      "current_mapping_status": "PASS"
    }
  },
  "subject_receipts": {
    "task_validation": {
      "locator": "tmp/quality/runs/77777777-7777-4777-8777-777777777777/receipt.json",
      "sha256": "1212121212121212121212121212121212121212121212121212121212121212",
      "result": "PASS"
    },
    "independent_review": {
      "locator": "tmp/quality/runs/88888888-8888-4888-8888-888888888888/receipt.json",
      "sha256": "1313131313131313131313131313131313131313131313131313131313131313",
      "result": "PASS"
    }
  },
  "required_dependency_receipts": [
    {
      "task_id": "LF-TSK-QLT-0001",
      "task_version": 1,
      "change_version": "1.0.0",
      "locator": "tmp/quality/runs/99999999-9999-4999-8999-999999999999/receipt.json",
      "sha256": "1414141414141414141414141414141414141414141414141414141414141414",
      "result": "PASS"
    }
  ],
  "reviewer_independence": {
    "reviewer_issuer_packet_sha256": "1515151515151515151515151515151515151515151515151515151515151515",
    "reviewer_identity": {
      "issuer_instance_id": "77777777-7777-4777-8777-777777777778",
      "actor_id": "independent_reviewer_fixture"
    },
    "producer_identity": {
      "agent_id": "agent_fixture",
      "subject_run_id": "22222222-2222-4222-8222-222222222222"
    },
    "producer_and_reviewer_differ": true,
    "review_run_is_distinct": true,
    "reviewer_wrote_subject_files": false,
    "status": "PASS"
  },
  "freshness_reconciliation": {
    "task_version_match": true,
    "change_version_match": true,
    "source_snapshot_match": true,
    "registry_policy_match": true,
    "dependency_receipts_current": true,
    "status": "PASS"
  },
  "artifact_manifest": {
    "locator": "tmp/quality/runs/66666666-6666-4666-8666-666666666666/artifact-manifest.json",
    "sha256": "1616161616161616161616161616161616161616161616161616161616161616"
  },
  "completeness": {
    "required_fields_checked": true,
    "kind_specific_fields_checked": true,
    "status": "PASS"
  },
  "canonical_rerun": {
    "argv": [
      "python3",
      "scripts/gates/cli.py",
      "run",
      "--mode",
      "incremental",
      "--evidence-packet",
      "tmp/quality/evidence/fixture/evidence-packet.json",
      "--issuer-packet",
      "tmp/quality/issuers/33333333-3333-4333-8333-333333333333.json",
      "--receipt-kind",
      "CATALOG_DECISION"
    ]
  },
  "catalog_task_status": "PASS",
  "result": "PASS",
  "reasons": []
}
```

<a id="8-current-input-evidence-chain-与-hash-dag"></a>

## 8. 当前输入证据链与哈希 DAG

<a id="81-evidence-chain"></a>

### 8.1 证据链

```text
explicit evidence packet + trusted issuer packet + current inputs
                                  |
                                  v
                           TASK_VALIDATION
                                  |
                                  v
                         INDEPENDENT_REVIEW
                                  |
                                  v
                           CATALOG_DECISION
```

审查收据冻结验证收据定位/哈希，并把审阅者声明的写入集合与审查计划对账；零写入结论还必须重新读取验证证据包的变更文件快照，对每个受验对象文件的当前字节/状态做验证，因此调用方自报列表或计划列表都不能单独建立独立性。目录决策冻结前两份收据定位/哈希，并再次重验全部前序链中的验证受验对象快照，再冻结当前任务/变更/来源/注册表/策略/依赖的规范定位/哈希；同字节别名也不是当前输入。它还必须重新验证每份前序/依赖收据引用的受信签发者证据包与当前权限来源注册表/来源证明，而不是信任收据内自报的签发者或语义 PASS 字段。任一前序收据修改、版本改变、规范定位漂移、签发者权限来源无法验证、受验对象快照不再当前、必需依赖收据过期或审查不独立时，目录决策为 FAIL。

验证 PASS 只证明检查结果，审查 PASS 只证明独立复核。只有当前输入 `CATALOG_DECISION` 可把目录任务标为 PASS。用户阶段 Gate 批准是另一类显式证据，Gate 不能生成或推断用户批准。

<a id="82-artifact-manifest-hash-dag"></a>

### 8.2 产物清单哈希 DAG

哈希引用边表示“左侧产物包含右侧定位/哈希”，必须形成 DAG：

```text
current receipt
  -> artifact manifest
       -> logs / typed outputs / auxiliary evidence leaves
  -> persisted frozen plan
       -> task packet / completion / source / policy / registry leaves
  -> prior immutable receipts
       -> their earlier manifests and leaves
```

`content_fingerprint` 排除自身；清单不列自己或当前收据；当前收据不嵌入自身哈希；当前收据只指向已存在前序收据；前序收据不反向引用当前收据；`latest` 等别名不参与验收。自引用、同身份不同字节或任何环均为 `FAIL/hash-graph-invalid`。

清单的 `subject:*`、`check:*` 等产物角色身份只在该清单的 `run_id` 内唯一；哈希依赖图使用运行命名空间对账，同一运行的身份/字节冲突仍失败。收据、计划、证据包的结构定义身份继续全局对账。执行器的输入观察记录必须精确包含 `locator`、相等的 `expected_sha256`/`actual_sha256` 与 `status: verified`，它不是引用边；计划已冻结对应输入边。命令流与日志是哈希绑定原始字节，不因内容恰好是 JSON 而递归解析。`.json` 原始/辅助证据可以保留空白字符，但必须严格拒绝重复键和非有限数字，并继续追踪其中的定位/哈希引用；正式收据、清单、计划、证据包与审查/目录证据节点仍要求规范字节。

运行器的之前状态可以用精确的 `{locator, state: absent}` 记录文件在写入前不存在；该记录没有可跟随的字节，因此不是哈希引用边，但承载它的辅助产物仍由父节点绑定。`state: present` 或其他无 `sha256` 的定位结构必须失败即拒绝，防止用状态字段绕过真实产物边。

<a id="9-self-host-bootstrap-与正式-receipt-拓扑"></a>

## 9. 自举引导与正式收据拓扑

<a id="91-bootstrap-provenance-三规则"></a>

### 9.1 引导来源证明的三项规则

1. 原引导产物保持原样；后续证据只保存定位、原始字节哈希与真实观察到的时间；
2. 引导证据永远不能被回填、改写或升级为目录 PASS，也不能伪造历史 READY 或早于控制面建立的 Gate 时间；
3. 每个对应任务必须针对当前输入按正式依赖拓扑重新取得验证、独立审阅与目录决策，才能成为目录 PASS。

普通派发的绝对规则是：每个硬依赖必须在派发前具有精确任务/变更版本、`required_result=PASS` 和当前输入目录收据；合同依赖必须解析到声明生产者/版本的当前不可变产物。唯一例外是一次、边界固定的 Gate 自举引导差异：它只覆盖建立首个控制面所必需的 `QLT-0001` 至 `QLT-0014` 中实际需要引导的运行，只产生 non-READY 来源证明；偏差绑定在原始不可变运行证据中，不另建历史文档。该例外不是 PASS、不满足依赖，也不扩展到业务任务；`QLT-0013` 首次可用后立即关闭，后续不得复用或创建第二个同类例外。

本文不定义引导迁移命令、自动导入或补录实现。

<a id="911-g1-current-input-activation-profile"></a>

### 9.1.1 G1 当前输入激活配置档案

首轮集成复核证明，正式收据拓扑仅有依赖图还不够：闭包中每个任务还必须具备非空 `validation_command`、`allowed_files`、`forbidden_files` 与 `file_claims`，并在注册表中拥有唯一、按目录排序的受验对象。当前 G1 闭包因此使用 [`harness/g1-task-contract-profiles.yaml`](../../harness/g1-task-contract-profiles.yaml) 激活 21 个原文档型任务；原先已有专用实现测试的任务保留专用命令。

配置档案只声明稳定任务、负责人、单一证据文件声明、当前必需输入和封闭的语义断言。命令由 [`scripts/gates/registry_profiles.py`](../../scripts/gates/registry_profiles.py) 从任务 id 派生，调用方不能选择 argv。最终 [`harness/gate-check-registry.yaml`](../../harness/gate-check-registry.yaml) 仍是版本化、哈希绑定的运行输入；`--check` 必须证明它与目录/配置档案的确定性投影完全相同。新增这些执行元数据不改变既有交付物、验收条件、依赖结果或产出的合同，因此保持既有任务/变更版本；若未来改变任何上述业务合同，仍须正常升版并同步全部精确版本固定项。

规划器消费的原始 `task.json` 必须精确遵守 Qoder 运行器实际落盘合同：14 个调用者交接字段、运行器绑定身份、`parent_session_id` 与 `permission_mode` 为必需字段；只允许运行器已定义的非空 `title` 和值为 `true` 的 `_resume_mode` 作为可选字段。运行器使用稳定格式化 JSON 持久化原始任务/完成记录；它们以原始字节哈希绑定并做 duplicate-key/字段语义检查，但不伪装成规范 JSON。证据包、计划与收据自身仍必须规范。`owner`、`discovered_from` 和 `file_claims` 只从规范当前目录取得，并分别与负责人解析、证据包声明和注册表对账；它们不得由调用者塞入原始任务。这样初始/resume 的封闭隔离测试样例与真实运行器产物使用同一形状和序列化，避免测试专用字节掩盖正式激活失败。

文档型检查不能只以文件存在判 `PASS`。[`scripts/gates/task_contracts.py`](../../scripts/gates/task_contracts.py) 对每个必需输入做安全读取与 SHA-256 记录，再执行配置档案中的稳定语义断言。缺少当前输入或配置档案非法为 `FAIL`；语义断言不满足为 `BLOCKED`。`LF-TSK-ARCH-0008` 还要求决策包出现精确 `G1 user decision: APPROVED` 标记；在用户明确批准前，它必须保持 `BLOCKED`。

每个激活任务的变更文件范围是负责人专属的 `tmp/quality/task-evidence/<DOMAIN>/<task-id>/result.json`。这些忽略的文件只承载本次正式验证证明；真实设计/代码输入由冻结计划的 `consumed_inputs` 绑定。这样既不把历史文档伪装成本次写入，也允许当前产物漂移使验证失效。

<a id="92-正式-current-input-拓扑"></a>

### 9.2 正式当前输入拓扑

控制面首轮实现完成后，正式收据必须从 `QLT-0001` 开始按以下 DAG 签发；“并列”表示依赖已满足后可独立验证，不表示突破单 Qoder 运行限制：

精确依赖由 [任务目录](../../planning/workstreams.yaml) 维护，下表保留本设计的验收顺序；不再另画与目录重复的依赖图。

| 任务 | 硬依赖来源 | 合同依赖来源 |
|---|---|---|
| `QLT-0001` | 无 | 无 |
| `QLT-0002` | `QLT-0001` | 无 |
| `QLT-0003` | `QLT-0001` | 无 |
| `QLT-0006` | `QLT-0001` | 无 |
| `QLT-0007` | `QLT-0001`、`QLT-0006` | 无 |
| `QLT-0014` | `QLT-0001`、`QLT-0006` | 无 |
| `QLT-0004` | `QLT-0002` | 无 |
| `QLT-0005` | `QLT-0002` | 无 |
| `QLT-0008` | `QLT-0002`、`QLT-0003` | `QLT-0007`、`QLT-0014` |
| `QLT-0009` | 无 | `QLT-0008` |
| `QLT-0010` | 无 | `QLT-0007`、`QLT-0008`、`QLT-0009`、`QLT-0014` |
| `QLT-0011` | 无 | `QLT-0007`、`QLT-0010`、`QLT-0014` |
| `QLT-0012` | 无 | `QLT-0010`、`QLT-0011` |
| `QLT-0013` | `QLT-0004` | `QLT-0007`、`QLT-0008`、`QLT-0010`、`QLT-0011`、`QLT-0012`、`QLT-0014` |
| `ARCH-0007` | 无 | 无 |
| `ARCH-0008` | `ARCH-0007`、`QLT-0003`、`QLT-0005`、`QLT-0006`、`OPS-0001` | `QLT-0013` |
| `OPS-0001` | 无 | 无 |

`QLT-0002`、`QLT-0003`、`QLT-0006@2/2.0.0` 在 `QLT-0001` PASS 后可并列验证；QLT-0007 和 QLT-0014 分别通过硬依赖消费 QLT-0001/0006，独立产出显式证据包与受信签发者证据包合同。QLT-0004/0005 从 QLT-0002 分支。QLT-0013 不直接消费原始 QLT-0009 检查器结果；它消费 QLT-0010 已封装的验证收据，避免跨层旁路。

`ARCH-0008` 保留现有五个直接硬依赖：`ARCH-0007`、`QLT-0003`、`QLT-0005`、`QLT-0006@2/2.0.0`、`OPS-0001@2/1.1.0`，并新增对 `QLT-0013` 产出的合同的合同边。其他 G1 任务继续通过这五个出口的硬依赖闭包进入 ARCH-0008。G1 证据集成、审查和用户决定仍由 ARCH-0008 拥有；QLT-0013 只产出目录闭包合同，不判断或请求用户批准。

任务及合同版本以当前目录和注册表为准；本页不保留版本迁移流水账。规范产物与签发身份边界见下节，旧收据不得证明当前输入。

正式规划采用此设计时，预期清单为 113 任务、256 边（231 硬依赖、24 合同、1 软依赖）、13 产出的合同，ARCH-0008 闭包为 30 任务/60 边（41 硬依赖、19 合同）。这里是待落目录的一致性目标，不表示规划已修改。

## 10. 方案比较与推荐

<a id="方案-a纯-compiler--run---mode--显式-registry--immutable-chain"></a>

### 方案 A：纯编译器 + `run --mode` + 显式注册表 + 不可变链

`plan` 零副作用；`run --mode` 从参数或受信启动器的单值环境绑定获得两个证据包定位，复用编译器、持久化本次实际计划、向磁盘和调用方刷新 start 事件；TASK_VALIDATION 再执行固定参数序列，审查与目录决策只消费前序收据/证据并追加新收据。它兼容现有命令文本，接口窄且可复现。

<a id="方案-b持久化-plan-service--reusable-plan-id"></a>

### 方案 B：持久化计划服务 + 可复用计划 ID

计划服务引入额外生命周期、过期、并发和权限语义，并混淆内容身份与执行身份，不符合当前窄接口与确定性目标。

<a id="方案-c直接执行-caller-validation-string"></a>

### 方案 C：直接执行调用者验证字符串

实现短，但不能证明 argv 与注册表一致，并允许任意命令文本进入执行路径，不满足安全与审计要求。

推荐方案 A。它保留 `plan/run/status`、`run --mode incremental|full`、显式注册表、三态、不可覆盖收据和当前输入链，并把实现分到明确依赖的 JIT 任务。

## 11. 实现归属与直接证明

任务、估时、版本和精确依赖只由[目录](../../planning/workstreams.yaml)维护；跨文档导航见[任务证据索引](../reviews/g1-task-evidence-map.md)。本表仅解释控制面各组件的结果与拒绝条件，不保存旧任务草案。

| 实现责任 | 单一结果 | 核心验收 |
|---|---|---|
| `LF-TSK-QLT-0007` 显式证据包物化器 | 由主代理显式核对六字段并绑定原始任务/完成记录/输出/差异/测试哈希 | 禁止自由文本推断；证据包字段/哈希/受验对象身份齐全；不建立签发者信任 |
| `LF-TSK-QLT-0014` 受信签发者证据包物化器 | 将 Qoder、Codex、人工或 CI 执行者绑定到可验证来源证明，生成分离签发者证据包 | 调用者自报执行者/角色不能建立信任；证据包授权/身份/哈希齐全；伪造身份 FAIL |
| `LF-TSK-QLT-0008` 规划器 + 注册表 | 纯编译器冻结 QLT-0007/0014 证据包、规范范围/对账/指纹、执行层次与 declared-command/固定参数序列注册表 | `plan` 零写入；TASK_VALIDATION 选择检查，审查/目录零检查；相同输入指纹相同；未知、漂移或种类/层次不匹配 FAIL |
| `LF-TSK-QLT-0009` 检查器结果 | 固定参数序列执行、类型化结果适配器与三态聚合 | 必需跳过/未运行/空非 PASS；进程事实不冒充断言；聚合矩阵通过 |
| `LF-TSK-QLT-0010` TASK_VALIDATION 存储/状态 | 稳定种类处理器/存储、实际计划持久化、预检查磁盘/调用者双刷新、验证收据、执行层护栏与只读状态 | 调用方检查器前收到 run_id/事件定位；任一刷新失败不启动检查器；只有验证路由可调用执行器；种类/层次不匹配 FAIL |
| `LF-TSK-QLT-0011` 独立审阅 | 串行安装唯一 CLI 审查路由，以零-check 证据消费计划发布不可覆盖审查收据 | 生产者自审、受验对象哈希漂移或非空交付检查 FAIL；审查不回写受验对象且不重跑交付命令 |
| `LF-TSK-QLT-0012` 哈希 DAG 核验器 | 只验证计划/收据/清单/prior-receipt 哈希图 | 自环、回边、别名与环测试样例 FAIL；合法 DAG PASS |
| `LF-TSK-QLT-0013` 目录闭包 | 串行安装唯一 CLI 目录路由，以零-check 证据消费计划执行验收注册表、当前输入/依赖/审查对账并发布决定收据 | 不调用交付执行器；核对验收注册表、签发者、新鲜度与完整收据 DAG；只有完整当前链可目录 PASS |

Codex 工作包逐任务投影不新增或合并目录结果：`QLT-0006` 拥有调用者/运行器身份分层和版本化已持久化的投影合同；`QLT-0007` 拥有 `qoder|codex` 通用证据包身份对账；`QLT-0008` 拥有按客户端分支的纯当前目录编译；`QLT-0011` 消费由不同执行者签发的零写入 Codex 审查计划。四项各自保留结果证据，工作包的一个 `run_id` 只表达执行实例，不替代四个任务身份。

哈希核验、目录闭包和 ARCH-0008 G1 集成分属 QLT-0012、QLT-0013 与既有 ARCH-0008，不能合并为跨结果任务。若还需要崩溃恢复、并发锁、符号链接/路径加固、隐私/脱敏或远端证据存储，应另建 JIT 任务，不能塞入 `QLT-0003@1` 或上述原子结果。

<a id="12-review-coverage-checklist非-catalog-acceptance"></a>

## 12. 审查覆盖检查清单（非目录验收）

目录对 `LF-TSK-QLT-0003@4` 只有一个验收条件，覆盖冻结计划、运行/状态/收据以及唯一交付执行层次。以下五项帮助审阅者覆盖该条件，不新增验收条件：

1. CLI 保留 `run --mode incremental|full`；参数或受信启动器的两个单值环境绑定显式提供证据包路径，缺失/冲突上下文负例 FAIL；`plan` 零副作用；运行复用纯编译器、持久化实际计划，并在检查器前向磁盘与调用方双刷新 `START` 事件。
2. 计划/收据 JSON 测试样例覆盖 V1 必需字段；指纹可重算；原始调用者范围、规范化的数组、规范声明与三方对账无冲突；真实规划命令/运行时路径正确。
3. QLT-0006 不被描述为六字段来源；QLT-0007 由主代理显式核对六字段并绑定原始任务/完成记录/输出/差异/测试哈希，禁止从自由文本猜测；QLT-0014 独立验证签发者信任；签发者/执行者/进程身份内部一致。
4. 三态、种类完整性、审查独立性、当前输入链、不可覆盖收据与无环哈希图可从合同推导；引导只有一次 non-READY 差异，绝不补录。
5. 已激活任务的硬性/合同边与当前目录一致；ARCH-0008 独占 G1 集成、审查与用户决定。修改任务版本时，相关消费方与阶段入口/退出绑定同步更新。

文档审阅只能证明当前设计合同满足声明的要求。CLI、测试、真实收据、规划 DAG 变更、各目录任务当前输入 PASS、`LF-TSK-ARCH-0008` G1 审查与用户阶段 1 决定仍需独立证据。

运行时绑定与签发边界见下节；语义版本以目录合同为准，冻结证据包结构仍为 v1。阶段审批按[手册步骤 6](validation/06-phase1-decision.md)执行。


## 工作包规范产物与运行时签发边界

<a id="current-execution-evidence"></a>

工作包结果结构以 [共享策略](../../harness/agent-policy.manifest.yaml) 为准：调用者提供稳定任务/变更版本、范围、验收与失败策略；运行器绑定真实父级、会话、客户端和执行者，生成唯一运行 ID。缺运行时会话不得用随机 UUID 替代；每任务保留独立投影、六字段结果、完成记录与信号，聚合完成记录只索引精确逐任务产物和哈希。

规范发布器只发布显式结构化数据，不运行子进程、不解析日志推断成功、不签发正式收据、不回退旧布局。发布器校验结构与绑定，不认证宿主；宿主核验器才是身份认证边界。

`verify` 复核逐任务键集合、固定定位、当前目录投影、六字段结果、外部证据哈希/字节及聚合状态；PASS 只证明规范产物完整性，不等于正式任务验收。唯一操作命令见[手册步骤 4](validation/04-harness-and-dispatch.md)。

`client` 是工具类型，不是唯一执行者。同客户端的不同实例须具有不同且经宿主认证的执行者/代理、运行/签发者实例，真实协作会话可以共享。独立审查排除生产者和验证签发者，保持零受验对象写入；同执行者/代理/运行/实例、伪造宿主、重放身份和越权签发继续拒绝。

<a id="codex-issuer-调用边界"></a>

`IssuerPacketMaterializer(..., trusted_codex_context=...)` 要求运行器提供精确的 `actor_id`、`session_id`、`parent_session_id`、`client` 四字段映射。从真实运行时绑定派生时，`actor_id` 对应 `identity.agent_id`，其他三字段取实际值；不能直接传入含 `run_id`、`agent_id`、`parent_client` 的完整绑定。`_derive_codex_expected` 比较完整映射，额外字段会触发 `identity-drift`。证明也必须使用同一实际宿主身份，不能用随机会话代替。

源完整性 PASS 不能替代正式 Gate 收据；源作者不能签发自己的任务验证或独立审阅。
