# 1. 派发预检：冻结合同

## 1.1. 输入与输出

`check_dispatch(candidate_view, catalog_snapshot, active_instances)` 只消费调用方冻结的 JSON：候选任务、同版本目录快照和调度窗口内全部可写活动实例。结果保留三态、稳定原因码、规范声明和三份输入指纹；不复制敏感业务内容或完整模型载荷。

候选和活动实例都必须与目录的任务 ID、版本、描述符哈希、负责人、`allowed_files`、写声明和 `produced_contracts` 对账。缺字段、格式错误、身份/版本不符或活动快照声明缺失为 `FAIL`。

## 1.2. 路径与 owner

路径只接受仓库相对 POSIX 的精确路径、末尾 `/*` 和末尾 `/**`。拒绝绝对路径、`..`、反斜线、控制字符、重叠/重复声明、符号链接或大小写歧义。允许范围与 `file_claims` 必须逐项相同，并归单一最具体 owner；跨 owner、无 owner 或与禁止范围冲突不能静默缩窄。

## 1.3. 判定

候选与任一活动写声明有符号交集为 `BLOCKED/write-overlap`；不同任务同时生产同一公开合同为 `BLOCKED/contract-writer-conflict`。无法证明路径安全或快照完整性为 `FAIL`。全部声明一致且无冲突才为 `PASS`，且只对提交快照和当前调度窗口成立。
