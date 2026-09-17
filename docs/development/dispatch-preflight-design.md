# Phase 1 dispatch preflight design · `LF-TSK-QLT-0005`

> 状态：**设计冻结，尚未实现或验收**。本设计服务于 `LF-TSK-QLT-0005@1 / change 1.0.0` 的后续单次 Qoder 实现；不生成 READY、Gate receipt 或派发许可。任务来源是 [`planning/workstreams.yaml`](../../planning/workstreams.yaml) 的 multi-agent scheduling safety capability，结构模板是 [`planning/task-template.yaml`](../../planning/task-template.yaml)。当前 OpenSpec change 是 [`establish-lexiflow-foundation`](../../openspec/changes/establish-lexiflow-foundation/design.md)。

## 1. Outcome and responsibility

调用者把一个待派发的、已物化的 `lexiflow.task.v1` candidate descriptor、同版本 catalog snapshot 和自己筛选出的 active task descriptors 一次性传入。Preflight 完整判断：Task/version/owner 与 catalog 是否一致；每个可能写入的路径是否归单一 most-specific owner；`allowed_files` 与 `file_claims` 是否相同；candidate 与 active 的写入空间或同一 public contract writer 是否冲突。输出稳定的 `PASS`、`BLOCKED` 或 `FAIL`、逐项诊断和输入 fingerprint。它是**纯输入判定**，不读取进程、Qoder 状态或 runtime task directory，不负责 terminal 过滤、lease、daemon、排队、等待或派发。

`PASS` 只说明这份冻结 snapshot 中没有发现冲突。调用者负责证明 active snapshot 完整、非终态筛选正确、身份真实，并在实际派发前保持该 snapshot 对应的调度窗口有效；窗口失效必须重新提交输入。Preflight 本身不宣称消除检查至派发间的竞态，也不取代 [`AGENTS.md`](../../AGENTS.md) 的“Qoder 同时最多 1 个 run”和 callback-first 300/600 秒约束。

## 2. Alternatives and recommendation

| 方案 | 做法 | 收益 | 代价 |
|---|---|---|---|
| A · 纯 Python 判定器 + 一次性 CLI adapter | 三份冻结输入；有限 path matcher；结构化三态结果；调用者负责 snapshot 与调度 | 易于 fixture、审查和复现；无隐式状态；与当前 Python harness 相邻 | 无法单独保证跨进程原子派发；调用者必须维持调度窗口 |
| B · runner 内嵌进程扫描与文件锁 | 从 Qoder process/task directory 推断 active writers，并在检查后持锁派发 | 单一 Qoder 的本机窗口可更紧 | 混合 QLT-0005 与 QLT-0006 生命周期；Codex writers 仍无法准确发现；状态不明时容易误报 PASS |
| C · 常驻调度服务/数据库 lease | 集中登记活跃 Task、owner、合同和租约 | 将来可覆盖跨主机高并发 | Phase 1 引入服务、过期策略和恢复协议；违反最小闭环，也不能凭空证明未登记的 writers |

**推荐 A。** 参考仓库 [`harness/agent-policy.manifest.yaml`](../../../feipi-session-browser-java/harness/agent-policy.manifest.yaml) 与 [`qoder-subtasks.md`](../../../feipi-session-browser-java/docs/development/qoder-subtasks.md) 要求并行写范围不重叠、Qoder 单 run、未知状态阻断新派发；其 [`repository.file-boundary`](../../../feipi-session-browser-java/scripts/gates/checks/repository/check_file_boundary.py) 用 manifest 单一来源和 fail-closed 路径读取。本项目把这些原则改为可复用的 Task descriptor 判定，不复制参考项目的进程探测或业务 Gate。参考链接均为同一 workspace 的设计来源，不形成运行时依赖。

## 3. Frozen input and output contract

函数输入固定为 `check_dispatch(candidate_view, catalog_snapshot, active_instances)`；三个参数是已经解析的、不可变 JSON-compatible data。CLI 仅将三个本地 JSON 文件解析后调用同一函数，不能另实现一套规则。Input 包含：

