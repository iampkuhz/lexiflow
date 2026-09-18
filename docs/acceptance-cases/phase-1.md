<a id="phase-1-acceptance-cases"></a>

# 第一阶段验收案例

<a id="acceptance-case-registry"></a>

验收案例是产品要求、实现任务与可执行测试之间的稳定连接。ID 一经使用不复用；废弃时保留记录并标记 `retired`。

每条案例至少包含：

- `Case id`：新用例默认 `LF-AC-<DOMAIN>-<NNN>`；已分配的 `LF-<DOMAIN>-<NNN>` 与 `ARCH/FLOW/LEARN/SOURCE/TENANT/FAIL/OBS-<NNN>` 保持稳定，不复用；
- `Priority`：P0/P1/P2；
- `Requirement source`；
- `Given / When / Then`；
- `Evidence owner` 与未来测试位置；
- `Status`：proposed/已接受/implemented/经核验的/retired。

OpenSpec 描述长期行为，案例描述可观察验收，测试负责产生证据。仅存在映射不代表测试已经运行。

<a id="lf-caption-001--p0--english-first"></a>

## LF-CAPTION-001 · P0 · 英文优先

- **来源:** 产品架构 / 英文字幕不等待语义服务
- **前提** 客户端收到当前英文字幕
- **当** 后端、Redis 或语义供应商超时
- **则** 英文在客户端本地时延预算内显示，且没有整句中文替换
- **证据负责人:** Chrome 扩展集成测试（阶段 5）
- **状态:** accepted-design

<a id="lf-enrich-001--p0--rule-and-model-separation"></a>

## LF-ENRICH-001 · P0 · 规则与模型分离

- **来源:** 产品架构 / 规则与模型分离
- **前提** 一个用户已高熟悉度的候选词段
- **当** 提示编排执行提示需求判断
- **则** 候选可在不调用语义供应商的情况下被抑制
- **证据负责人:** 提示编排架构/单元测试（阶段 4）
- **状态:** accepted-design

<a id="lf-profile-001--p0--profile-source-of-truth"></a>

## LF-PROFILE-001 · P0 · 个人档案事实来源

- **来源:** 产品架构 / 模块化单体
- **前提** 同一用户在两个浏览器实例观看内容
- **当** 一个实例明确标记词项为 known
- **则** 服务端个人档案版本前进，另一实例按同步合同看到新版本
- **证据负责人:** 后端/扩展合同测试（阶段 3/6）
- **状态:** accepted-design

<a id="lf-learning-001--p0--event-idempotency"></a>

## LF-LEARNING-001 · P0 · 事件幂等

- **来源:** 产品架构 / 学习行为可重放且幂等
- **前提** 多设备重复上传同一个事件身份
- **当** 学习归约投影处理这些请求
- **则** 只形成一份有效学习证据并只更新一次个人档案
- **证据负责人:** 学习归约持久化/集成测试（阶段 2/6）
- **状态:** accepted-design

<a id="lf-adapter-001--p1--source-independence"></a>

## LF-ADAPTER-001 · P1 · 来源独立

- **来源:** 产品架构 / YouTube 是内容适配器
- **前提** 新的内容适配器能产生规范化片段与上下文
- **当** 它接入组合根
- **则** 个人词汇、学习归约与提示编排核心不需要依赖来源平台代码
- **证据负责人:** 模块边界测试（阶段 3）
- **状态:** accepted-design

<a id="lf-agent-001--p0--non-busy-wait-delegation"></a>

## LF-AGENT-001 · P0 · 无忙等委派

- **来源:** 代理执行 / 主代理无忙等轮询
- **前提** Qoder 运行已启动
- **当** 任务仍在执行且没有完成记录
- **则** 主 LLM 不循环查询；后台兜底首次不早于 300 秒，后续间隔不短于 600 秒
- **证据负责人:** Harness 生命周期测试（阶段 1）
- **状态:** implemented-pending-gate

