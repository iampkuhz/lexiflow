# 1. 隔离验证环境

> 位置：[执行门禁](run-repository-gates.md) → 隔离验证环境。环境就绪与验证 PASS 是两个独立结果。

## 1.1. 一次性准备运行时与镜像

| 任务 | 入口 | 成功判据 |
| --- | --- | --- |
| 准备 Hook 环境 | 本页一次性准备 Python、Java 25 与本机 Podman 镜像 | 所需工具和镜像在本机可用；尚未代表测试通过 |
| 正常交付／精确诊断 | [交付 Hook](../reference/hooks.md) 或 `python3 -m scripts.verification.commands.diagnose_repository_check --check-id <canonical-check-id>` | 执行器在同一隔离资源 lease 内执行要求的检查并完成精确清理 |
| 单独运行完整 Verify | `python3 scripts/verification/commands/run_repository_checks.py` | 使用调用者确认的隔离 Redis（如所选检查需要）；结果不是自动准备动作 |

首次显式创建 ignored Python 环境并安装声明依赖：

```bash
python3 -m venv .local/lexiflow-python
.local/lexiflow-python/bin/pip install -r requirements-dev.txt
```

后续使用该解释器，不依赖系统环境恰好已有 PyYAML/Ruff。调用绝对解释器路径只固定主进程，不能固定 Node 等检查内部按 `PATH` 启动的 `python3`。运行检查前在当前会话激活该环境：

```bash
source .local/lexiflow-python/bin/activate
python3 -c 'import sys; print(sys.executable)'
```

也可仅为本次命令前置 `PATH="$PWD/.local/lexiflow-python/bin:$PATH"`，让主进程及其子进程使用同一已准备的 Python 环境；不修改个人 shell 配置。`eng.release.build` 声明其真实 Python 子解释器依赖，工具链证据记录实际 `PATH` 解析的执行文件；声明就绪不替代真实用例的能力检查。Hook 不自动安装。确认 Java 25：

```bash
python3 -m scripts.local.run_with_java_toolchain java -version
```

Hook 使用本机已有 Podman；只有 Redis 检查依赖缺失时，才按需显式准备固定 Redis 镜像。SQLite 集成测试在每个测试的临时目录中创建独立数据库文件，测试结束自动清理。

## 1.2. Hook 管理 Redis 测试资源

正常交付只需执行 [Hook 入口](../reference/hooks.md)，无需 export Redis 地址，也不需要运行阶段 helper。策略与执行能力位于 `scripts/environment/services/isolated_redis.py`。Hook 从冻结检查的 `required_environment` 判断是否需要 Redis；所选检查共用一个 lease，结束或中断后清理。不需要外部服务的检查不创建容器。

- 只允许本机 Unix socket 或 loopback VM 的 Podman 连接；拒绝远端引擎。
- 使用固定镜像身份、唯一容器名、随机 loopback 端口和独立资源；不挂宿主目录、开发数据或持久卷。
- Redis 禁用持久化；readiness 超时为 BLOCKED。
- 测试变量只在 Hook 进程内临时绑定，忽略继承地址，不读取 `.env`，不改 shell profile。
- 仅清理当前 lease 标签匹配的精确容器；不执行 prune、不删除其他容器、不改开发配置。
- 成功、失败、超时、Ctrl-C、SIGTERM 均须完成归属核对和清理；清理不能确认时不得报告 PASS。

生命周期记录、cidfile 和清理结果保存在 ignored `tmp/quality/test-services/<lease>/`。出现清理 BLOCKED 或强杀时，先按记录核验标签与精确 ID，再人工处理该资源。

## 1.3. 显式 Verify 与 SQLite 边界

[开发 SQLite 文件](../../infra/local/compose.yaml)及本机目录只服务本机导入和体验，绝不是测试夹具；不得把开发文件路径填入测试配置。每个适配器集成测试通过 `@TempDir` 创建隔离 SQLite 文件，载入唯一最新 `infra/sqlite/schema.sql`；不同测试不共享文件。仅 Redis 是可能需要隔离 lease 的外部测试服务。

独立运行 Verify 时，调用者自行提供所需 Redis 地址；SQLite 测试数据库由测试夹具创建并仅对单个测试可见。API 运行采用只读 `mode=ro`，拒绝缺失数据库，不隐式建库或清库。

`deliveryFull` 包含静态检查、JUnit、SQLite 集成、runtime smoke 和 API boot JAR；普通 `check` 不代替它。runtime smoke 使用合成输入和临时 SQLite 文件短暂启动 API 后停止，不证明真实页面、生产部署或容量。更多原生任务见 [Java Reference](../reference/java-checks.md)。
