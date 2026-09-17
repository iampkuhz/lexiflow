# Phase 1 Architecture Review Checklist

`LF-DECISION-P1-001` 只有在下列项目被明确接受或记录为待决时才能完成。

## Product boundary

- Vocabulary Profile 是服务端核心资产；Extension 只缓存、渲染和采集行为。
- YouTube 被定义为 Content Adapter，未来来源不需要复制核心学习逻辑。
- 生成、投递、实际展示、点击四类事实没有混用。
- `HintDisplayed` 只在当前 caption 的 hint 真正渲染后上传；晚到且未展示的 annotation 不形成学习证据。

## Domain ownership

- Identity/Devices、Content、Global Lexicon、Vocabulary、Learning、Enrichment、Semantic、Client Delivery/Sync、Platform 各有唯一 owner。
- 任一持久状态都能回答“哪个 Domain 可以修改它”。
- 跨 Domain 依赖通过公开 contract，禁止跨表访问和具体适配器反向进入 core。
- Learning 到 Vocabulary 是更新命令，Enrichment 到 Vocabulary 是版本化只读快照，避免循环依赖。

## Runtime flow

- 英文字幕显示路径不包含网络、后端或模型前置条件。
- 确定性 enrichment 快路径有明确时延预算和降级结果。
- 语义 cache miss 使用增量异步结果；迟到结果必须由 caption/context/profile/engine version 防止误应用。
- 只有 durable handoff 成功后才能在 fast result 声称 pending；失败时返回 no-pending 降级。
- Learning event 先可靠、幂等保存再 ACK；Profile 投影允许重放并处理重复、乱序与多设备输入。
- 显式 known/unknown 的最终响应必须区分 Profile 已投影与待补投影，不得先 ACK 事件再假定下一请求能读到新版本。

## Cache and failure isolation

- 可共享的 contextual meaning 与用户专属 need-hint 决策分开缓存。
- PostgreSQL 是事实来源；Redis、浏览器缓存和投影可重建。
- Redis、Semantic Provider、worker 或后端不可用时，英文观看仍成立。

## Semantic、security 与 source contracts

- Semantic 只暴露 `disambiguate`、`translateInContext`、`extractPhrase`、`explainSentence` 等任务级能力；Domain 不依赖 Provider SDK、模型名、Prompt 或 transport 错误。
- Semantic 的 success、abstained、malformed、timeout、refusal 与 late result 有稳定结果语义；Semantic evidence 不直接决定是否展示。
- Extension、API、worker、store 和 Provider 的 trust boundary、最小输入、credential owner 与 abuse cases 已明确。
- correlation、log、metric、trace 和 redaction 能关联 fast/slow/learning 流程，同时禁止默认记录完整字幕、模型输入和 Vocabulary Profile。
- YouTube、Web、PDF 与 audio 都通过 source-neutral Content contract 表达；capability absent、缺失和乱序不会触发猜造。

## Delivery architecture

- Modular Monolith 是当前默认；`api` 与 `worker` 是组合根而非业务微服务。
- 当前没有引入 Kafka、Kubernetes、Vector DB、Service Mesh 或完整 Event Sourcing。
- 模块边界计划由构建依赖和 architecture tests 强制，而不只依赖文档。

## Technology and reproducibility proposal

- 推荐基线是 Java 25 LTS + Spring Boot 4.x + Gradle 9.7.x 后端，Manifest V3 + TypeScript 6.x + Node 24 LTS Extension，以及独立 Python 3.12 工具链；三套候选方案和取舍已审阅。
- Java Domain 保持 plain Java；Spring、数据库、Redis、HTTP、Chrome 与 Provider SDK 只进入 adapter/composition root。
- Python 3.12 仅用于 Phase 1 Harness，已记录 security-only/EOL 风险、升级阈值和不晚于 2027-10 的复核窗口。
- 批准后必须精确锁定 runtime、Wrapper、distribution/JAR checksum 与依赖图；多项目/included build strict lock、错误 runtime、缺 lock、双环境 clean build 和 architecture test 都有计划证据。
- 系统默认 Java 仍为 26，但产品构建通过确定性 launcher 只接受仓库 Temurin 25；Gradle Wrapper、严格依赖锁、Java quality gates 与 Architecture Test 已落地并完成本机 clean build。Phase 1 仍需 current-input Gate receipt；独立干净环境复现属于批准后的后续验收。

## Gate and evidence boundary

- OpenSpec change-local checklist、独立文档 review、Qoder exit `0`、callback queued 和 bootstrap validation 都不等于 catalog `PASS`。
- Planning validator、Gate control plane 与 dispatch file-claim preflight 必须按目录 DAG 完成，并在最新冻结输入上运行 required checks。
- 每个正式 receipt 固定 task/change/run/agent/parent identity、input hashes、命令/退出码、acceptance/effect/risk 和 changed-file claim 对账；latest 指针不作为证据。
- G1 只有在 required receipt 聚合为 `PASS` 且用户明确 `APPROVED` 后才开放 Phase 2；任何 skipped、not-run 或 unavailable 都不能聚合成 `PASS`。

## Initial targets requiring owner approval

- 英文 caption 到可见 P95 不高于 50 ms（本地路径）。
- L1 annotation P95 不高于 20 ms。
- 后端确定性 annotation P95/P99 不高于 250/500 ms。
- 异步语义 annotation P95 不高于 2.5 s，超时静默降级。
- 显式 known/unknown 在其他活动设备 5 s 内生效；被动事件 60 s 内影响 Profile。
- MVP 标注集初始目标：hint span precision 至少 85%，contextual hint 人工可接受率至少 80%。

这些数值在 Phase 1 都是 `Assumption / Initial Target`，不应在确认前描述为已承诺 SLO。