1. `candidate_view`：包含完整物化的 `schema_version: lexiflow.task.v1` Task descriptor，以及本次实际 handoff 的 `allowed_files` 和 `forbidden_files` 原始值。Task 必须有 `identity.task_id/task_version/change_version/task_source`、`ownership.primary_owner/contract_owner`、`scope.logical_scopes/allowed_files/file_claims` 和 `produced_contracts`。`scope.allowed_files` 是 Task schema 的列表；当前 runner 只保证 handoff `allowed_files` 是非空字符串，逗号分隔是 **preflight v1 对本次物化 handoff 值新增的 grammar**，不是 runner 已有保证。Task 列表、handoff 字符串和 write claims 规范化后必须完全相同。候选未登记、只有 seed row 或缺少实际 handoff 值时不作猜测。
2. `catalog_snapshot`：由调用者冻结的 `planning/workstreams.yaml` 有效 Task index；对候选和每个 active Task/version 保存 canonical descriptor SHA-256、owner、`allowed_files`、write claims 和 `produced_contracts`；同时保存 `path_ownership.scopes[].proposed_paths`、public contract producer registry、catalog/source hash，以及路径元数据 snapshot。Preflight 只从这份 snapshot 派生 active Task 的 claim 和 contract，拒绝调用方提供可缩窄的替代集合。候选 descriptor/hash/claims/contracts 也必须与 snapshot 精确相等。路径元数据至少声明 repo root 的 canonical identity、已存在 symlink path 集合、casefold collision 集合、扫描完整性和 source hash；缺少该声明或格式非法则 `FAIL/input-incomplete`。Preflight 不读取或验证 QLT-0002 receipt，也不自己遍历 Git 或 filesystem；依赖 PASS 由 catalog/scheduler 在进入本判定前保证。
3. `active_instances`：调用者提供的、在本调度窗口仍可写的全部实例，统一适配为 `lexiflow.dispatch-active-instance.v1`。每项只允许 `task_id`、`task_version`、`change_version`、`task_descriptor_sha256`、本次实际 handoff 的 `allowed_files` 原始值，以及 `instance_id`、`client`、`parent_session_id`；`instance_id` 是调用方为 Codex 或 Qoder 实例提供的稳定唯一 ID，不要求 preflight 理解其运行时来源。Preflight 要求 descriptor hash 与 catalog snapshot 一致，并把 handoff `allowed_files` 按 v1 grammar 规范化后与 snapshot 的 canonical allowed/write-claim 集合三方对账；冲突比较使用 snapshot 的 canonical claims/contracts，不能使用调用方另报的集合。列表外层必须包含 `snapshot_source`、`snapshot_sha256`、`selected_at`、`complete_for_dispatch_window: true` 和 `nonterminal_only: true`。Preflight 只校验这些声明是否存在、格式合法、实例 ID 唯一且 Task/catalog 版本一致，并把声明写入结果；**不证明列表真实完整，也不读取 completion、进程或 Gate status 验证终态真相**。声明缺失为 `FAIL/active-snapshot-declaration-missing`，格式、descriptor hash、handoff claim 或身份不合法为 `FAIL/active-snapshot-invalid`。调用方不得用 preflight PASS 反推未提交的 writer 不存在。

输入中不得放 token、真实字幕、用户历史、Vocabulary Profile 或模型 payload。Result 只保留 Task/instance ID、规范化 claim、owner、contract name、reason code、matcher version 和三份输入的 SHA-256 fingerprint；不复制完整 descriptor 或个人数据。Diagnostic 按路径/contract/active identity 排序，确保同输入同结果。CLI exit code 仅映射三态，不作为 Gate receipt：`0=PASS`、`1=BLOCKED`、`2=FAIL`，stdout 为单个 JSON result。

## 4. Path language and normalization

Matcher version `dispatch-path-v1` 把路径解释为由 `/` 分隔的非空 segment 序列，并只接受 repo-relative POSIX path 的三个有限形态：

