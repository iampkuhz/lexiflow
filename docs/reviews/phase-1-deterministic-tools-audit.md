# 第一阶段确定性工具审查

> 2026-09-17 · 当前变更：`audit-phase1-deterministic-enforcement` · 范围仅 Architecture / Java Foundation。

本页是本轮审查入口。先看下表了解每个工具负责什么，再看实际发现与修复；架构提案、工程执行和正式阶段验收分开陈述。详细命令、原始报告、反例与哈希在本地 `tmp/quality/phase1-audit-20260917/`，不提交运行数据。

## 1. 工具配置与责任

| 工具 / 入口 | 固定配置及执行位置 | 本轮核实的执法行为 | 证明边界 |
|---|---|---|---|
| Java 编译 | Temurin 25，`--release 25`，UTF-8，`-Xlint:all -Werror`；共享 `lexiflow.java-base` | launcher 拒绝 JDK 26、错误发行商、缺失或损坏 release；完整构建编译产品与工具 | 不使用系统默认 JDK，也不自动升级版本 |
| Gradle Wrapper / locks | 9.7.1、distribution SHA-256；root / settings / included build / leaves 严格锁定 | 隔离篡改 PMD lock 版本后，真实依赖解析因严格约束冲突失败 | lock 保证版本一致性；本轮复用已有依赖缓存，不是双环境复现 |
| Spotless | 8.7.0，google-java-format 1.28.0；main/test Java | 隔离错误格式源码被 `spotlessJavaCheck` 拒绝 | 格式和 imports 由此独占，不在 Python 重写规则 |
| Checkstyle | 10.21.4，零 warnings/errors；共享 XML | 缺失类型/公开方法 Javadoc 等风格问题被真实任务拒绝 | 测试源码有明确 Javadoc/常量命名豁免；record 参数说明由下一行负责 |
| PMD | 7.25.0，共享八条高信号 whitelist；main/test | 未使用私有字段、空 catch 等反例被真实 `pmdMain` 拒绝 | 只承诺已启用的规则；不声称全部 PMD 规则均启用 |
| `javaSourceGates` | 一次共享 compiler parse：中文注释、生产 record 参数 Javadoc、main/test 禁止 PMD suppression | 目录、抑制、注释指令、record 注解与词法反例均验证；修复见第 2 节 | 明确允许合法版权头和单独工具指令；不证明注释内容与实现语义一致 |
| Javadoc / DocLint | private visibility，`Xdoclint:all,-missing`，`Werror` | 错误 link 被真实 Javadoc 任务拒绝 | 文档缺失由 Checkstyle / record 规则补足，不在 DocLint 再做重复规则 |
| ArchUnit | 仅导入产品 main 编译目录；规则和隔离 fixture 共用 | 非空/缺失输入、外部库、adapter、组合根、Spring、domain→application；正反例分别验证 | 第一阶段没有业务 domain 类，必须报告该范围为空；fixture 证明规则能力，不证明未来业务已合规 |
| Gradle 项目依赖护栏 | 模块→模块，application→模块，platform→application/模块，apps 组合；无环 | 合法图通过；反向、环、未知角色及缺失目标拒绝 | 此处检查项目方向；跨 Domain 私有数据访问仍须按公开 contract 设计审阅 |
| JUnit / 零跳过检查 | JUnit 6.0.3；安全 XML 解析；失败、错误、跳过、结果缺失、计数不完整均拒绝 | 十个 XML 正反例直接调用真实 Gradle owner；失败移除历史 PASS 文件 | 有源码却没有结果不能通过；无测试源码的空 leaf `NO-SOURCE` 不算业务测试完成 |
| JaCoCo | 0.8.14；leaf / root XML + HTML | 本轮实际生成覆盖率报告 | 当前是报告工具，未配置最低覆盖率拒绝阈值；覆盖的是第一阶段工程工具和骨架，不是产品功能 |
| 聚合交付入口 | 增量 `check`；完整 `deliveryFull` → `qualityFull` → `check` + 两个 Boot jar | 已声明聚合入口拒绝 `-x`、测试过滤、构建目录覆盖和 `--dry-run`，强制当前执行；完整验证串行执行 | 不能把子任务、退出 0、历史 XML 或未运行入口当作另一层通过 |
| Python 统一 Gate | catalog / dispatch / evidence / issuer / plan / receipt / hash DAG | 当前 registry 投影独立核对；公开增量入口如实记录 | 当前 registry 是 Phase 1 工程合同检查，尚无 Gradle adapter；Java delivery 是直接工程验证，不能伪装为正式 Java Task 收据 |

