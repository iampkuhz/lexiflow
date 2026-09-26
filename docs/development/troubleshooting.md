# 1. 按阶段排障：先找失败层，再决定下一步

> 位置：[文档首页](../README.md) → [工程地图](overview.md) → 排障。先确认当前 workflow 与输入，不要把所有问题都处理成“重试一次”。

## 1.1. 本地体验没有提示

若 API 在启动时已报 `relation "lexicon_hint_lookup" does not exist`，先处理[数据库结构与发布资料](operations/lexicon-import.md#15-已有开发库结构不匹配时)，不是 Chrome 或模型配置问题。首次安装按[根 README](../../README.md#本地启动)完成本地编译、配置初始化与词库发布，不能只运行最后的 API 命令。

API 能正常启动后，按浏览器 → 请求 → API → 资料 → 展示定位：

1. 英文是否正常可见？无可靠来源、广告、导航或关闭增强时，扩展应退回英文，不伪造输入。
2. 构建端口是否匹配 API？查看[本地体验](operations/local-experience.md)，不靠改远端地址解决。
3. API 日志的 runtime lexicon 是否为 postgres，publishedVersion 是否有效？health 成功不证明资料已发布。
4. `NO_PENDING` 表示没有可靠提示，不是模型没配置；观看链路不调用模型。
5. 是否在返回前已经换句、seek 或关闭？过期结果必须被放弃，不能延续到下一句。

API、页面聚合诊断与 service worker 的观察方法见[扩展 E2E 与诊断](operations/extension-e2e.md#15-日志与排障)。不同阶段样本数不同，不能直接相减均值或跨进程时钟。

## 1.2. Verify 卡住或失败

| 现象 | 先确认 | 安全下一步 |
| --- | --- | --- |
| missing-environment | 缺哪项 required_environment | 去[隔离环境准备](operations/verification-environment.md)，不连接开发库 |
| Java 版本或启动器错误 | LEXIFLOW_JAVA_HOME 与 Temurin 25 | 修复运行时，不修改 Wrapper/锁或跳过检查 |
| 模块断言失败 | 哪个 check、原生报告和输出 | 用[精确任务](reference/java-checks.md)定位，再回完整检查 |
| 文档/投影错误 | 具体文件、链接、字段和真源 | 用[独立工具](reference/standalone-tools.md)定位；不手改派生结果掩盖漂移 |
| 输入漂移、覆盖缺口 | frozen input、diff 与选中检查 | 核对最终输入再验证，不能只修改 report |

环境不足是 BLOCKED，不说明业务断言失败。非 PASS 不能被其他成功项冲抵。report 只证明其绑定输入，不证明之后修改的文件。

## 1.3. Delivery Gate 无法继续

| 阶段 | 常见卡点 | 处理边界 |
| --- | --- | --- |
| submit | 报告/Task 要求/producer 不完整 | 补真实输入，不编造 identity/descriptor |
| validate | 与 producer 身份重合，冻结输入漂移 | 使用真实独立 Session；漂移后重新匹配送验输入 |
| review | 无真实 findings 或 reviewer 不独立 | 完成实际审查，不默认 PASS、不重跑交付命令 |
| check | dependency receipt 或用户 approval 缺失 | 条件补齐后重核，不能自动生成批准 |

逐步操作见 [Delivery Gate](change-delivery/delivery-gate.md)。已有记录不可覆盖；保留原失败，不用一次定向成功改写历史。

## 1.4. Agent 尚未完成或收到重复 callback

先绑定明确 run/Task/Session，不扫描“最新”日志猜归属。STARTING、未知占用和 observation timeout 不构成重派许可。Qoder 父任务在 AWAITING_CALLBACK 时结束当前轮；Codex 原生子代理使用原生事件等待。

匹配终态 callback 后核对事实，再显式 ack；已 ack/superseded 的重复事件直接结束。调度失败、执行失败、验收失败分别计数和处理，不改 Task identity 重置预算。详见 [Agent workflow](agent-workflow.md)与[身份/事实](agent-workflow/identity.md)。

## 1.5. 记录并回到流程

记录输入版本、命令、证据位置、实际 PASS/BLOCKED/FAIL 及未证明边界；[记录模板](reference/record-template.md)只帮助说明，不是正式 receipt。修复局部问题后回到对应 workflow 阶段，不跳过原先要求。
