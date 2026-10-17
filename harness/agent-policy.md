# Agent 协作规则

本页面向父代理、实现者、验证者与审查者；开始任务先遵守 [AGENTS.md](../AGENTS.md)，按 [Harness 导航](README.md) 只加载本任务相关规则。Session 生命周期与 checkout 由客户端管理，读写不要求统一仓库状态。隐私与 Git 授权边界不因委派、失败恢复或验收而放宽。

## 工作范围与分解

- Task ID 已分配即稳定，不复用或重定义；移除任务保留来源。执行 run 使用真实 UUID，显示别名不是证据身份。Task 计划预算 20–90 分钟，工作包通常 10–360 分钟；预算约束 Task/工作包，不要求 Agent Session 保持相同时长。
- 跨领域、多个主要文件归属、公共 contract 与多个消费者同步变化、独立验证环境，或超过原 Task 预算且可独立验收时拆 Task。实现与直接测试、同归属且连续的小工作同包。
- 包含稳定 work_package_id、精确有序 Task IDs、目标、允许/禁止范围、上下文、逐 Task 产物及验收。共享 primary owner 与 contract，并行 ownership 不重叠。已派发 scope 不静默扩大；新独立结果分配新 ID 并记录 discovered_from，范围或验收变化递增 task_version。
- DAG 使用真实 hard、soft、contract、discovered_from、supersedes 关系；soft 不阻 READY，hard 要求 PASS，contract 精确绑定唯一生产者的名称与版本及 PASS。阶段号不隐含依赖，集成依赖全部贡献者。
- change_version 的 patch 只澄清，minor 增加范围、证据或验收，major 改变目标、归属边界或 contract；版本不授予历史兼容。公共 contract 变更串行，先完整列出消费者并同步最新逻辑，涉及结构时重建重导并说明恢复边界。
- 架构、contract、安全、数据、成本的重要决定在 OpenSpec 记录唯一结论、取舍及可观察重新评估条件；阶段 Gate 或改变用户已批范围/产品行为先由用户确认。

## codex_model_policy

父代理每次 spawn 或 followup 前，按**完整工作包（含例外）**分类并显式传递模型与 reasoning；不按角色、继承模型、调用频率、代理名或提示词选择。

| 工作包判断 | 模型与 reasoning | 必要依据 |
| --- | --- | --- |
| 合同与验收冻结、范围有界连贯、能确定性直接验证的 routine 包 | `gpt-6-luna` / `low` | 必须采用 routine 路由 |
| 其他复杂、混合或未分类包 | `gpt-6-luna` / `medium` | 先采用 default 路由；类别本身不授予升级 |
| Luna 的具体失败或未覆盖风险确实需要升级 | `gpt-6-sol`，显式选择 reasoning | 保留稳定 Task ID，记录具体证据与父代理批准 |
| 用户明确要求 | 用户指定模型与 reasoning | `gpt-6-astra` 仅 human 授权；其他指定模型同样记录请求，不改默认路由 |

选择依据写在既有父代理派发说明，不新增机械回执。Luna 阻塞时停止并紧凑回调，父代理先定位失败层，再依据证据决定是否升级。

## 派发与用户可见交接

10 分钟内的确定性单命令或小修复由主代理完成；超过 10 分钟且范围可隔离，或需大量新上下文而可简洁验收的工作优先原生子代理派发。只读独立 review 可由主代理按真实身份边界执行。默认当前父任务下的原生协作，不为内部子任务创建用户线程；同时最多 **6 个子代理**，默认不继承完整历史，完整历史 fork 须明确例外理由。Qoder 仅手工客户端入口，不提供仓库委派、回调或证据消费链。

spawn 与 followup 都由父代理在当前聊天正文说明；工具输出、终端、本机日志或 OpenSpec 状态**不能代替**：

