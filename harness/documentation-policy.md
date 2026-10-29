# 1. 文档维护规则

## 1.1. 唯一正文与读者路径

每项事实只有唯一正文，父页保留目的、核心模型、边界与确定结论，不重复子页细节。生命周期从 `docs/README.md` 导航；系统事实在 `docs/system.md` 与 `docs/system/`，读者任务在 `docs/scenarios/`，参数和入口在 `docs/reference.md` 与 `docs/reference/`，路线图和用户内容分别在 `docs/roadmap/`、`docs/user/`。工程规则仅在 `AGENTS.md` 与 `harness/`，执行状态仅写状态页。

按任务下钻真实入口、输入输出和失败去向，Reference 按需查阅而非必经步骤。区分执行入口、内部库、机器声明和人工步骤，声明不是执行步骤。只有步骤、字段、命令、示例或诊断确需展开时才拆同名子目录；不设机械模板、数量或篇幅，细节页不复制父页背景。

## 1.2. 语言与已确定结论

中文承担叙述与连接，Harness、Quality Gate 等规范英文术语保持原样；命令、路径、API、标识符、schema 字段、状态固定值与来源示例不得翻译或改写。Markdown 标题按层级使用十进制序号，重排时保留仍被引用的旧锚点。

工程对人说明统一使用中文：Git commit 的主题与正文、PR 标题与正文及审查说明、工作流／运行／任务／步骤的可见名称、手动操作表单说明和发行说明。技术专名及 Conventional Commits 的 type/scope 等标识可保留英文，但实际说明须为中文，准确陈述改动与验证边界。此规则适用于所有 agent 和人工工程入口，不绑定客户端。 面向用户的进度与交付使用“开发检查、正式验收、独立审查、发布”等中文表述，并说明验证对象；不把正式验收流程简称为单元测试。命令、标识符、schema 和原始日志中的英文保持不变。可编辑的既有 PR 文案可更新；不得仅为翻译而重写历史 commit、移动标签、改写已运行流水线快照或冻结验收证据。平台自动文案与外部工具原始日志保留原文，不冒称已全面中文化。

文档陈述已经确定的最佳方案及其边界，不将候选清单、比较表或 A/B/C 未决选项转嫁给读者。需要重新决策时先在 OpenSpec 变更中完成决策，再更新唯一结论。每个主题只维护最新版，直接更新、合并或删除旧页，不建历史目录、日期快照或复审副本；版本比较仅限路径与标题均明确标记 comparison/diff 的页面。原始运行收据与日志留在 ignored 目录且不可改写，历史通过结果不是最新版验收。

## 1.3. 图源工作流与审阅责任

新建或修改图源，以及执行图表校验前，读取本机 `feipi-plantuml-generate-diagram/SKILL.md`。先在 ignored `tmp/diagrams/` 写 draft，按 skill 校验、渲染，将 brief、回执与预览留在同一临时目录；人工核对中文、箭头、分支、终态和布局，无法预览则记 `BLOCKED`。将已校验 draft 完整原样复制为目标 Markdown 的 fenced `plantuml`，复核定界符及来源哈希；图源变化后重新走 draft 流程，不直接改正文图源。

图源只在 Markdown 正文；禁止提交或从正文链接 PUML、SVG、PNG 调试产物。`docs/**/diagrams/` 的已有 Markdown 索引仅作导航，不存放新的图源、brief、回执或预览。`docs_check` 只做固定静态检查，不证明中文语义、图像布局或正式 Gate 验收。

## 1.4. 本机 skill 边界

仅在创建、重构或审计 skill 本身时读取 `feipi-skill-govern/SKILL.md`；普通文档维护不调用 skill 治理。两个受支持 skill 的源由 `CODEX_HOME/skills` 解析，默认 `~/.codex/skills`。`local_skills check` 只读，`link` 显式创建 ignored `.agents/skills` 链接；缺源 `BLOCKED`，不同目标拒绝覆盖，不下载、不修改上游或本机配置。工具可用性不能代替遵守相应 skill 或语义审阅。
