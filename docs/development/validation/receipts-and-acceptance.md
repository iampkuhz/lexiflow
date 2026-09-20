# 1. 校验：收据与验收

## 1.1. 取得当前上下文

正式 Gate 只消费明确的 evidence packet 和 issuer packet。执行者先确认 Change Verify 的 scope review，再用 `certify_submit.py` 生成 subject evidence；不得从 `latest`、目录扫描、时间或历史输出推断它们。

## 1.2. 执行与读取

```bash
python3 scripts/gates/formal_gate.py plan --mode incremental --evidence-packet <path> --issuer-packet <path>
python3 scripts/gates/formal_gate.py run --mode incremental --evidence-packet <path> --issuer-packet <path>
```

`plan` 是纯编译；`run` 才创建新的运行产物。独立 TASK_VALIDATION 会在同一冻结输入调用 Repository Verify，只有其 `PASS` 才能发布 validation `PASS`。读取该 `run_id` 下实际计划、开始事件和最终收据，核对检查是否执行、输入是否漂移、基线结果、三态及每个原因。没有真实 packet 时不要构造示例路径后声称结果。

## 1.3. 分层验收

`TASK_VALIDATION` 执行交付检查；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证收据和哈希 DAG。三层各使用当前授权身份和新鲜输入，不能重用他层的执行结果或自行签发身份。详见[Gate 控制面](../gate-control-plane.md)。

## 1.4. 结论

缺上下文、签发者或必需收据为 `BLOCKED`；哈希、身份、输入或检查结果不一致为 `FAIL`。只有所需层的真实收据均为 `PASS`，才称相应验收范围通过。
