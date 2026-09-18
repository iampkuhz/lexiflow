# 1. Gate 控制面：接口与计划

## 1.1. 唯一入口

```text
python3 scripts/gates/cli.py plan --mode incremental|full --evidence-packet <repo-relative-path> --issuer-packet <repo-relative-path>
python3 scripts/gates/cli.py run  --mode incremental|full --evidence-packet <repo-relative-path> --issuer-packet <repo-relative-path>
python3 scripts/gates/cli.py status --run-id <uuid>
```

参数与环境变量同时提供时必须逐字一致；缺少、冲突或不是单一仓库相对路径均为 `FAIL`。不从 `latest`、目录扫描、文件时间或进程身份补全证据定位。

## 1.2. 计划不变量

`plan` 和 `run` 必须调用同一个无副作用 `compile_plan`。相同模式、receipt kind、目录、策略和两个证据包产生相同规范计划与 `content_fingerprint`；每次 `run` 产生新的 `run_id`。指纹不包含生成时间、输出位置或运行身份。

计划冻结任务/变更、原始范围与规范声明、证据包和签发者哈希、选择的固定检查及其注册表来源。任何冻结输入漂移均为 `FAIL/input-drift`。

## 1.3. 注册表边界

只有显式注册表中的检查可由 `run` 调用；检查选择来自冻结计划，而非调用者字符串。`plan` 不写运行产物也不执行检查；`status` 只读取指定 `run_id` 的固定产物，不等待、不重试、不重新判定。
