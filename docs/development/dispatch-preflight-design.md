<a id="phase-1-dispatch-preflight-design--lf-tsk-qlt-0005"></a>

# 阶段 1 派发预检设计 · `LF-TSK-QLT-0005`

> 本页定义派发预检合同；实现与验收状态见[第一阶段状态页](../roadmap/phase-1-status.md)，不以设计正文签发派发许可。

<a id="1-outcome-and-responsibility"></a>

## 1. 结果与职责

调用者把一个待派发的、已物化的 `lexiflow.task.v1` 候选描述符、同版本目录快照和自己筛选出的活动任务描述符一次性传入。预检完整判断：任务/版本/负责人与目录是否一致；每个可能写入的路径是否归单一最具体的负责人；`allowed_files` 与 `file_claims` 是否相同；候选与活动的写入空间或同一公开合同写入者是否冲突。输出稳定的 `PASS`、`BLOCKED` 或 `FAIL`、逐项诊断和输入指纹。它是**纯输入判定**，不读取进程、Qoder 状态或运行时任务目录，不负责终态过滤、租约、守护进程、排队、等待或派发。

`PASS` 只说明这份冻结快照中没有发现冲突。调用者负责证明活动快照完整、非终态筛选正确、身份真实，并在实际派发前保持该快照对应的调度窗口有效；窗口失效必须重新提交输入。预检本身不宣称消除检查至派发间的竞态，也不取代 [`AGENTS.md`](../../AGENTS.md) 的“Qoder 同时最多 1 个运行”和共享回调优先与兜底约束。

<a id="2-alternatives-and-recommendation"></a>

## 2. 替代方案与建议

| 方案 | 做法 | 收益 | 代价 |
|---|---|---|---|
| A · 纯 Python 判定器 + 一次性 CLI 适配器 | 三份冻结输入；有限路径匹配器；结构化三态结果；调用者负责快照与调度 | 易于测试样例、审查和复现；无隐式状态；与当前 Python harness 相邻 | 无法单独保证跨进程原子派发；调用者必须维持调度窗口 |
| B · 运行器内嵌进程扫描与文件锁 | 从 Qoder 进程/任务目录推断活动写入者，并在检查后持锁派发 | 单一 Qoder 的本机窗口可更紧 | 混合 QLT-0005 与 QLT-0006 生命周期；Codex 写入者仍无法准确发现；状态不明时容易误报 PASS |
| C · 常驻调度服务/数据库租约 | 集中登记活跃任务、负责人、合同和租约 | 将来可覆盖跨主机高并发 | 阶段 1 引入服务、过期策略和恢复协议；违反最小闭环，也不能凭空证明未登记的写入者 |

**推荐 A。** 参考仓库 harness/agent-policy.manifest.yaml（本地定位 `../../../feipi-session-browser-java/harness/agent-policy.manifest.yaml`，不随仓库提供） 与 qoder-subtasks.md（本地定位 `../../../feipi-session-browser-java/docs/development/qoder-subtasks.md`，不随仓库提供） 要求并行写范围不重叠、Qoder 单运行、未知状态阻断新派发；其 repository.file-boundary（本地定位 `../../../feipi-session-browser-java/scripts/gates/checks/repository/check_file_boundary.py`，不随仓库提供） 用清单单一来源和失败即拒绝路径读取。本项目把这些原则改为可复用的任务描述符判定，不复制参考项目的进程探测或业务 Gate。参考链接均为同一工作区的设计来源，不形成运行时依赖。

<a id="3-frozen-input-and-output-contract"></a>

## 3. 冻结的输入输出合同

函数输入固定为 `check_dispatch(candidate_view, catalog_snapshot, active_instances)`；三个参数是已经解析的、不可变兼容 JSON 的数据。CLI 仅将三个本地 JSON 文件解析后调用同一函数，不能另实现一套规则。输入包含：

