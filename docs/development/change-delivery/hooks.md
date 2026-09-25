# 1. 交付 Hook

> 位置：[开发交付](../change-delivery.md) → 交付 Hook。Hook 是执行入口，不是独立 Formal Gate，也不是 Git 提交锁。

## 1.1. 谁负责什么

- `AGENTS.md` 保留交付必须验证、不能虚报 PASS 和共享规则入口，不重复命令实现。
- Harness 保存事件策略、语言工具、Check 选择、环境与超时合同。
- Verification 执行冻结输入、检查与报告；Codex/Qoder 配置只触发同一个入口。
- Git Hook 仍只提醒，不在编辑、每次工具调用或 commit 时扫描；CI 必须独立执行 Repository Verify，不能信任本机 Hook。

## 1.2. 交付时实际做什么

正常 Stop 作为交付尝试，串行运行 Change Verify 和 Repository Verify 的公开 API，发布两份不可变报告。Change Verify 按 Git diff（含未跟踪文件和删除）选择模块；Repository Verify 覆盖完整 baseline。Java 由 Gradle `deliveryFull` 执行静态检查和测试；scripts 执行 Ruff、Pylint 中文 docstring 检查及所属模块测试。任何必需检查未运行、环境缺失、输入漂移或测试失败都不能产生交付 PASS。Change 不通过时不继续重复跑 baseline。

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
- 同 checkout 的 Hook 使用非等待锁；已有检查时立即 BLOCKED，不重跑、不排无限队列。手工 Verify 与 Hook 也须由操作者串行安排。
- 不跨交付缓存 PASS，不以旧报告代替真实执行。进程退出自动释放锁；不要删除锁文件“解锁”。
- Hook 按需创建本次隔离 PostgreSQL/Redis 测试容器，检查结束自动清理；不再要求手工导出地址。运行时或本机镜像缺失则 BLOCKED，不自动安装、下载、启动容器引擎或使用开发库。准备方式见[验证环境](../operations/verification-environment.md)。

入口优先使用已准备的 `.local/lexiflow-python/bin/python`，没有则使用当前解释器。临时测试服务由环境模块按 `harness/test-services.json` 管理，两个 Verify 共用一个 lease。

紧急退出可以直接把 `harness/delivery-hooks.json` 改为 `{"mode":"off"}`，或在宿主启动环境设置 `LEXIFLOW_DELIVERY_HOOK_DISABLE=1`。关闭不需要先通过门禁，输出明确为未验证，不豁免交付检查。也可以仅移除本项目客户端 Stop 入口，不改其他项目或全局配置。

## 1.4. 双客户端配置与手工执行

Codex 使用 `.codex/hooks.json`；Qoder IDE/CLI 使用 `.qoder/settings.json`，参见 [Qoder Hooks](https://docs.qoder.com/cli/hooks)。两者调用同一 Python 入口；Qoder 仅传 `--client qoder` 适配输出协议，不复制检查实现。首次加载或命令变化需要在实际宿主中审阅/信任；Qoder 需重新加载会话。命令夹具通过不证明宿主已加载配置，不自动修改用户信任设置。

在仓库根目录执行真实交付检查：

```bash
printf '%s\n' '{"hook_event_name":"Stop","stop_hook_active":false}' | python3 scripts/verification/delivery_hook.py
```

命令中的引号不要手工加 JSON 展示用的反斜杠。裸运行不输入 JSON 会立即诊断 TTY；管道未关闭在 3 秒后明确返回错误，不无限等待 EOF。

入口放在 Verification 模块，因为它拥有跨模块检查编排；Repository 模块不反向导入 Verification。Python 内部负责全部结果与诊断，shell 仅定位仓库并将启动失败转换为非自动续轮退出，保留解释器原始 stderr。
