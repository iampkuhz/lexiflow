# 信任边界与威胁合同

> Proposed。先读 [横切合同导读](../phase-1-cross-cutting-contracts.md)。

网页、客户端声明、模型输出和投递 payload 跨边界时都需要明确验证。

## 精确语义与后续证据

下面保留原合同的任务映射、Must/Should/Later、conformance、验收和未决假设。它们约束后续实现，不能从文档存在推断产品通过。

<!-- retained-contract:start -->
**对应任务：`LF-TSK-SEC-0001`**

### 5.1 资产与信任区域

| 资产 | 业务 owner | 主要风险 |
|---|---|---|
| 用户、设备与会话身份 | Identity & Access | token 窃取、会话混淆、越权。 |
| Caption/Context 与来源位置 | Content | 私密内容泄漏、恶意内容污染、错误归属。 |
| Vocabulary Profile 与 Learning facts | Vocabulary Profile / Learning | 跨用户读取、行为伪造、不可解释篡改。 |
| Durable work 与 annotation result | Workflow/Enrichment | replay、重复执行、篡改、stale 投递。 |
| Provider credentials、预算与路由策略 | Platform/Semantic | 密钥泄漏、成本滥用、供应链风险。 |
| Logs、metrics、traces 与 run evidence | Platform/Observability | 二次数据泄漏、不可控保留和关联识别。 |

信任区域如下：

1. **Browser/page 区域**：网页、YouTube DOM、第三方脚本和页面文本不可信。
2. **Extension 区域**：官方 Extension 代码可受浏览器上下文、旧版本或本地篡改影响；它是受限客户端，不是授权事实来源。
3. **Public network / API boundary**：所有客户端输入在此重新认证、授权、限流、规范化和绑定 owner。
4. **Application / Domain 区域**：只消费已经通过入口校验的 command，但仍按 Domain invariant 验证行为语义。
5. **Worker / durable handoff 区域**：任务来自可信存储却可能重复、过期、旧版本或被错误配置；消费端必须幂等并重验合同。
6. **Store 区域**：PostgreSQL/Redis 通过各 Domain outbound port 访问；基础设施权限不赋予跨 Domain 数据所有权。
7. **External Provider 区域**：Provider 是外部处理方；输入必须最小化，输出始终不可信，凭据只能由 server-side adapter 持有。

### 5.2 Boundary crossing rules

**Must**

- 网页内容只作为数据进入 source adapter；其中的指令性文本、标记或脚本不得改变系统 policy、工具权限、Provider routing 或观测规则。
- Extension 永远不能自证 user、device、profile version、Learning effect 或数据 owner。API 用服务端身份上下文重新绑定并校验所有引用。
- API 在 durable intake 前限制输入大小、上下文窗口、语言/编码、引用归属、幂等身份和速率；失败输入不得进入 Domain 或 worker queue。
- Worker 对 job identity、task/change contract version、owner、attempt/retry budget 和 stale/cancel 状态重新校验；重复交付只产生一次业务效果。
- Domain 只能通过自己的 port 访问 store；不得利用共享数据库权限读取其他 Domain 表或绕过公开 contract。
- 发送 Provider 的数据仅包含当前 capability 所需的有界文本和非个人化语义上下文；不得发送完整观看历史、完整 Vocabulary Profile、会话凭据或无关 transcript。
- Provider credential 只存在于 server-side secret boundary；不得进入 Extension、durable payload、日志、trace、fixture 或 Provider result。
- Provider 输出经过大小、结构、语言、引用和 Semantic contract 校验；输出文本不得直接变成代码、查询、权限或路由指令。
- telemetry 按第 6.5 节 redaction contract 处理；生产数据不得复制到测试或 Agent handoff。

**Should**

- Extension 权限保持最小 host/feature 范围，页面隔离层与 privileged Extension 层分开。
- API、worker 与 store 使用独立运行身份和最小权限；Provider adapter 按 provider/环境隔离 credential 与预算。
- 高成本 Semantic capability 同时受用户、设备、来源和全局预算保护，避免合法账号被自动化放大。

**Later**

- Phase 2 固定授权、用户隔离、导出和删除控制；Phase 3 固定 transport 认证与输入限制；Phase 4 固定 Provider data-handling；Phase 7 完成密钥轮换、恢复和外部安全测试。

