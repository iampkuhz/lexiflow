# Qoder work-package 与 Harness 审计

> 审计日期：2026-09-16  
> 范围：`tmp/qoder-tasks/*/{task,started,completion,result}.json`、runner、machine policy 与 `.qoder/` 项目入口。未读取或解释 `stdout.log`、`stderr.log`。

## 结论

历史调度确实过碎。23 个 terminal run 对应 12 个 Qoder session，其中 12 个 initial、11 个 resume。旧 completion 没有结束时间，因此历史持续时间使用同一 run 的 `started.json` 与 `completion.json` 文件时间差估算：平均 6.97 分钟、中位数 4.21 分钟、14/23 少于 7 分钟、最长 30.90 分钟。

只有 6/23 run 存在结构化 `result.json`；其余 17 个即使 Qoder 退出 0，也没有 runner 强制的可验收结果。旧合同把 Qoder 直接绑定到 20–90 分钟原子 Task，并允许一个初始 run 加一次 resume，实际产生了大量短 session 和返工。

项目已有 `backend-implementer`、`extension-implementer`、`quality-verifier` 三个 Qoder agent profile，但旧 runner 没传 `qodercli --agent`。因此角色文件存在不等于执行时生效。旧 prompt 只给自由文本 `required_context` 和 `validation_command`，启动前不验证文件 hash、工具可执行性或命令入口。

## 已实施修复

- Qoder 调度单位改为 180–360 分钟、至少两个 Task、同 owner/contract 且具有完全相同 allowed/forbidden scope 与 file claims 的稳定 work package。180–360 分钟必须等于当前 catalog 内全部 `task_ids` 的 `estimated_task_minutes` 之和，不能由调用者虚报。Catalog Task 继续保持 20–90 分钟，作为 DAG 和逐项验收单位。
- 新 handoff 强制 `work_package_id`、ordered `task_ids`、版本 maps、`estimated_minutes`、`agent_profile` 与 `harness_manifest`。
- runner 使用 `--agent <profile> --setting-sources project`，并继续 `--disallowed-tools Agent`。
- `lexiflow.qoder-harness.v1` 在派发锁内、创建 run 前检查：package identity、当前 catalog 的 owner/版本/估时总和/写域集合、AGENTS/profile/catalog/机器策略上下文 SHA-256、真实工具 version probe，以及无 shell validation argv、cwd、timeout 与 executable。
- `qoder_task.py preflight --task ...` 可在不创建 run 的情况下执行相同的 catalog、profile、hash、工具与命令入口检查；`start` 仍会再次检查，不能用旧 preflight 绕过输入漂移。
- Qoder 必须写 `lexiflow.qoder-work-package-result.v1`，结果包含每个 Task 的有序 outcome。退出 0 但缺少或身份不匹配的 result 时 completion 为 `failed`。
- 新 completion 精确写 `started_at`、`finished_at`、`duration_seconds` 与原始 `qoder_exit_code`；不再依赖文件时间分析新 run。
- `result` 命令只暴露结构化结果 locator/status，不再返回 stdout tail；回调只包含 work package 身份和 run 目录。

## 当前验证

- `python3 -m unittest discover -s tests/harness -p 'test_*.py'`：26 tests PASS。
- 真实本机 harness preflight：9 个 hash-bound context、Python 3.12、ripgrep、qodercli 三个 probe 与一条 fixed-argv validation 入口均通过，证据位于 `tmp/quality/qoder-harness-selftest/preflight-result.json`。
- 当前 catalog 中可同时满足“同 owner、完全相同 scope/claims、估时总和 180–360 分钟”的未建模工作包数量为 0。因此没有为展示功能而虚构估时或启动新的 Qoder；后续产品工作必须先把一组真实的同写域 Task 设计成合格 package，再通过 `preflight` 和 `start` 派发。
