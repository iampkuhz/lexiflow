# 1. 校验：收据与验收

## 1.1. 取得冻结提交

正式验收从已经 `PASS` 的 Change Verify run 开始：

```bash
python3 -m scripts.acceptance submit --task-id <id> --change-report-id <uuid> --confirm-scope-report-id <uuid> [--producer-run-id <uuid>]
```

`submit` 自动读取 catalog 中的 Task/version/dependencies、该报告、模块声明、完整检查输入闭包、改动快照和 producer 来源。调用者不能传 actor、session、descriptor 或 Task version。可选 `producer-run-id` 只是经核验的 Codex/Qoder 原始运行引用；没有它时当前真实 Codex task 是 producer。缺少、过期或漂移的来源不能退化为默认值。

## 1.2. 独立验证与审查

```bash
python3 -m scripts.acceptance validate --submission-id <uuid>
python3 -m scripts.acceptance review --submission-id <uuid> --validation-id <uuid> --findings-json <path> --decision PASS
python3 -m scripts.acceptance check --submission-id <uuid>
```

`validate` 必须在与 producer 不同的真实 Codex task/session 中运行，并直接调用 `scripts.verification` 公共 API。必需检查未执行、跳过、覆盖缺口、来源/配置/快照漂移或环境失败都不能返回 `PASS`。`review` 要有明确的非空审查意见，且 reviewer 不能是 producer 或 validator；`review` 和 `check` 都不启动交付命令。

## 1.3. 记录与结论

每个 submission、validation、review 和 check record 在 `tmp/quality/acceptance/` 使用 UUID、内容 SHA256、无重复键 JSON 与一次性原子发布。签发时 authority 必须是当前可信本机来源；历史 record 不因任意十五分钟 TTL 自动失效，但绑定来源、任务要求或内容漂移会失效。`check` 只读取并核对这条 hash DAG 及当前依赖闭包；未知 Task、缺记录、多条竞争记录、路径异常、hash 不一致、依赖版本变化或传递依赖 hash 更新均 fail closed。需要显式用户批准的 Task 还必须有用户维护的 approval record，精确绑定 Gate、依赖 Task/version 与 check hash；系统不自动生成批准。

缺少依赖或 approval 属于可后补外部条件：条件核对返回 `BLOCKED`，不会抢先写入一个使同一 submission 永久失败的 check record。条件满足后的首个 `PASS` 原子发布；后续调用只在完整当前性重验通过时幂等返回该记录。

工程 fixture 可以证明实现链路，不能代替生产所需的独立真实 session。`PASS`、`BLOCKED` 与 `FAIL` 是唯一结果。