1. `candidate_view`：包含完整物化的 `schema_version: lexiflow.task.v1` 任务描述符，以及本次实际交接的 `allowed_files` 和 `forbidden_files` 原始值。任务必须有 `identity.task_id/task_version/change_version/task_source`、`ownership.primary_owner/contract_owner`、`scope.logical_scopes/allowed_files/file_claims` 和 `produced_contracts`。`scope.allowed_files` 是任务结构定义的列表；当前运行器只保证交接 `allowed_files` 是非空字符串，逗号分隔是 **预检 v1 对本次物化交接值新增的语法**，不是运行器已有保证。任务列表、交接字符串和写入声明规范化后必须完全相同。候选未登记、只有种子行或缺少实际交接值时不作猜测。
2. `catalog_snapshot`：由调用者冻结的 `planning/workstreams.yaml` 有效任务索引；对候选和每个活动任务/版本保存规范描述符 SHA-256、负责人、`allowed_files`、写入声明和 `produced_contracts`；同时保存 `path_ownership.scopes[].proposed_paths`、公开合同生产者注册表、目录/来源哈希，以及路径元数据快照。预检只从这份快照派生活动任务的声明和合同，拒绝调用方提供可缩窄的替代集合。候选描述符/哈希/声明/合同也必须与快照精确相等。路径元数据至少声明仓库根的规范身份、已存在符号链接路径集合、casefold 碰撞集合、扫描完整性和来源哈希；缺少该声明或格式非法则 `FAIL/input-incomplete`。预检不读取或验证 QLT-0002 收据，也不自己遍历 Git 或文件系统；依赖 PASS 由目录/scheduler 在进入本判定前保证。
3. `active_instances`：调用者提供的、在本调度窗口仍可写的全部实例，统一适配为 `lexiflow.dispatch-active-instance.v1`。每项只允许 `task_id`、`task_version`、`change_version`、`task_descriptor_sha256`、本次实际交接的 `allowed_files` 原始值，以及 `instance_id`、`client`、`parent_session_id`；`instance_id` 是调用方为 Codex 或 Qoder 实例提供的稳定唯一 ID，不要求预检理解其运行时来源。预检要求描述符哈希与目录快照一致，并把交接 `allowed_files` 按 v1 语法规范化后与快照的规范允许范围/write-claim 集合三方对账；冲突比较使用快照的规范声明/合同，不能使用调用方另报的集合。列表外层必须包含 `snapshot_source`、`snapshot_sha256`、`selected_at`、`complete_for_dispatch_window: true` 和 `nonterminal_only: true`。预检只校验这些声明是否存在、格式合法、实例 ID 唯一且任务/目录版本一致，并把声明写入结果；**不证明列表真实完整，也不读取完成记录、进程或 Gate 状态验证终态真相**。声明缺失为 `FAIL/active-snapshot-declaration-missing`，格式、描述符哈希、交接声明或身份不合法为 `FAIL/active-snapshot-invalid`。调用方不得用预检 PASS 反推未提交的写入者不存在。

输入中不得放令牌、真实字幕、用户历史、个人词汇档案或模型载荷。结果只保留任务/实例 ID、规范化声明、负责人、合同名称、原因代码、匹配器版本和三份输入的 SHA-256 指纹；不复制完整描述符或个人数据。诊断按路径/合同/活动身份排序，确保同输入同结果。CLI 退出码仅映射三态，不作为 Gate 收据：`0=PASS`、`1=BLOCKED`、`2=FAIL`，stdout 为单个 JSON 结果。

<a id="4-path-language-and-normalization"></a>

## 4. 路径语法与规范化

匹配器版本 `dispatch-path-v1` 把路径解释为由 `/` 分隔的非空片段序列，并只接受仓库相对 POSIX 路径的三个有限形态：

- `a/b/file.ext` 是精确语法体系，只包含精确序列 `[a,b,file.ext]`。
- `a/b/*` 是单子级语法体系，只包含 `[a,b,x]`，其中 `x` 恰好是一个非空普通片段；它不包含 `a/b`，也不包含 `[a,b,x,...]`。
- `a/b/**` 是后代语法体系，只包含 `[a,b,x,...]`，其中至少有一个后代片段；它不包含 `a/b` 自身。

`*` 和 `**` 只能占整个末尾路径片段；不接受 `?`、字符类、花括号展开、否定匹配、递归通配符位于中间、shell 展开或特殊的“其他所有文件”文本。交接字符串先按字面逗号分隔并去除首尾空白每项；空项、重复项、重叠的冗余项、无法与列表精确对齐都 `FAIL/path-expression-invalid`。路径自身含逗号也拒绝；范围列表每项仍须通过同一解析器。`forbidden_files` 保留运行器的协作声明，但此纯预检不把它当成缩小写声明的规则；允许与禁止明显交叉时 `BLOCKED/forbidden-overlap`，含未知自由文本时由调用者/Gate 做后验审查，不能用它证明并行安全。

解析拒绝空路径、绝对路径（POSIX/drive/UNC）、反斜线、`..`、`.`、重复或尾随 `/`、NUL/控制、非 NFC Unicode；只将逗号外层空白去除首尾空白，不悄悄改写路径内部字符。仓库根规范身份必须与目录快照的根一致；不得使用 `Path.resolve()` 将可疑路径带出仓库再比较。任何声明的字面量前缀命中快照的符号链接本体或祖先，或者其通配符空间可能覆盖已登记符号链接，均 `FAIL/path-symlink-unsafe`。缺少完整符号链接清单是 `FAIL`。规范化的 casefold 别名、路径索引中已有的大小写冲突，或负责人模式仅靠大小写区分，均 `FAIL/path-case-ambiguous`；匹配器不依赖 macOS/Linux 的文件系统大小写行为。新建路径在快照之后出现的符号链接/案例别名由调用者重建快照；旧 `PASS` 不可复用。