- `a/b/file.ext` 是 exact language，只包含精确序列 `[a,b,file.ext]`。
- `a/b/*` 是 one-child language，只包含 `[a,b,x]`，其中 `x` 恰好是一个非空普通 segment；它不包含 `a/b`，也不包含 `[a,b,x,...]`。
- `a/b/**` 是 descendant language，只包含 `[a,b,x,...]`，其中至少有一个后代 segment；它不包含 `a/b` 自身。

`*` 和 `**` 只能占整个末尾 path segment；不接受 `?`、字符类、brace、negation、globstar 位于中间、shell expansion 或特殊的“其他所有文件”文本。Handoff 字符串先按字面逗号分隔并 trim 每项；空项、重复项、重叠的冗余项、无法与列表精确对齐都 `FAIL/path-expression-invalid`。路径自身含逗号也拒绝；scope 列表每项仍须通过同一解析器。`forbidden_files` 保留 runner 的协作声明，但此纯 preflight 不把它当成缩小写 claim 的规则；allowed 与 forbidden 明显交叉时 `BLOCKED/forbidden-overlap`，含未知自由文本时由调用者/Gate 做后验审查，不能用它证明并行安全。

解析拒绝空路径、绝对路径（POSIX/drive/UNC）、反斜线、`..`、`.`、重复或尾随 `/`、NUL/control、非 NFC Unicode；只将逗号外层空白 trim，不悄悄改写路径内部字符。Repo root canonical identity 必须与 catalog snapshot 的 root 一致；不得使用 `Path.resolve()` 将可疑路径带出仓库再比较。任何 claim 的 literal prefix 命中 snapshot 的 symlink 本体或祖先，或者其 wildcard 空间可能覆盖已登记 symlink，均 `FAIL/path-symlink-unsafe`。缺少完整 symlink inventory 是 `FAIL`。规范化的 casefold alias、路径 index 中已有的大小写冲突，或 owner pattern 仅靠大小写区分，均 `FAIL/path-case-ambiguous`；matcher 不依赖 macOS/Linux 的 filesystem 大小写行为。新建路径在 snapshot 之后出现的 symlink/case alias 由调用者重建 snapshot；旧 `PASS` 不可复用。

有限形态使包含/相交可以**符号计算**，不靠当前存在的文件列表。判断的是上述 path language 是否有共同成员；exact path 若同时也是 descendant language 的成员便相交，不把“目录可能被递归写入”作为额外隐含语义。固定真值如下：

| 左侧 | 右侧 | 相交 | 包含关系 |
|---|---|---:|---|
| `dir/x` | `dir/*` | 是 | `dir/*` 包含 `dir/x` |
| `dir/x` | `dir/**` | 是 | `dir/**` 包含 `dir/x` |
| `dir/x` | `dir/x/**` | 否 | 均不包含对方 |
| `dir/*` | `dir/x/**` | 否 | 均不包含对方 |
| `dir/*` | `dir/**` | 是 | `dir/**` 包含 `dir/*` |
| `dir/x/*` | `dir/**` | 是 | `dir/**` 包含 `dir/x/*` |
| `dir/x/**` | `dir/**` | 是 | `dir/**` 包含 `dir/x/**` |
| `foo/**` | `foobar/**` | 否 | 均不包含对方 |

owner-crossing 使用同一交集/包含算法；不得因为某个 path 当前不存在而改变真值。任何 matcher 无法证明包含或不相交的表达式都 `FAIL/path-expression-unsupported`，不可近似成“安全”。

## 5. Owner, claim and public-contract rules

