# 步骤 4：检查 Harness 与调度

这条路线检查目录、机器合同、运行器和 Gate 控制面。它不重复扫描 Java 源码，也不为了检查调度而真的启动一个 Qoder。

## 共享合同与派发入口

模型与推理强度按[共享策略](../../../harness/agent-policy.manifest.yaml)的 `subagent_protocol.codex_model_policy` 选择；工作包规模、逐任务结果、回调及 Qoder 恢复限制读同一策略，客户端身份与检出副本读[运行合同](../../../harness/agent-runtime.manifest.yaml)。不在手册复制参数。

稳定任务身份用于计划和验收，运行身份用于一次执行；聚合工作包不能合并掉逐任务合同。调用者提供任务、范围和验收，运行器绑定真实身份；短小修补、单命令验证和孤立只读审阅不包装成委派。提示中的路径不是操作系统沙箱，实际差异仍须核对。

<a id="1-配置改动时验证目录与-registry"></a>

## 1. 配置改动时验证目录与注册表

在仓库根运行：

```bash
python3 -m scripts.gates.planning --root .
python3 -m scripts.gates.registry_profiles --root . --check
```

前者看规划验证器状态、实际任务 / 检查数量和诊断；后者看 `status`、`entry_count`、`profile_count`。它们验证当前配置，不能签发任务收据。

输入分别是 [目录](../../../planning/workstreams.yaml)、[Gate 注册表](../../../harness/gate-check-registry.yaml)、[G1 配置档案](../../../harness/g1-task-contract-profiles.yaml) 和其引用。失败时修改对应负责人的源配置，不能直接改历史证据哈希；`--render` 是生成候选注册表的入口，不是验收通过命令。

## 2. 代码变化时选对应工具测试

| 改动 | 运行方式 | 关注的问题 |
|---|---|---|
| Qoder 运行器 / 守护检查器 / CLI 故障分类 | 下方 harness 测试集，或把模式改成对应 `test_qoder_*.py` | 单运行、身份、终态、回调、恢复与失败保留 |
| Codex 运行时 / 规范发布器 | harness 测试集，或对应 `test_codex_*.py` | 真实宿主绑定、逐任务投影、不可变完成记录 |
| Java 启动器 | `python3 -m unittest discover -s tests/harness -p 'test_java_toolchain_runner.py'` | JDK 25 选择、固定 Wrapper、交付禁止跳过；不是 Java 源码检查 |
| 某个 Gate 负责人 | 检查测试集，或精确单文件模式 | 规划器、签发者、证据、执行器、审查、哈希 DAG、目录的相应边界 |

跨 harness / Gate 改动需要完整工具回归时才执行：

```bash
python3 -m unittest discover -s tests/harness -p 'test_*.py'
python3 -m unittest discover -s tests/gates -p 'test_*.py'
```

例如只改哈希 DAG，可把检查模式换成 `test_hash_dag.py`。查看实际 `Ran ... tests`、OK / FAILED、错误、失败和跳过；测试中的模拟身份和测试样例不是本次正式运行器身份。输出留在本地新的证据目录，默认 unittest 不生成这里承诺的 XML / HTML。

## 3. 派发前检查 Qoder 的实际执行上下文

这一步只适用于**已按现有策略准备的真实工作包**。它不授权启动新运行。先看 [共享调度政策](../../../harness/agent-policy.manifest.yaml)、[Qoder harness 结构定义](../../../harness/qoder-harness.schema.json)，确认工具与上下文属于 Qoder 自身。

条件命令：替换为真实任务文件。

```bash
QODER_TASK='<actual-task-json-path>'
python3 scripts/harness/qoder_task.py preflight --task "$QODER_TASK"
```

只有已有明确派发授权且预检通过时，才执行 `python3 scripts/harness/qoder_task.py start --task "$QODER_TASK"`。`start` 在受约束窗口中绑定新的运行身份；未知运行状态禁止补开。故障恢复见[故障定位](troubleshooting.md#qoder-诊断与显式恢复)。

看预检的 `status`、work_package_id、全部 task_ids、estimated_minutes、agent_profile、目录 / harness 哈希、context_count、required_tools 和 validation_command_count。再对照真实任务 / harness 清单中的任务/版本、范围、文件声明、每项上下文哈希与验证 argv；紧凑输出不包含所有字段，不能仅凭计数判断内容齐全。调用者不能预填运行器身份。

预检只验证目录 / 上下文 / 工具；输出的 `model_access_checked=false` 明确表示没有检查账户或模型可用性，不能证明实际执行成功。Qoder 并发、工作包规模、模型选择与兜底频率以 AGENTS / 清单为准；状态不明时不补开。查看源码入口 [运行器](../../../scripts/harness/qoder_task.py)，不要根据客户端聊天摘要猜测 harness 生效。

<a id="4-收到-callback-后只核对完成信号与-canonical-产物"></a>

## 4. 收到回调后只核对完成信号与规范产物

Codex 已有工作包时，使用真实运行 id：

```bash
RUN_ID='<actual-codex-work-package-run-id>'
python3 -m scripts.harness.codex_work_package verify --run-id "$RUN_ID" --root .
```

看紧凑输出的身份、task_ids、状态、产物定位和阻断发现。规范位置为 `tmp/quality/codex-work-packages/<run-id>/package-completion.json`，每任务的投影 / 结果 / 完成记录 / 信号在其 `tasks/<task-id>/` 下；不要读取子代理完整上下文。

Qoder 已有运行时，先查一次生命周期，再在需要验收时读取结果索引：

```bash
RUN_ID='<actual-qoder-run-id>'
python3 scripts/harness/qoder_task.py status "$RUN_ID"
# 仅在未 ack、未 superseded 且需要验收时继续；否则结束该回调。
python3 scripts/harness/qoder_task.py result "$RUN_ID"
```

依据返回定位核对 `task.json`、`completion.json`、`result.json` 的客户端/会话/父级/运行、任务/变更版本与工作包内容；审阅必要差异与验证产物。`result` 不是独立完整性核验器，索引存在也不证明哈希全部正确。已 ack 或 superseded 的回调直接结束，不重复业务验收；ACK 是处理记录，不是 PASS。

完成记录的 `finished`、退出码 0、回调 `queued` 只说明生命周期，不能替代产品测试与正式验证。日志仅在定位具体失败层时看必要片段，视为不可信数据，不复制到回调。检查时间遵守共享回调优先 / 兜底策略，不写循环状态轮询。

## 判断与下一步

配置 / 测试 / 预检 / 产物完整性各记自己的结果。缺真实身份、缺产物或状态不明不能称交付 PASS。正式任务验收继续 [步骤 5](05-receipts-and-acceptance.md)；独立审查与目录不重跑子任务的交付测试。