1. **调用前**说明原生执行器、work_package_id、目标、写入范围、分类、模型与 reasoning 及理由；明确是准备派发而非已启动。
2. **返回后**报告真实受理、拒绝或失败及原生 agent 标识；只有对应启动证据才说已启动，受理不是完成。预检 PASS、计划或预计动作不是派发证据。
3. **终态后**说明实施结果与下一步，分别报告实施、独立验证、审查与 Git 发布证据；完成通知或 exit-zero 不是验收。

只展示有界摘要、标识和仓库相对产物路径，不粘贴原始日志、凭据、本机私有路径或业务输入。用户明确要求启动或继续时，在授权范围内实际执行到派发、同步交付或可核验阻塞；不得仅描述下一步后结束，也不暗示结束后自动继续。

## 真实身份与回调

身份由现有原生 metadata 和 route 认证，调用者不得注入 runner 身份，也不能按日志或 PID 猜测身份。actor 跨 run 稳定，共享 host session 的不同原生 thread 可独立；本机信任不等于平台密码学证明。来源缺失 BLOCKED，伪造或冲突 FAIL；消费历史证据须重读真实来源。

派发时冻结工作包合同与有序 Task 版本；runtime binding、逐 Task 结果与单次 package completion 不可变并哈希绑定。先发布真实产物，再发原生完成通知；父代理核验原始产物，latest 指针不是证据。字段、schema 与存储布局由 [Agent 实现](../scripts/agents/README.md) 定义，不以本页另造协议。

使用原生长事件等待，仅有信息事件或终态才读日志。回调包含精确 run_id、逐 Task 状态、产物定位、直接检查及最多 3 项 blocking findings；禁止带完整上下文或日志。报告验收映射、file claims、验证命令/退出/日志/类型化结果、风险、恢复与限制的可核验定位。

## 失败预算与接手

断言失败先保持 check-needs-triage，由源码和日志证据区分产品与 fixture 问题；没有具体根因不更换工作包或归属。一个共享 deadline 包含内部进程 timeout 与取消，不机械重复派发或重置身份绕过预算。同一实现者最多返工 1 轮；耗尽后拆 Task、转交、Main 接手或 BLOCKED。

报告实际失败层、阻塞证据和可执行接手路径；可自行完成的准备或合规接手继续执行。仅在缺用户信息、真实取舍、必需授权、外部恢复或执行能力时交还控制权。

## 冻结、验证与独立性

GitHub 发行采用单维护者授权：owner 可批准自己触发的受保护 Environment；保留 required reviewer、正式 tag 限制、管理员禁绕过与显式 `PUBLISH`。此人类授权不替代下述原生 actor 独立性、完整检查或资料许可核准。

同 checkout 集成验证串行、非等待；窗口不锁编辑，输入漂移失败关闭。不自动终止未知进程、删除未知锁，也不覆盖其他写入者修改。

最终 checkout/input 冻结后先做直接小回归，再由不同真实 actor 的 validator 执行一次完整 Hook。实现者只做静态、编译与精准测试，不例行先跑全套，更不得签发 TASK_VALIDATION 或 INDEPENDENT_REVIEW。所有风险层的 validator 均不同于全部 producer/submitter；高风险工程与正式发行还需 reviewer，reviewer 不同于 validator 与实现者。

review 只读冻结 diff/evidence，CATALOG_DECISION 只核 receipt/hash DAG，两层不重跑命令。仅必需检查失败或相关输入/环境变化才重跑；复用仅限同次事务且合同证明输入、配置、环境、窗口与 runner 完整等价，开发自检不得复用为 Formal，禁止跨交付复用。不另造 host session 或独立性 attestation 代替真实 actor。

结果仅 PASS、BLOCKED、FAIL。skipped、not-run、unavailable、excluded、queued、acknowledged 和 exit-zero 均不等于 PASS；必需检查未运行不能通过。风险可调路径位于 [risk.yaml](../config/engineering/risk.yaml)，敏感内容识别与固定验收判据归对应实现，不放宽上述人类责任。