有限形态使包含/相交可以**符号计算**，不靠当前存在的文件列表。判断的是上述路径语法体系是否有共同成员；精确路径若同时也是后代语法体系的成员便相交，不把“目录可能被递归写入”作为额外隐含语义。固定真值如下：

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

跨负责人使用同一交集/包含算法；不得因为某个路径当前不存在而改变真值。任何匹配器无法证明包含或不相交的表达式都 `FAIL/path-expression-unsupported`，不可近似成“安全”。

<a id="5-owner-claim-and-public-contract-rules"></a>

## 5. 负责人, 声明与公开合同规则

[`planning/workstreams.yaml`](../../planning/workstreams.yaml) 当前规定逻辑范围由 **最具体的** 负责人决定，`proposed_paths` 在 G1 绑定物理路径前已经执行声明约束。预检对每个写入声明的**全部可能路径**求负责人：按最长字面量路径前缀优先，再按精确 > 直接子级 > 子树排序；同优先级落到不同负责人是 `FAIL/catalog-owner-ambiguous`，无负责人是 `BLOCKED/path-unowned`。若宽声明触及更深层的不同负责人（如内容路径包含 ADP 子树，或 `ops/**` 包含 OBS 子树），整项 `BLOCKED/owner-crossing`；不能因为候选文件目前未存在就放行。`scope.logical_scopes`、`ownership.primary_owner`、每个 `file_claims[].owner` 和目录任务负责人必须对齐，声明的 `contract_owner` 必须是目录中的负责人；候选版本或目录任务身份不符是 `FAIL/task-version-mismatch`，明确越负责人的声明是 `BLOCKED/owner-mismatch`。

当前预检面向修改型任务，`scope.file_claims` 只接受 `mode: write`。规范化的 `scope.allowed_files` 集合与写入声明路径集合必须**逐项相同**，所有声明由同一 `primary_owner` 拥有；缺少声明、额外声明、读取/写入模式混用、仅靠 `forbidden_files` 缩小宽声明，都 `BLOCKED/claim-mismatch`。不做推测性文件枚举。只读研究不作为写入者候选，调用者仍负责它与正在改动的接口/输入的协调。

每个规范化的活动实例先将描述符哈希和实际交接允许值与目录快照对账，再对快照中的规范路径/负责人/声明执行同一校验；无效活动输入是 `FAIL/active-snapshot-invalid`，不可忽略后只比较剩余实例。候选的任一写入声明与活动的任一规范写入声明符号相交时 `BLOCKED/write-overlap`，结果保留两端声明与活动身份。候选与活动的公开合同都从已对账的目录规范 `produced_contracts` 取得；不同任务同时声明写同一合同名称时，**无论版本或路径是否相交**，均 `BLOCKED/contract-writer-conflict`，符合目录的 `public_contract_changes_are_serialized`。写 `contracts/**` 或已登记公开合同路径的任务缺少相应 `produced_contracts` 声明时 `FAIL/contract-writer-unknown`。预检不判断依赖是否 PASS；QLT-0002 与 Gate 负责依赖/证据。

<a id="6-three-state-decision-and-interfaces"></a>

## 6. 三态判定与接口

| 结果 | 触发条件 | 是否可以据此继续 |
|---|---|---|
| `PASS` | 三份输入声明完整、可解析、身份/负责人/声明一致，符号相交和合同写入者检查均已执行且无冲突 | 只对调用方显式提交的活动列表成立；调用者可在**同一有效调度窗口**进行其余 Qoder/代理规则检查；不是任务/Gate PASS，也不证明列表真实完整 |
| `BLOCKED` | 检查完整后确认任务声明或当前活动写入者违反负责人、声明、路径/合同独占规则 | 修改描述符、拆任务或等待真实终态后提交新快照；不能静默缩窄写范围 |
| `FAIL` | 任何输入缺失、非法、版本不符、路径/符号链接/案例不可证明、活动声明缺失或格式非法、匹配器无法证明结论 | 不得派发；修复输入/调用者声明后重新运行 |

汇总严格 `FAIL > BLOCKED > PASS`。所有检查的诊断应保留，不能遇到第一处冲突就丢掉其他已可判定冲突；但输入坏到无法安全解析时直接 `FAIL` 且不处理不可信内容。空候选声明、缺少活动快照元数据、未知状态、未运行检查、CLI 零退出码或 Qoder 完成记录都不能解释为验收通过。

