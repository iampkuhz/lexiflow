# LexiFlow Qoder 入口

读取仓库根 AGENTS.md、harness/agent-policy.manifest.yaml、交接绑定的 `harness_manifest` 与项目角色配置。按精确 `task_ids` 读取目录中每项任务，不只交付主任务。Catalog 存在时用于补充核对，核心 handoff 的范围、验收、验证和规则上下文才是执行边界。遵守共享范围、身份、验证、禁止递归委派及 Git 护栏；结果先落盘再向精确父会话回调。退出零不代表验收通过。需要文档技能时按 harness/documentation-policy.yaml 声明显式读取本机链接的 `SKILL.md`；不得假定 Qoder 会自动发现 Codex 的技能。