[`planning/workstreams.yaml`](../../planning/workstreams.yaml) 当前规定 logical scope 由 **most-specific** owner 决定，`proposed_paths` 在 G1 绑定 physical paths 前已经执行 claim 约束。Preflight 对每个 write claim 的**全部可能路径**求 owner：按最长 literal path prefix 优先，再按 exact > direct-child > subtree 排序；同优先级落到不同 owner 是 `FAIL/catalog-owner-ambiguous`，无 owner 是 `BLOCKED/path-unowned`。若宽 claim 触及更深层的不同 owner（如 Content 路径包含 ADP 子树，或 `ops/**` 包含 OBS 子树），整项 `BLOCKED/owner-crossing`；不能因为候选文件目前未存在就放行。`scope.logical_scopes`、`ownership.primary_owner`、每个 `file_claims[].owner` 和 catalog Task owner 必须对齐，声明的 `contract_owner` 必须是 catalog 中的 owner；候选版本或 catalog Task identity 不符是 `FAIL/task-version-mismatch`，明确越 owner 的 claim 是 `BLOCKED/owner-mismatch`。

当前 preflight 面向修改型 Task，`scope.file_claims` 只接受 `mode: write`。规范化的 `scope.allowed_files` 集合与 write claim path 集合必须**逐项相同**，所有 claim 由同一 `primary_owner` 拥有；缺少 claim、额外 claim、read/write mode 混用、仅靠 `forbidden_files` 缩小宽 claim，都 `BLOCKED/claim-mismatch`。不做推测性文件枚举。Read-only research 不作为 writer candidate，调用者仍负责它与正在改动的接口/输入的协调。

每个 normalized active instance 先将 descriptor hash 和实际 handoff allowed 值与 catalog snapshot 对账，再对 snapshot 中的 canonical path/owner/claim 执行同一校验；无效 active 输入是 `FAIL/active-snapshot-invalid`，不可忽略后只比较剩余实例。Candidate 的任一 write claim 与 active 的任一 canonical write claim 符号相交时 `BLOCKED/write-overlap`，结果保留两端 claim 与 active identity。Candidate 与 active 的 public contract 都从已对账的 catalog canonical `produced_contracts` 取得；不同 Task 同时声明写同一 contract name时，**无论 version 或路径是否相交**，均 `BLOCKED/contract-writer-conflict`，符合 catalog 的 `public_contract_changes_are_serialized`。写 `contracts/**` 或已登记 public contract path 的 Task 缺少相应 `produced_contracts` 声明时 `FAIL/contract-writer-unknown`。Preflight 不判断依赖是否 PASS；QLT-0002 与 Gate 负责依赖/evidence。

## 6. Three-state decision and interfaces

| 结果 | 触发条件 | 是否可以据此继续 |
|---|---|---|
| `PASS` | 三份输入声明完整、可解析、身份/owner/claim 一致，符号相交和 contract writer 检查均已执行且无冲突 | 只对调用方显式提交的 active 列表成立；调用者可在**同一有效调度窗口**进行其余 Qoder/agent 规则检查；不是 Task/Gate PASS，也不证明列表真实完整 |
| `BLOCKED` | 检查完整后确认 Task 声明或当前 active writers 违反 owner、claim、path/contract 独占规则 | 修改 descriptor、拆 Task 或等待真实终态后提交新 snapshot；不能静默缩窄写范围 |
| `FAIL` | 任何输入缺失、非法、版本不符、path/symlink/case 不可证明、active 声明缺失或格式非法、matcher 无法证明结论 | 不得派发；修复输入/调用者声明后重新运行 |

汇总严格 `FAIL > BLOCKED > PASS`。所有检查的诊断应保留，不能遇到第一处 conflict 就丢掉其他已可判定冲突；但输入坏到无法安全解析时直接 `FAIL` 且不处理不可信内容。空 candidate claim、缺少 active snapshot 元数据、未知状态、未运行检查、CLI exit 0 或 Qoder completion 都不能解释为验收通过。

实现的最小文件分解：

| 文件 | 边界 |
|---|---|
| `scripts/gates/dispatch_preflight.py` | `check_dispatch` 与有限 path matcher；只消费三份输入并返回 typed result；一个薄 `check` CLI adapter 读三个 JSON 文件；不调用 `scripts/gates/cli.py` 的 `plan/run/status`、不生成 receipt |
| `tests/gates/test_dispatch_preflight.py` | 解析、owner、claim、write/contract conflict、三态与 CLI fixtures；所有输入均为合成路径/Task，不使用真实用户内容 |