### 5.3 Threat 与 abuse-case register

| ID | Abuse case | 边界 | Must control / safe outcome |
|---|---|---|---|
| SEC-T1 | 恶意页面伪造 caption、超长文本、控制字符或指令性内容。 | Page → Extension → API | 本地隔离、规范化、大小限制；当作内容数据；失败时只显示原英文/原来源内容。 |
| SEC-T2 | 被篡改 Extension 使用他人 content/profile/result identity。 | Extension → API | 服务端认证与 owner binding；跨用户引用统一拒绝且不泄漏存在性。 |
| SEC-T3 | 离线重放、重复点击/展示或伪造 Learning event。 | Extension → Learning | 幂等 identity、引用链和语义校验；客户端数值不能直接写 familiarity。 |
| SEC-T4 | 攻击者制造大量难句触发 Provider 成本。 | API/Worker → Provider | capability allowlist、配额、总预算、回压与可观测拒绝；英文路径继续。 |
| SEC-T5 | durable job 重复、过期、版本不兼容或被错误路由。 | Store → Worker | contract/version/owner 校验、幂等、stale cancel；不产生重复 annotation/evidence。 |
| SEC-T6 | Provider 返回 malformed、恶意标记、过长内容或诱导系统改变 policy。 | Provider → Semantic | 标准化与约束校验；失败分类；原始输出不进入 Domain/telemetry。 |
| SEC-T7 | Redis key 碰撞或共享 Semantic cache 混入用户信息。 | Cache → Application | namespace/version/user isolation、跨用户复用 eligibility；可疑命中按 miss 处理。 |
| SEC-T8 | 日志/trace 记录字幕、term、Profile、token 或 Provider payload。 | Runtime → Telemetry | allowlist 属性、集中 redaction、forbidden-field scan；事件保留但敏感值删除。 |
| SEC-T9 | 内部模块凭共享数据库绕过 Domain owner。 | Module → Store | port-only dependency、最小数据库权限、architecture/contract test。 |
| SEC-T10 | 账号切换或撤权后离线 L1 继续显示前用户 hint。 | Identity → Extension L1 | user/session binding、主动清除、有限 hard expiry、下一次鉴权拒绝。 |
| SEC-T11 | source revision 改变后旧 annotation 被投递到新 caption。 | Delivery → Extension | content/caption identity、revision/profile version 比对；stale 静默丢弃。 |
| SEC-T12 | 运维、测试或 Agent 复制生产字幕与学习数据用于复现。 | Store/Telemetry → Tooling | 只用合成 fixture；受控诊断 metadata；禁止真实数据进入仓库和 handoff。 |

### 5.4 Acceptance evidence

`LF-TSK-SEC-0001` 的 `PASS` 证据至少包括：

1. 一张含 Extension、API、Application、worker、PostgreSQL、Redis、Provider 与 telemetry sink 的 data-flow/trust-boundary 图，每条 crossing 标明认证、校验、最小化和 owner。
2. 上述 threat register 的逐项评审 receipt，至少给出预防、检测、安全降级和后续 owner；仅写“以后加安全”不算完成。
3. abuse fixtures 覆盖跨用户引用、重复 event/job、超长/恶意 caption、malformed Provider、stale result、cache collision、成本放大和 telemetry 泄漏。
4. secret/data-flow review，证明 Provider 凭据只在 server adapter，发送 Provider 的输入有界且无 Profile/history/session credential。
5. architecture evidence，证明 core module 不依赖 Chrome/YouTube、HTTP、Redis、PostgreSQL 或具体 Provider，且共享 store 不绕过 Domain contract。

### 5.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| SEC-A1 | 首版认证机制可以同时表达用户与设备归属，不需把设备提升为独立业务 owner。 | Phase 2 Identity threat review。 |
| SEC-A2 | 发送有界三句上下文足以满足主要 Semantic capability。 | Phase 4 质量/隐私对照实验。 |
| SEC-A3 | Browser L1 的有限 hard expiry 加下次鉴权足以处理长期离线设备。 | Phase 5/7 离线撤权 journey。 |
| SEC-A4 | 单体内部的模块权限与 architecture gate 在早期足以防止跨 Domain 数据访问。 | Phase 2 persistence spike；若不足，再拆数据库 role/schema 权限。 |
<!-- retained-contract:end -->
