# LexiFlow Qoder Entry

读取并遵守仓库根 `AGENTS.md`、handoff 指定的 `harness_manifest` 和绑定的项目 agent profile。按 `task_ids` 从清单已绑定哈希的 `planning/workstreams.yaml` 读取每个 Task 的 deliverable、验收、验证命令和文件声明；不得只完成 anchor Task。一个 session 完成一个 catalog 估时总和为 180–360 分钟、至少覆盖两个同 owner/contract/write scope Task 的 work package；为每个 Task 保存独立 outcome。禁止递归派发 Agent、自动 Git mutation、读取或提交用户数据和本地配置。完成后先在 run 目录写 `lexiflow.qoder-work-package-result.v1` 的 `result.json`，再发送紧凑回调。进程退出 0 不代表主 Agent 验收通过。