<a id="lf-enrich-002--p0--pending-requires-durable-handoff"></a>

## LF-ENRICH-002 · P0 · 待处理状态必须有持久交接

- **来源:** 产品架构 / 待处理工作有持久证据
- **前提** 快速通道可返回确定性提示注释
- **当** 语义交接提交失败
- **则** 响应不能宣称语义任务待处理，英文和快速结果仍可使用
- **证据负责人:** 提示编排/应用失败测试（阶段 4）
- **状态:** accepted-design

<a id="lf-learning-002--p0--display-is-an-observed-fact"></a>

## LF-LEARNING-002 · P0 · 显示是实际观察到的事实

- **来源:** 产品架构 / 生成、投递、展示与点击分离
- **前提** 慢速提示注释已生成但客户端切换字幕，没有实际渲染
- **当** 客户端消费晚到结果
- **则** 不上传 `HintDisplayed`，学习归约不据此增加展示证据
- **证据负责人:** 扩展/学习归约旅程测试（阶段 5/6）
- **状态:** accepted-design

<a id="lf-gate-evidence-001--p0--explicit-result-evidence"></a>

## LF-GATE-EVIDENCE-001 · P0 · 显式结果证据

- **来源:** 验收控制面 / `LF-TSK-QLT-0007`
- **前提** 一个已结束的代理运行及主代理的结构化复核
- **当** 物化器创建结果证据包
- **则** 六个结果字段与任务/完成记录/stdout/stderr/差异/测试哈希完整绑定，且不从自由文本猜测字段；身份只接受 qoder/codex，Codex 逐任务原始任务另绑定稳定工作包、有序任务、目标、调用者合同与运行器身份
- **并且** Qoder 任务/完成记录作为签发者来源证明时接受运行器的格式化 JSON 空白字符、冻结原始定位/哈希，并拒绝重复键、身份漂移、非终态或陈旧完成记录
- **证据负责人:** `tests/gates/test_evidence_packet.py`
- **状态:** implementation-reviewed; 目录收据待处理

<a id="lf-gate-issuer-001--p0--trusted-issuer-provenance"></a>

## LF-GATE-ISSUER-001 · P0 · 受信签发者来源

- **来源:** 验收控制面 / `LF-TSK-QLT-0014`
- **前提** 一个请求签发 Gate 收据的执行者
- **当** 签发者物化器验证其权限
- **则** 只接受权限来源注册表中固定核验器证明的来源证明，自报执行者、角色或核验器不能形成信任
- **证据负责人:** `tests/gates/test_issuer_packet.py`
- **状态:** implementation-reviewed; 目录收据待处理

<a id="lf-gate-plan-001--p0--frozen-deterministic-plan"></a>

## LF-GATE-PLAN-001 · P0 · 冻结的确定性计划

- **来源:** 验收控制面 / `LF-TSK-QLT-0008`
- **前提** 当前目录、显式证据/签发者证据包与版本化的注册表
- **当** 同一模式编译 Gate 计划
- **则** 不产生写入或运行身份，且相同规范输入得到相同内容指纹；规划器按 qoder/codex 使用互斥原始任务结构定义，只从当前目录投影负责人/discovery/文件声明，并让 TASK_VALIDATION 选择检查、审查/目录冻结零检查的证据消费层次
- **证据负责人:** `tests/gates/test_gate_planner.py`
- **状态:** implementation-reviewed; 目录收据待处理

<a id="lf-gate-dispatch-001--p0--fail-closed-dispatch-preflight"></a>

## LF-GATE-DISPATCH-001 · P0 · 失败即拒绝的派发预检