实现的最小文件分解：

| 文件 | 边界 |
|---|---|
| `scripts/gates/dispatch_preflight.py` | `check_dispatch` 与有限路径匹配器；只消费三份输入并返回类型化结果；一个薄 `check` CLI 适配器读三个 JSON 文件；不调用 `scripts/gates/cli.py` 的 `plan/run/status`、不生成收据 |
| `tests/gates/test_dispatch_preflight.py` | 解析、负责人、声明、写入/合同冲突、三态与 CLI 测试样例；所有输入均为合成路径/任务，不使用真实用户内容 |

若文件职责变大，再在同一个 QLT-0005 范围内把匹配器拆到 `scripts/gates/dispatch_paths.py` 并增加对应测试；不先建通用调度框架。CLI 是派发诊断入口，Gate 收据的唯一公开入口仍是 [`scripts/gates/cli.py`](gate-control-plane-design.md) 中的 `plan/run/status`。将预检嵌入运行器或 Gate 必须另建明确依赖/版本任务；不能因为两个脚本同目录就形成隐藏调用。

分工固定：QLT-0002 验证目录结构定义、ID、负责人注册表、类型化 DAG 和生产者唯一性，QLT-0005 只比较**本次** 候选与活动声明；QLT-0003 只冻结 Gate 设计合同；QLT-0007 与 QLT-0014 分别物化显式结果证据和可信签发者；QLT-0008/0009 负责编译计划与执行固定参数序列检查；QLT-0010/0011/0013 串行建立验证、审查与目录决定收据路由；QLT-0012 只读验证证据哈希 DAG；QLT-0006 运行器绑定运行身份、保证单 Qoder 运行、写完成记录后回调、处理 按共享策略执行的守护检查器。主代理负责实际差异/Gate 独立验收。不能把任何一层的 `queued`、`ack`、零退出码当成另一层的 `PASS`。

<a id="7-test-matrix-and-acceptance"></a>

## 7. 测试矩阵与验收

| 测试样例组 | 必测输入与结果 |
|---|---|
| 身份/快照 | 正确任务/目录/规范化的活动实例；调用方少报声明/合同时仍从目录规范集合检测冲突；描述符哈希/交接允许不一致、重复实例 ID、缺完整性/非终态声明、声明格式非法、任务/版本/负责人漂移 → `FAIL`；不测试或声称运行时真相 |
| 路径语法/安全性 | 精确、单子级、后代和上方八组固定真值、逗号列表合法；空项、中间通配符、绝对路径、`..`、反斜线、符号链接前缀/通配符、casefold 别名 → `FAIL` |
| 负责人/声明 | 最具体的嵌套负责人正例；宽声明穿越负责人、无负责人、允许范围/文件_claim 不同、禁止交叉 → `BLOCKED`；同优先级负责人歧义 → `FAIL` |
| 调度 | 不相交写入者 → `PASS`；精确/子树、直接/子树、片段边界的重叠真/假例；相交写入或同名不同版本公开合同生产者 → `BLOCKED` |
| 纯接口 | 相同三份输入结果指纹/诊断稳定；CLI 只读且无文件写、进程探测、Qoder 状态、租约、sleep；格式错误 JSON → `FAIL` |

QLT-0005 交付给主代理的验收不超过五项：

1. 三份冻结输入的身份、任务/变更版本、目录负责人与活动来源证明/筛选声明均校验并记录；声明缺失或格式非法为 `FAIL`，结果不宣称验证运行时真相。
2. `allowed_files` 有限语法与路径安全负例全部失败即拒绝；最具体的负责人和允许范围/写入声明对账均有可复现测试样例。
3. 候选与活动实例写入重叠与同名公开合同写入者冲突均 `BLOCKED`，不相交的合成任务 `PASS`。
4. `FAIL > BLOCKED > PASS`、结果指纹、稳定原因代码、CLI 退出映射由定向测试证明；纯判定器不读运行时/进程状态或写收据。
5. 主代理独立核对 Qoder 差异仅在批准的 `scripts/gates/**`、`tests/gates/**`，运行定向测试和规定的 `python3 scripts/gates/cli.py run --mode incremental`；CLI/Gate 尚不存在或检查未运行时不得记 `PASS`。

本设计自身没有运行产品或 Gate。QLT-0005 的目录依赖是 QLT-0002@1/1.0.0 `PASS`；在其当前输入目录收据及已激活控制面链落地前，文档不是派发许可。若后续变更范围、输入格式或验收，先更新目录任务/变更版本和交接，再派发新运行。