若文件职责变大，再在同一个 QLT-0005 scope 内把 matcher 拆到 `scripts/gates/dispatch_paths.py` 并增加对应测试；不先建通用 scheduling framework。CLI 是派发诊断入口，Gate receipt 的唯一公开入口仍是 [`scripts/gates/cli.py`](gate-control-plane-design.md) 中的 `plan/run/status`。将 preflight 嵌入 runner 或 Gate 必须另建明确 dependency/版本任务；不能因为两个脚本同目录就形成隐藏调用。

分工固定：QLT-0002 验证 catalog schema、ID、owner registry、typed DAG 和 producer uniqueness，QLT-0005 只比较**本次** candidate 与 active claim；QLT-0003 只冻结 Gate 设计合同；QLT-0007 与 QLT-0014 分别物化显式 result evidence 和可信 issuer；QLT-0008/0009 负责编译 plan 与执行 fixed-argv checks；QLT-0010/0011/0013 串行建立 validation、review 与 catalog-decision receipt route；QLT-0012 只读验证 evidence hash DAG；QLT-0006 runner 绑定 run identity、保证单 Qoder run、写 completion 后 callback、处理 300/600 秒 watchdog。Main Agent 负责实际 diff/Gate 独立验收。不能把任何一层的 `queued`、`ack`、exit 0 当成另一层的 `PASS`。

## 7. Test matrix and acceptance

| Fixture 组 | 必测输入与结果 |
|---|---|
| Identity/snapshot | 正确 Task/catalog/normalized active instance；调用方少报 claim/contract 时仍从 catalog canonical 集合检测冲突；descriptor hash/handoff allowed 不一致、重复 instance ID、缺完整性/非终态声明、声明格式非法、Task/version/owner drift → `FAIL`；不测试或声称 runtime 真相 |
| Path grammar/safety | exact、one-child、descendant 和上方八组固定真值、逗号列表合法；空项、mid-star、absolute、`..`、backslash、symlink prefix/wildcard、casefold alias → `FAIL` |
| Owner/claims | most-specific nested owner 正例；宽 claim 穿越 owner、无 owner、allowed/file_claim 不同、forbidden 交叉 → `BLOCKED`；同优先级 owner 歧义 → `FAIL` |
| Scheduling | disjoint writers → `PASS`；exact/subtree、direct/subtree、segment boundary 的 overlap 真/假例；相交 write 或同名不同版本 public contract producer → `BLOCKED` |
| Pure interface | 相同三份输入结果 fingerprint/diagnostics 稳定；CLI 只读且无文件写、进程探测、Qoder status、lease、sleep；malformed JSON → `FAIL` |

QLT-0005 交付给 Main Agent 的验收不超过五项：

1. 三份冻结输入的身份、Task/change 版本、catalog owner 与 active provenance/筛选声明均校验并记录；声明缺失或格式非法为 `FAIL`，结果不宣称验证 runtime 真相。
2. `allowed_files` 有限 grammar 与路径安全负例全部 fail closed；most-specific owner 和 allowed/write claim 对账均有可复现 fixtures。
3. Candidate-active write overlap 与同名 public contract writer 冲突均 `BLOCKED`，不相交的合成 Task `PASS`。
4. `FAIL > BLOCKED > PASS`、结果 fingerprint、稳定 reason code、CLI exit mapping 由定向测试证明；纯判定器不读 runtime/process 状态或写 receipt。
5. Main Agent 独立核对 Qoder diff 仅在批准的 `scripts/gates/**`、`tests/gates/**`，运行定向测试和规定的 `python3 scripts/gates/cli.py run --mode incremental`；CLI/Gate 尚不存在或检查未运行时不得记 `PASS`。

本设计自身没有运行产品或 Gate。QLT-0005 的 catalog dependency 是 QLT-0002@1/1.0.0 `PASS`；在其 current-input catalog receipt 及已激活控制面链落地前，文档不是派发许可。若后续变更范围、输入格式或验收，先更新 catalog Task/change version 和 handoff，再派发新 run。
