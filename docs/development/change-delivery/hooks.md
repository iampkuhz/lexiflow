# 1. 交付 Hook

> 位置：[开发交付](../change-delivery.md) → 交付 Hook。Hook 是执行入口，不是独立 Formal Gate，也不是 Git 提交锁。

## 1.1. 先看触发、执行与证明边界

**Hook 是客户端触发器，不是独立验收。** 完整活动图及失败分支只在[执行门禁场景 B](../scenarios/run-repository-gates.md)维护；本页查协议、客户端配置和恢复。

| 环节 | 实际入口／职责 | 深入位置 |
| --- | --- | --- |
| 客户端 Stop | Codex／Qoder 配置调用同一入口 | [.codex/hooks.json](../../../.codex/hooks.json)、[.qoder/settings.json](../../../.qoder/settings.json) |
| 判断回合与输出协议 | [delivery_hook.py](../../../scripts/verification/delivery_hook.py) 判断中间标记／diff／停用／重入状态 | 1.2 与 1.3；中间回合不代表 PASS |
| 风险检查与服务 | 开发 Verify API 串行执行 profiles，环境模块一次准备并清理 lease | [执行场景 B](../scenarios/run-repository-gates.md)、[隔离环境](../operations/verification-environment.md) |
| 正式验收／CI | 不由 Hook 代签；Git Hook 只提醒 | [Delivery Gate](delivery-gate.md)、[CI](../operations/github-actions.md) |

规则只在 [AGENTS.md](../../../AGENTS.md)／[Harness](../../../harness/README.md)，客户端不复制检查实现。只注册 Stop，不在编辑、每次工具调用或 commit 时扫描。

## 1.2. 交付时实际做什么

正常 Stop 在一个验证窗口内准备隔离环境，经开发验证 API 串行执行风险所需 profiles，再清理本次资源；不经 shell 另起一次开发 CLI。活动顺序、每步预期及样本依据只在[执行场景 B](../scenarios/run-repository-gates.md)维护；检查选择和语言工具见[Verify](verification.md)。

| 入口条件 | 实际处理 | 输出含义 |
| --- | --- | --- |
| 非 Stop 事件 | 不调用开发检查 | 空响应，不签 PASS |
| 显式停用 | 不调用开发检查 | BLOCKED／未验证，不豁免交付要求 |
| 精确的末行中间标记 | 不调用开发检查 | 中间回合，不签 PASS |
| 无待检 diff | 不调用开发检查 | 无待检变更，不签 PASS |
| 正常 Stop 且存在 diff | 取得非等待窗口，再执行 B | 开发报告，不是正式验收 |

不合法的 JSON、策略或读取失败属于 BLOCKED，不能归入“无需检查”。条件判断次序以 [evaluate()](../../../scripts/verification/delivery_hook.py) 为准。

