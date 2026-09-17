# 架构文档重构审查

> 2026-09-17 · change：`restructure-architecture-reading-guide` v1.0.0 · 第一阶段内的文档重构。

本轮把阅读路径改为整体总览、专题解释、详细合同，并增加穿插图解。架构决策、产品实现和正式阶段验收保持各自的状态；这里记录文档检查，不把改写正文视为新的 Task receipt。

## 阅读结构为何调整

原总览同时展开目标、模块、流程、同步矩阵、失败、验收和调度。现在总览围绕观看与学习两条主链路建立整体认识；模块、流程、生命周期、决策和工程交付各自回答一个专题；详细合同保留逐项实现要求。

从 [新架构入口](../architecture/README.md) 开始，进入 [总览](../architecture/phase-1.md)，再按问题选择专题。原 539 行总览收敛为 104 行；原 615 行横切正文改为 80 行导读，精确条款移入六份独立合同。行数只说明阅读层次发生变化，不作为质量指标。

## 保留的设计边界

七个业务 Domain、公开 contract、无环依赖、薄 Extension、英文优先、规则/模型分工、PostgreSQL 事实来源、Learning→evidence→Profile、显式/隐式一致性、持久 handoff、晚到拒绝及删除屏障都保持原义。

十项 ADR 的编号、原标题、Proposed 状态、结论、替代方案和复审要求保留。调整为四组问题，并在每项内部先展示推荐结论。Java 25/Spring Boot 的实际骨架已落实，因此纠正原参考文字里“框架尚未决定”的阶段表述；没有加入业务类型、SQL、endpoint 或 Prompt。

## 图解与检查范围

16 张 PlantUML 图，涵盖 architecture、component、deployment、sequence、activity、mindmap、state、class 八类。正文前先交代问题，图后解释读法、结论与省略范围；源码、brief、PNG 和 SVG 在 [图源目录](../architecture/diagrams/README.md)。

13 张使用 typed profiles，三张 class/state 图使用 fallback；后者没有 typed coverage/layout 证明。全部实际 SVG 经过图包验证，PNG 在本地由 SVG 转换为白色底图。每种图型都有视觉复核；概念关系与工作状态图各调整一次布局；学习时序补明成功返回 version 前的缓存失效，保证图与原流程的顺序一致。三张调整图各渲染两次，其他图各一次；批次 renderer preflight 一次，没有循环重试。

| 检查 | 结果 | 实际范围 |
|---|---|---|
| 详细条款迁移 | PASS | 9 个详细合同块、5 个生命周期块与 baseline 比对；只迁移相对链接和修正已落实的 Java 阶段状态，不删除精确条件 |
| ADR 保留 | PASS | 10 项编号/原标题/Proposed 状态，全部正文小节保留，仅分组、层级、先后及小节标题变化 |
| 本地链接与章节锚点 | PASS | 全 docs 与根 README，67 个 Markdown 页面、最终链接统计见本地核对记录；旧主架构锚点通过兼容 id 保留 |
| 图包及导出绑定 | PASS | 16 个真实 SVG；48 项 PUML/SVG/PNG 哈希核对；13 typed + 3 fallback 的证明范围分别记录 |
| 视觉与读法 | PASS | 查看全图联系表和各类图的完整 PNG，中文可读，无缺字/截断；正常路径、箭头语义与概念图省略范围由正文说明 |
| 旧 profile 直接输入诊断 | PASS | 15 项受影响文档任务的旧 lexical assertions 仍成立；只是诊断，不签发 current receipt，搬移的详细文件仍需后续正式纳入冻结输入 |
| 产品与规则 owner 未变 | PASS | backend、scripts、tests、harness、planning、AGENTS 与正式 OpenSpec spec 的 tracked bytes 不变 |
| 公开增量 Gate | FAIL | 显式运行后缺 `LEXIFLOW_GATE_EVIDENCE_PACKET` / missing-evidence-context，在上下文阶段拒绝，零 checker execution |

七个历史 `#Lx-Ly` 引用保留为原时点的行号证据，不冒充新章节锚点，也不以旧行号为当前正文背书。新专题页使用命名章节或页面链接。没有运行产品构建/测试来验证纯文档排版，避免重复已通过的 Java 交付检查。

这些 PASS 只针对上表明确范围的本地文档工程检查，未产生正式独立 Task review/catalog receipt。公开 Gate 的 FAIL 与下述旧输入失配均保持真实结果，不能称第一阶段验收通过。

## 当前输入与历史收据分别处理

本轮只读复核 29 个原 catalog receipt 根：14 个仍匹配当前输入；15 个因架构或参考正文 bytes 改变而返回 FAIL / hash-graph-invalid。原 receipt 未修改或重签，交付命令重新执行数为 0；这些 FAIL 表示历史证据已不能证明改写正文，不是回滚架构的理由。

受影响任务：ARCH-0001 至 ARCH-0007、PRD-0001、SEM-0001/0002、PRF-0002、SEC-0001、OBS-0001、ADP-0001、OPS-0001。路径搬移后的详细合同尚未成为旧 frozen required_inputs 的新正式证据；重新验收时应由相应合同 owner 纳入新输入与冻结版本，不用文档内链接替代 hash binding。

本轮没有改 harness、catalog、checker/schema 或历史收据；没有 product 测试重复执行，也没有派发 Qoder/Codex 子任务、循环轮询、Git 提交或推送。业务架构仍 Proposed，G1 未退出，第二阶段未激活。

原始 baseline、diagram packages、静态校验拒绝、布局调整、迁移比对和 receipt freshness 记录在本地 `tmp/architecture-docs-20260917/`，不提交运行数据。
