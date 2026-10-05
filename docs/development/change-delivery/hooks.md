# 1. 交付 Hook

> 位置：[开发交付](../change-delivery.md) → 交付 Hook。Hook 是执行入口，不是独立 Formal Gate，也不是 Git 提交锁。

## 1.1. 谁负责什么

- `AGENTS.md` 保留交付必须验证、不能虚报 PASS 和共享规则入口，不重复命令实现。
- Harness 保存事件策略、语言工具、Check 选择、环境与超时合同。
- Verification 执行冻结输入、检查与报告；Codex/Qoder 配置只触发同一个入口。
- Git Hook 仍只提醒，不在编辑、每次工具调用或 commit 时扫描；CI 必须独立执行 Repository Verify，不能信任本机 Hook。

## 1.2. 交付时实际做什么

正常 Stop 调用 `python3 -m scripts.verification.development`，与开发 CLI 共用风险准备和执行逻辑，在同一个验证窗口内隔离环境后串行执行所需 profiles。机械/局部风险执行 `development-change`；高风险工程另执行 `development-baseline`。正式发行的完整 Repository baseline 仍由 required formal check 路径执行，不由开发 PASS 代替。Java 检查通过 Gradle 执行；scripts 按所属 profile 执行质量与测试检查。任何必需检查未运行、环境缺失、输入漂移或测试失败都不能产生开发 PASS。

Ruff 风格与格式规则在 `harness/python-quality.toml`；Pylint 中文 docstring 规则在 `harness/python-docstrings.toml`，只约束 `scripts/` 模块与公共定义。工具固定版本在 `requirements-dev.txt`。不以注释数量、模板注释或 lint 通过代替语义审阅。

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

紧急退出可以直接把 `harness/delivery-hooks.json` 改为 `{"mode":"off"}`，或在宿主启动环境设置 `LEXIFLOW_DELIVERY_HOOK_DISABLE=1`。关闭不需要先通过门禁，输出明确为未验证，不豁免交付检查。也可以仅移除本项目客户端 Stop 入口，不改其他项目或全局配置。

## 1.4. 双客户端配置与手工执行

Codex 使用 `.codex/hooks.json`；Qoder IDE/CLI 使用 `.qoder/settings.json`，参见 [Qoder Hooks](https://docs.qoder.com/cli/hooks)。两者调用同一 Python 入口；Qoder 仅传 `--client qoder` 适配输出协议，不复制检查实现。首次加载或命令变化需要在实际宿主中审阅/信任；Qoder 需重新加载会话。命令夹具通过不证明宿主已加载配置，不自动修改用户信任设置。

在仓库根目录执行真实交付检查：

```bash
printf '%s\n' '{"hook_event_name":"Stop","stop_hook_active":false}' | python3 scripts/verification/delivery_hook.py
```

命令中的引号不要手工加 JSON 展示用的反斜杠。裸运行不输入 JSON 会立即诊断 TTY；管道未关闭在 3 秒后明确返回错误，不无限等待 EOF。

入口放在 Verification 模块，因为它拥有跨模块检查编排；Repository 模块不反向导入 Verification。Python 内部负责全部结果与诊断，shell 仅定位仓库并将启动失败转换为非自动续轮退出，保留解释器原始 stderr。

## 1.5. 工作流：先确定 checkout，再冻结验证

交付验证遵循以下顺序，避免重复执行和证据混淆：

1. **先确定最终 checkout 和输入**：在启动任何验证前，确认工作树状态、变更范围和输入文件指纹。不在验证中途切换分支或修改输入。
2. **避免重复完整验证**：先做与变更相关的有界直接自检，再由独立 validator 对冻结批次执行完整 Hook；不要惯例性地先跑完整 adapter，再跑 development，最后重复 Hook。修复真实失败或相关输入变化后的必要重验不属于重复浪费。
3. **长事件等待与日志读取**：对于长时间运行的检查（如 Gradle deliveryFull、Node suite），使用原生事件等待而非轮询。日志按事件/终态读取，不在运行中反复 tail。Qoder 终态使用 callback，不依赖轮询状态文件。
4. **审查者只读冻结证据**：审查者读取已冻结的报告和日志，不重新执行检查。若必要检查失败/阻塞，且修复或相关输入、环境变化使旧结果不再适用，validator 按当前冻结计划进行必要重验；不得为避免重验而沿用失效证据。

此工作流不添加跨回合缓存或调度器；实现者可做有界直接自检。仅在既有事务合同证明完整输入、配置、环境、窗口、runner 与 context 相同的情况下复用同次事务成功 Check，禁止跨交付复用。日志仅在有信息的事件或终态读取，原生等待用长事件等待。共享顺序及重验条件见 [policy validation_efficiency](../../../harness/agent-policy.manifest.yaml)。状态按真实执行记录报告。