[Codex Stop 协议](https://learn.chatgpt.com/docs/hooks) 的含义是“本轮结束”，不是“整个任务完成”。因此，中间汇报、提问、等待用户、Qoder callback 交接以及只读 review/catalog 回合，必须在最后单独加一行：

```text
<!-- lexiflow:intermediate -->
```

该标记只表达尚未交付，不能与交付 PASS 声明一起使用。Hook 只比较消息末行，不读取 transcript、不保存消息。普通交付不要加此标记。没有消息字段的手工 Stop 也执行检查。只注册 Stop，不注册工具事件或 SubagentStop。

## 1.3. 失败、进度与恢复

- stdout 只输出客户端协议 JSON；stderr 立即打印环境准备、阶段与命令，运行中每 30 秒显示耗时。Verify 返回后逐项显示真实状态，包括 `Java/Gradle deliveryFull PASS`；缺项显示 BLOCKED 和未执行原因，而不把命令退出 0 当 PASS。宿主可能收集而非实时展示 stderr，终端直接执行可实时看到。
- 成功：返回真实 PASS 和报告路径。首次 FAIL/BLOCKED：Codex `decision=block` / Qoder `decision=deny` 请求一次修复续轮，包含失败 Check、原因和日志。再次失败：`continue=false` 停止自动重试并明确失败；停止不等于通过。手工执行应看 JSON 中结果，而不是把协议进程退出 0 当验证 PASS。
- 每项检查保留 Registry 超时；运行最多 1800 秒，客户端上限 1860 秒预留清理余量。超时、Ctrl-C、SIGTERM 回收当前 Check 进程组及本次测试容器；不把 SIGKILL 当正常清理。
- 同 checkout 的公开 Verify、Hook、工作包和 Formal validation 使用同一非等待窗口；已有检查时立即 BLOCKED，不重跑、不排无限队列。窗口不锁源码编辑，其他写入者仍可编辑；实际风险闭包漂移使本次失败，无关写入可继续。未知锁不删除、未知进程不终止。
- 不跨交付缓存 PASS，不以旧报告代替真实执行。进程退出自动释放锁；不要删除锁文件“解锁”。
- Hook 按需创建本次隔离 PostgreSQL/Redis 测试容器，检查结束自动清理；不再要求手工导出地址。运行时或本机镜像缺失则 BLOCKED，不自动安装、下载、启动容器引擎或使用开发库。准备方式见[验证环境](../operations/verification-environment.md)。

入口优先使用已准备的 `.local/lexiflow-python/bin/python`，没有则使用当前解释器。临时测试服务由环境模块按 `harness/test-services.json` 管理，profile 执行共用一个 lease。

紧急退出可以直接把 `harness/delivery-hooks.json` 改为 `{"mode":"off"}`，或在宿主启动环境设置 `LEXIFLOW_DELIVERY_HOOK_DISABLE=1`。关闭不需要先通过门禁，输出明确为未验证，不豁免交付检查。也可以仅移除本项目客户端 Stop 入口，不改其他项目或全局配置。当前禁止新的 Qoder agent 派发与 Qoder Hook 的 Stop 触发是不同边界：前者不表示已停用 Qoder 客户端的本项目 Stop Hook。

## 1.4. 双客户端配置与手工执行

Codex 使用 `.codex/hooks.json`；Qoder IDE/CLI 使用 `.qoder/settings.json`，参见 [Qoder Hooks](https://docs.qoder.com/cli/hooks)。两者调用同一 Python 入口；Qoder 仅传 `--client qoder` 适配输出协议，不复制检查实现。首次加载或命令变化需要在实际宿主中审阅/信任；Qoder 需重新加载会话。命令夹具通过不证明宿主已加载配置，不自动修改用户信任设置。

在仓库根目录执行真实交付检查：

```bash
printf '%s\n' '{"hook_event_name":"Stop","stop_hook_active":false}' | python3 scripts/verification/delivery_hook.py
```

命令中的引号不要手工加 JSON 展示用的反斜杠。裸运行不输入 JSON 会立即诊断 TTY；管道未关闭在 3 秒后明确返回错误，不无限等待 EOF。

入口放在 Verification 模块，因为它拥有跨模块检查编排；Repository 模块不反向导入 Verification。Python 内部负责全部结果与诊断，shell 仅定位仓库并将启动失败转换为非自动续轮退出，保留解释器原始 stderr。

<a id="16-stop-场景图与节点"></a>

## 1.5. 能力边界与源码入口

| 能力 | 核心作用 | 入口链接 |
| --- | --- | --- |
| Hook 协议 | 解析 Stop、中间回合与停用开关 | [delivery_hook.py](../../../scripts/verification/delivery_hook.py) |
| 风险准备／执行 | 冻结真实风险及 profile，调用公共 Verify API | [development.py](../../../scripts/verification/development.py) |
| 环境生命周期 | 使用并清理本次隔离服务 lease | [test_services.py](../../../scripts/environment/test_services.py) |
| CI 边界 | quick 不等于发行完整 baseline | [CI policy](../../../harness/ci-policy.yaml) |

上一层：[开发交付能力总览](../change-delivery.md)。
