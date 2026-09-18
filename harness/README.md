# LexiFlow Harness

`harness/` 只保存静态、机器可读的仓库约束：

- `manifest.yaml`：项目类型、公开命令与文档入口。
- `agent-runtime.manifest.yaml`：客户端拥有 Session/checkout 的最小运行契约。
- `agent-policy.manifest.yaml`：隐私、Git、子任务、Qoder 调度和结果语义。
- `gate-check-registry.yaml`：按 catalog 顺序冻结的 fixed-command 检查清单。
- `g1-task-contract-profiles.yaml`：G1 文档型任务的封闭输入与语义断言；不包含自由命令。

`python3 -m scripts.gates.registry_profiles --root . --check` 验证 profile、catalog 与
registry 的确定性投影。`python3 -m scripts.gates.task_contracts --task-id <exact-id>`
只接受 profile 中的稳定 Task；`LF-TSK-ARCH-0008` 在缺少精确用户批准标记时返回
`BLOCKED`。

运行证据写入 ignored 的 `tmp/quality/runs/<run-id>/`；Qoder 任务写入
`tmp/qoder-tasks/<run-id>/`。Harness 不保存产品运行状态，也不把 active change 当成普通写入的权限令牌。

## 共享策略与文档治理

`agent-policy.manifest.yaml` 是共享调度策略真源；`policy-projections.yaml` 只描述兼容字段的投影位置。runtime、任务模板和 catalog 的字段形状保持兼容，修改共享值后显式执行：

```bash
python3 -m scripts.harness.policy_projection --write
python3 -m scripts.harness.policy_projection --check
python3 -m scripts.harness.docs_check
```

`--write` 只更新声明的投影字段，保留其他手写内容；`--check` 不修改文件。文档检查验证链接、锚点、图源围栏、入口大小和 docs 标题的层级十进制序号；重排标题时保留仍被引用的旧锚点。它要求父页保留核心模型和边界；仅在细节确有必要时放入同名子目录，且不规定子页数量、篇幅或模板。候选方案、方案比较和 A/B/C 选项不能进入 docs，文档只陈述已确定的方案供校验。它不替代中文语义、图文一致性或视觉审查。

## 本机 skill 接入

`documentation-policy.yaml` 声明两个按需技能。只从 `CODEX_HOME/skills`（默认用户主目录下的 `.codex/skills`）引用现有安装，不复制实现、不自动下载、不修改用户配置：

```bash
python3 -m scripts.harness.local_skills check
python3 -m scripts.harness.local_skills link
```

可用 `--source` 显式指定安装根目录；绝对路径只出现在本地检查结果中，不写入共享清单。逐 skill 链接位于忽略的 `.agents/skills/`。同目标重复执行不改变内容，已有不同目标拒绝覆盖，缺源返回 `BLOCKED`。Codex 发现这些链接后按需读取 `SKILL.md`；Qoder 通过项目入口显式读取，不依赖自动发现。

修改图先读 PlantUML skill；仅维护技能本身才读治理 skill。图源最终始终在 Markdown 内，但必须按 `documentation-policy.yaml.diagram_workflow` 操作：先在 `tmp/diagrams/<document-slug>/<diagram-id>/source.puml` 调试，校验、渲染并人工查看，再将**已校验的完整内容原样复制**到指定 `plantuml` 围栏，最后核对该围栏的来源哈希。图源改变就重新从临时源码校验；不得直接把未校验草稿写入正文。

`tmp/diagrams/` 保存 PUML 草稿、brief、SVG/PNG、回执和预览，均不提交。`docs/**/diagrams/` 不再用作调试目录；其中保留的 Markdown 索引只做导航。IDEA 设置中启用 Markdown 的 PlantUML 扩展，在正文预览核对图像；独立 PUML 预览不构成正文验收。

文档只维护每个主题的最新版：直接更新正文，合并后删除旧页；不建立历史目录、日期快照或复审副本。阶段状态只在状态页维护，规则和阈值只在指定真源维护。原始运行收据仍不可变，留在忽略目录，不转换为 docs 历史页。