- **来源:** 多代理调度安全 / `LF-TSK-QLT-0005`
- **前提** 冻结候选、目录/路径快照与完整非终态活动实例声明
- **当** 预检对账任务身份、负责人、允许范围/写入声明、案例/符号链接安全性与公开合同写入者
- **则** 只有互不重叠的规范写入者可 PASS；不完整或不安全输入为 FAIL；确认的归属、声明、写入或合同冲突为 BLOCKED
- **证据负责人:** `tests/gates/test_dispatch_preflight.py`
- **状态:** implementation-main-verified; 集成审查与目录收据待处理

<a id="lf-gate-check-001--p0--typed-fail-closed-checks"></a>

## LF-GATE-CHECK-001 · P0 · 带类型且失败即拒绝的检查

- **来源:** 验收控制面 / `LF-TSK-QLT-0009`
- **前提** 冻结注册表中一组必需检查
- **当** 执行器执行并聚合结果
- **则** 只运行固定参数序列，且跳过、未运行、空、格式错误或未证明的零退出码均不能得到 PASS
- **证据负责人:** `tests/gates/test_gate_executor.py`
- **状态:** proposed-implementation

<a id="lf-gate-validation-001--p0--observable-immutable-validation-run"></a>

## LF-GATE-VALIDATION-001 · P0 · 可观测且不可变的验证执行

- **来源:** 验收控制面 / `LF-TSK-QLT-0010`
- **前提** 一个合法 TASK_VALIDATION 计划
- **当** 唯一 Gate CLI 启动检查
- **则** 检查器前持久化并向调用方刷新 START/运行_id，最终收据不可覆盖，状态只读固定运行路径，且只有 TASK_VALIDATION 能调用交付执行器
- **证据负责人:** `tests/gates/test_gate_lifecycle.py`
- **状态:** proposed-implementation

<a id="lf-gate-review-001--p0--independent-review-chain"></a>

## LF-GATE-REVIEW-001 · P0 · 独立审阅链

- **来源:** 验收控制面 / `LF-TSK-QLT-0011`
- **前提** 一份不可变 TASK_VALIDATION 收据
- **当** 独立审阅者通过同一 CLI 发布审查
- **则** 生产者自审、身份漂移或非空交付检查均失败；审阅者写入集合必须与哈希绑定当前计划对账，且核验器必须重验验证变更文件快照对应的当前受验对象字节/状态；只读审查的空写入集合必须来自通过物化器与规划器的空快照/差异/changed-files 证据包（包括不同执行者的 Codex 工作包逐任务投影），调用方自报空列表不能掩盖实际受验对象变异；受验对象收据保持不变且投递验证不重跑
- **证据负责人:** `tests/gates/test_independent_review.py`
- **状态:** proposed-implementation

<a id="lf-gate-hash-001--p0--acyclic-evidence-graph"></a>

## LF-GATE-HASH-001 · P0 · 无环证据图

- **来源:** 验收控制面 / `LF-TSK-QLT-0012`
- **前提** 计划、清单、叶模块与前序收据引用图
- **当** 核验器只读核对定位、身份与哈希
- **则** 自环、回边、别名、缺失 node、变化的字节或环均失败
- **证据负责人:** `tests/gates/test_hash_dag.py`
- **状态:** proposed-implementation

<a id="lf-gate-catalog-001--p0--current-catalog-decision"></a>

## LF-GATE-CATALOG-001 · P0 · 当前目录决策

- **来源:** 验收控制面 / `LF-TSK-QLT-0013`
- **前提** 当前验收注册表、验证/审查/哈希证据与所有必需依赖收据
- **当** 唯一 CLI 发布 CATALOG_DECISION
- **则** 只有零-check 证据消费计划、无孤儿或重复案例、规范定位/哈希均当前、前序链验证受验对象快照重验当前、审查独立、每份前序/依赖收据签发者均按当前权限来源注册表验证且依赖为当前输入 PASS 时才可目录 PASS；同字节别名、自造签发者收据、非空交付检查或审查后受验对象变异必须失败
- **证据负责人:** `tests/gates/test_catalog_decision.py`
- **状态:** proposed-implementation
