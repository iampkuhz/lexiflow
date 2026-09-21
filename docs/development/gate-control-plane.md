# 1. Gate 控制面

Gate 区分本机自审、仓库健康与正式独立认证。三者共享已注册的确定性检查实现，但不共享身份结论或收据。

## 1.1. 直接入口

```text
python3 scripts/check_changes.py --base <ref> --expected-path <directory>  # 最终 diff 复核
python3 scripts/check_repository.py                                  # 当前 checkout 的全局基线
python3 -m scripts.acceptance submit --task-id <id> --change-report-id <uuid> --confirm-scope-report-id <uuid> [--producer-run-id <uuid>]
python3 -m scripts.acceptance validate --submission-id <uuid>
python3 -m scripts.acceptance review --submission-id <uuid> --validation-id <uuid> --findings-json <path> --decision PASS|BLOCKED|FAIL
python3 -m scripts.acceptance check --submission-id <uuid>
python3 -m scripts.acceptance status --submission-id <uuid>
```

不存在聚合 CLI；每个入口只处理一种明确场景。
仓库术语的统一定义见[仓库术语表](repository-glossary.md)。

## 1.2. Change Verify

Change Verify 只在工作完成前帮助实现者复核最终 diff。可选的 change context 可以在分析、实现或完成时创建和更新，记录预期目录及调整历史；它不是变更锁、编辑权限或提交前置条件。

`--base` 优先；没有时使用上游分支的 merge-base；没有上游时使用 `HEAD`。结果总是输出实际采用的 base。它只按最终改动选择 `change-targeted` 模块检查，并且只读取这些检查的输入闭包，不对无关模块做全局预检。结果包含：

- `execution_result`：选中检查的 `PASS`、`BLOCKED` 或 `FAIL`；
- `scope_review`：预期内、预期外或没有 context 的实际文件清单。

预期外改动只形成必须阅读的自审项，不改变 `execution_result`，不拒绝编辑或 commit。没有模块检查覆盖也会明确提示，不能称为已验证。Change Verify 的不可变报告位于 ignored `tmp/quality/verification-reports/`，不构成正式 receipt。

## 1.3. Repository Verify 与 readiness

Repository Verify 不读取 Task、身份或验收记录。它执行模块声明中 `repository-baseline` 的全部检查；正式验证可在同一公共 API 上合并 catalog 已固定的必需检查，并按命令、配置和输入闭包去重。它是本机与 CI 的仓库健康反馈，不签发正式 receipt。

缺少 JDK、隔离 PostgreSQL/Redis 测试目标或其他受控运行时返回 `BLOCKED`，并列出具体
`required_environment` 缺项。该状态归属于仓库准备度维护，不归因于产品断言；准备动作必须显式执行，
Verify 不下载、安装或修改环境。输入/hash 冲突、模块声明损坏和检查断言失败为 `FAIL`。

模块检查声明的 scope 只表达 `change-targeted` 或 `repository-baseline`；Task 条件只由正式验收读取，不能反向阻塞普通模块检查。

## 1.4. 正式验收

正式验收由 `scripts.acceptance` 的四个具名场景完成。`submit` 只接受已持久化的 PASS 日常变更报告，自动从 catalog 绑定 Task/version/dependencies、冻结的报告、模块检查声明、改动文件快照和真实 producer 来源；日常报告不包含也不推断 Task identity。委派实现可只传一个 `producer-run-id`，验收会从经过验证的 Codex/Qoder 原始完成记录解析 worker 身份并绑定摘要，调用者不能手写身份或 descriptor。

`validate` 必须在不同真实 Codex task/session 中运行。它先验证不可变 submission、任务来源、冻结 artifacts 和快照，再直接调用 `scripts.verification.verify_repository`。任何未执行、跳过、覆盖缺口、配置漂移或环境失败都不能产生 `PASS`。`review` 必须提供真实、非空的审查意见，且 reviewer 与 producer、validator 均独立；它不启动交付命令。`check` 只读取 validation/review/dependency records，验证内容哈希 DAG 与当前依赖闭包，不启动交付命令。

记录在 `tmp/quality/acceptance/` 原子、一次性发布，记录 ID 只能是 UUID；重复 JSON key、非普通文件、symlink、竞态覆盖和摘要漂移均 fail closed。authority 在签发时由当前本机 Codex 来源验证，历史记录不采用任意短 TTL；消费时重验已绑定来源和内容。需要用户批准的阶段入口只有在显式 approval record 精确绑定依赖版本与 check hash 时才可通过。工程 fixture 可以验证四场景链路，但不会伪造生产独立正式结论。

`review` 与 `check` 不执行交付命令，但会只读重算完整 frozen input closure；validation 后任何未改动 baseline 输入或声明漂移都不能继续通过。依赖或用户批准尚未到位时，`check` 返回 `BLOCKED` 且不发布永久终态；条件补齐后可对同一 submission 重核。成功 check 只发布一次，重复查询在重验当前条件后返回同一不可变记录。

## 1.5. 提交与 CI

版本化 hook 如安装，仅输出不阻断提醒，默认不运行昂贵验证，也不写 change context。commit、`--no-verify` 和范围变化都不会被本机 Verify 拒绝。provider-neutral CI 应独立运行 `scripts/check_repository.py` 并保留其产物；正式 CI 验收仍必须在独立 validator 环境调用 `scripts.acceptance validate`，不能以本地 session 规则替代身份隔离。
