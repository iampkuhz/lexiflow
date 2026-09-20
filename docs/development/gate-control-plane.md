# 1. Gate 控制面

Gate 区分本机自审、仓库健康与正式独立认证。三者共享已注册的确定性检查实现，但不共享身份结论或收据。

## 1.1. 直接入口

```text
python3 scripts/gates/change_context.py begin|update|show --paths <directory>...
python3 scripts/gates/change_verify.py --base <ref>     # 完成前最终 diff 复核
python3 scripts/gates/repository_verify.py doctor       # 只读环境诊断
python3 scripts/gates/repository_verify.py bootstrap --remediation-id <id>
python3 scripts/gates/repository_verify.py run          # 当前 checkout 的全局基线
python3 scripts/gates/certify_submit.py --task-id <id> --run-id <id> --confirm-scope-review <id>
python3 scripts/gates/formal_gate.py plan|run|status|doctor|prepare-issuer
```

`cli.py` 仅为旧调用方的兼容导入；新调用方必须使用上述按职责拆分的入口。

## 1.2. Change Verify

Change Verify 只在工作完成前帮助实现者复核最终 diff。可选的 change context 可以在分析、实现或完成时创建和更新，记录预期目录及调整历史；它不是变更锁、编辑权限或提交前置条件。

`--base` 优先；没有时使用上游分支的 merge-base；没有上游时使用 `HEAD`。结果总是输出实际采用的 base。它只按最终改动选择 `change-targeted` registry checks，并且只读取这些 checks 的输入闭包，不对无关 registry 路径做全局预检。结果包含：

- `execution_result`：选中检查的 `PASS`、`BLOCKED` 或 `FAIL`；
- `scope_review`：预期内、预期外或没有 context 的实际文件清单。

预期外改动只形成必须阅读的自审项，不改变 `execution_result`，不拒绝编辑或 commit。没有 registry 覆盖也会明确提示，不能称为已验证。Change Verify 产物位于 ignored `tmp/quality/change-verification/`，不构成正式 receipt。

## 1.3. Repository Verify 与 readiness

Repository Verify 不读取 change context、task diff 或 evidence packet。每次都执行 registry 标记为 `repository-baseline` 的全部适用确定性检查，并先验证 registry/profile/planning 的完整性。它是本机与 CI 的仓库健康反馈，不签发正式 receipt。

缺少 JDK、容器或受控运行时返回 `BLOCKED`，并带有 `blocking_scope: repository-readiness`、稳定的 `remediation_id`、`doctor` 和 `bootstrap` 命令。该状态归属于仓库准备度维护，不归因于当天迭代。`doctor` 只诊断；`bootstrap` 只显示受支持的显式准备动作，不在 Verify 内下载、安装或修改环境。输入/hash 冲突、registry 损坏和检查断言失败为 `FAIL`。

registry entry 的 `verification_scopes` 只能是 `change-targeted`、`repository-baseline` 或 `formal-only`。`registry_profiles --check` 是 Repository Verify 的完整性步骤，不再是 Change Verify 的全局前置。

## 1.4. Formal Gate

Formal Gate 仍是独立身份的三层不可变 receipt 链。产物执行者用 `certify_submit.py` 从已确认的 Change Verify 生成 subject evidence；这不会生成 issuer 或 validation receipt。`TASK_VALIDATION` 必须由不同真实 Codex task/session 运行，并在同一冻结输入上取得 Repository Verify `PASS` 后才能签发 `PASS` validation receipt。本地 Repository Verify 不能伪装为独立认证。

| 层 | 做什么 | 不做什么 |
|---|---|---|
| `TASK_VALIDATION` | 独立执行冻结交付 checks 与同一仓库基线，并写 validation receipt | 不接受实现者自签。 |
| `INDEPENDENT_REVIEW` | 消费冻结 diff、validation evidence 与 receipt | 不重跑 repository 或交付 checks。 |
| `CATALOG_DECISION` | 消费 validation/review/dependency receipt hash DAG | 不重跑 checks。 |

正式流程缺 subject、独立 issuer 或 runtime 为 `BLOCKED`；测试失败、身份/hash 冲突或冻结输入漂移为 `FAIL`。三层结果只有 `PASS`、`BLOCKED`、`FAIL`。

## 1.5. 提交与 CI

版本化 hook 如安装，仅输出不阻断提醒，默认不运行昂贵验证，也不写 change context。commit、`--no-verify` 和范围变化都不会被本机 Verify 拒绝。provider-neutral CI 应独立运行 `repository_verify.py run` 并保留其产物；正式 CI 认证还必须在独立 validator 环境调用 Formal Gate，不能以本地 session 规则替代 CI 身份隔离。