工具版本以 `backend/` 和 `harness/java-product.manifest.yaml` 为准。共享配置只维护一份；详细参考差异见 [参考一致性审查](phase-1-reference-and-consistency-audit.md)。PMD suppression 的真实形式依据 [PMD 官方文档](https://pmd.github.io/pmd/pmd_userdocs_suppressing_warnings.html)，Unicode escape 和文本词法依据 [Java 25 JLS](https://docs.oracle.com/javase/specs/jls/se25/html/jls-3.html)。

## 2. 本轮发现与修复

| 问题 | 修复位置与行为 | 验证方式 |
|---|---|---|
| `generated/gen` 名称让正常 source set 手写源码被漏扫 | `JavaSourceDiscovery` 不再按这些包名排除；真正构建输出仍排除，源码包名 `build/.gradle` 也必须扫描 | 固定目录反例 + 单元测试 |
| 只检查生产源码里的 `PMD.*` 字面量注解 | main/test 共用 AST；禁止 `PMD`、`PMD.*`、`unused`、`all`、数组和字面量拼接的抑制；无法确定的注解表达式拒绝，要求显式字面量 | 各 source set 抑制反例与正常 `unchecked` 正例 |
| 行注释 `NOPMD` 可绕过 PMD | 从真实行注释识别抑制标记；字符串和块文档中的术语不误报 | 抑制行拒绝，字符串/文档正例通过 |
| 块注释首行工具指令免除了后续说明 | 逐行排除指令，后续说明仍检查中文；版权头维持明确例外 | 多行英文反例拒绝、纯指令/版权头正例通过 |
| record 参数注解 `{}` 截断组件扫描 | 使用 AST 非 static record 成员，不再用首个 `{` 字符定位 | 第二参数缺 `@param` 的真实反例；static 常量不当成组件 |
| Unicode 注释分隔符漏扫，转义三引号 text block 误报 | 先按 Java Unicode escape 资格规则单次解码，保留原文件行号；词法器处理 text block 转义及 CR/CRLF | 隐藏注释/抑制反例、合法文本块与反斜杠正例 |
| JUnit XML 正则遗漏 failures/errors、畸形/缺失属性 | Gradle owner 安全解析 XML、校验 testcase 计数及各失败元素；禁外部实体；失败移除旧摘要 | positive、skip、failure、error、malformed、missing attribute、zero、count mismatch、entity、missing report |
| 非 Java 扩展名大小写及 build 包路径可漏过 | Gradle owner 按固定 Locale 规范扩展名；两个 Gradle 输入集合只排除 source set 外的 build/.gradle 输出，匹配实际扫描范围 | `.py/.PY/.kt/.scala/.groovy` 均拒绝；真实 source set 内 build 包中的 Python 反例拒绝 |
| 架构导入过宽，空集或漏编译易误读 | 独立 Luna 修复产品 main 范围、输入存在性和依赖规则，主会话审阅并运行实际 suite | 共享规则的隔离正反例；不污染生产源码 |
| 旧状态文档停留在 21/7/0 并声称 child 活动 | 当前说明移至本页及 README；原共享冻结文档保持 bytes，旧活动/计数仅适用于历史时点 | 本轮证据核对；旧收据保持不可覆盖，不批量重签 |

这些变化都在 Java/Gradle owner 实施。Python 临时探针只准备隔离输入、启动工具和收集结果，没有实现 Java 风格、注释、依赖或业务规则。

## 3. 实际执行与证据层次

基线完整执行使用 `deliveryFull --no-build-cache --rerun-tasks --console=plain`，171 个 actionable task 实际执行，11 个 Java 测试、零失败/错误/跳过。基线成功后八个自定义源码反例仍暴露漏检或误报，所以“正常构建通过”不能替代拒绝行为验证。

完整验证在架构 fixture 编译时发现 Spring 仅在运行时 classpath 的问题，补齐 testImplementation 与共享 BOM；锁文件将测试编译的 jspecify 1.0.0、slf4j 2.0.17 对齐既有运行时的 1.0.1、2.0.18，Spring 仍为 7.0.9，只有 architecture 测试锁变化。随后输入验证误将只有 package-info 文档的模块视为必须产生类型，已排除 package-info/module-info-only 范围；真正声明类型但缺编译输出仍拒绝。失败原始日志保留，未记为 PASS。DocLint 随后捕获词法方法改名后未同步的 `@param text`，已更新为 `rawText`；定向 Javadoc 验证通过，再执行最终冻结聚合。隔离 owner 探针为 27 项，均通过；其含义是无效输入被对应工具拒绝、有效输入通过，原始失败进程并未被当作正常构建成功。第一次 lock 探针匹配器遗漏实际 `Dependency Locking` 错误文本；读原始失败原因后纠正结果匹配，未重跑构建、未修改工具。

曾尝试补充两份共享历史文档，当前输入复核正确拒绝 29 个旧目录 DAG（共享节点 bytes 改变）。随后只撤回本轮对冻结历史页的修改，把新说明放在本页和导航中；保存修改尝试与失败报告，不重签、不弱化 hash 校验。覆盖共享状态文档会造成验收级联成本，因此审查的当前状态与冻结验收正文分开维护。

当前结果如下；`PASS` 指该行明确限定的检查，不表示 G1 或产品交付通过。

| 当前执行 | 结果 | 核实内容 |
|---|---|---|
| `python3 -m scripts.toolchain.java_gradle clean deliveryFull --console=plain` | PASS | 29 秒，199 个 actionable task 全部执行；launcher 加入 `--rerun-tasks --no-build-cache`；两份 Boot jar 和所需报告实际生成 |
| Java JUnit | PASS | 36 个测试，failures / errors / skipped 均 0；含真实 API/worker context、源码规则和架构正反例 |
| launcher 合同测试 | PASS | 13 个 Python 工程测试；验证 Temurin 25、冻结 Wrapper、禁止覆盖和强制执行参数，不扫描 Java |
| Gradle / 工具探针 | PASS | 27 个 owner 正反例，加 1 个真实 product source selection 反例；无效源码和 XML 被各自负责的工具拒绝 |
| 自定义 Java 源码探针 | PASS | 原先 8 个漏检/误报反例全部纠正；7 个非法输入拒绝，合法转义文本块通过 |
| 当前 prerequisite DAG | PASS | 恢复本轮冻结文档修改后，29 个目录根完整复核通过，交付检查重新执行数 0；不覆盖本轮新 Java 代码 |
| 统一公开增量 Gate | FAIL | `missing-evidence-context: missing LEXIFLOW_GATE_EVIDENCE_PACKET`；上下文解析阶段拒绝，未执行 checker，不计作通过 |
| G1 / 第二阶段 | BLOCKED | 用户决定 PENDING，ARCH-0008 未签发，Phase 2 未激活 |

真实架构范围为 **2 个产品类型、0 个 domain 类型**。仅有 package-info 的模块是骨架；四类规则的拒绝能力由隔离测试证明，不把空业务范围的测试名字解释为已实现业务合规。JaCoCo 行覆盖率 86.14%、分支 73.00%、指令 84.37%，仅作为工程工具覆盖诊断，未设达标阈值，不能作为产品质量目标完成证据。

最终原始日志为 `final-delivery-verified.log`，归档索引为 `final-summary.json`；另含 `post-fix-source-probes.json`、`owner-probes-summary.json`、`language-selection-fixed.json`、`preserved-receipt-revalidation.json` 和公开 Gate 日志。它们都是本地审查证据，不是新增正式 receipt。失败构建和修改共享文档时的失败 DAG 也保留，未覆盖、未借用旧 PASS。

Java 完整构建、历史 Phase 1 prerequisite 收据、当前公开 Gate 结果和用户决定是四件独立的事。第一阶段 registry 目前没有 Java Gradle 选择和 adapter，因此本轮 Java 报告属于直接工程验证；统一 Gate 只验其冻结注册的工程合同。不得用 29 个文档/harness 收据来声称 Java 的本轮改动已获得正式独立验收。后续启用正式 Java 交付 Task 时，必须先接入一个 Gradle 聚合 adapter 及其冻结输入/报告绑定，review/catalog 只消费收据；本轮不新增重复扫描器或重签全量历史收据。

## 4. 第一阶段设计与后续边界

| 需要理解的内容 | 当前证据 | 当前不能声称的行为 |
|---|---|---|
| 六个用户要求的架构输出 | [需求追踪表](phase-1-user-requirement-traceability.md)、[决策包](g1-decision-package.md) | 没有进入 SQL、业务 endpoint、Prompt、Compose 或 Domain 产品实现 |
| 英文字幕独立渲染、Rules 决定是否提示、Models 决定语境 | [架构](../architecture/phase-1.md)、[横切合同](../architecture/phase-1-cross-cutting-contracts.md) | 没有实测字幕延迟或模型提示准确率 |
| 模块化单体、服务端 Profile、PostgreSQL 事实来源 | [十项 ADR](../architecture/decisions.md) 和所有权合同 | ADR 仍 Proposed；服务与跨设备事实同步尚未交付 |
| 最小 context、缓存可重建、脱敏与删除 | 横切合同及 [生命周期保证](../architecture/phase-1-lifecycle-guarantees.md) | 当前静态规则和测试 fixture 不证明真实用户数据删除、晚到结果 fencing 或服务故障恢复 |
| 工具重现 | 本机锁定构建及隔离执法探针 | 双环境、无依赖缓存、CI 重现未执行；按 [独立复现计划](../development/post-approval-toolchain-verification.md) 另行验收 |
| 子任务执行效率 | 本轮一个 Luna，callback 驱动、零进度查询；Qoder 为零 | 历史调度和耗时不能被本轮修复抹去；调用量不作为质量目标 |

先修工具实际缺陷，再记录证据和设计限制。本轮继续优化第一阶段；第二阶段保持未激活，ARCH-0008 的用户决定条件不因本次审查而改变。

## 第一阶段仍需保留的检查事项

1. 新 Java 交付有独立工程证明，正式 Task receipt 尚未签发。Java Task 接入统一验收时需一个 Gradle 聚合 adapter 与冻结报告绑定；当前 registry 的文档/harness PASS 不代替它。保持 Java 唯一执法，不再新增 Python 源码规则。
2. 历史页保留验收绑定，本页提供当前状态；后续修改架构/公共合同必须评估真正受影响的 Source 与 receipt，不因展示信息变化批量重签。
3. 十条 Proposed ADR、SLO/提示质量/成本和真实数据生命周期仍是设计与后续实验，不以本轮 fixture 或覆盖率“完成”它们；阶段范围保持第一阶段。
