# 1. 隔离验证环境

> 位置：[执行门禁](run-repository-gates.md) → 隔离验证环境。环境就绪与验证 PASS 是两个独立结果。


## 1.1. 一次性准备运行时与镜像

| 任务 | 入口 | 成功判据 |
| --- | --- | --- |
| 准备 Hook 环境 | 本页一次性准备 Python、Java 25 与本机 Podman 镜像 | 所需工具和镜像在本机可用；尚未代表测试通过 |
| 正常交付 | [交付 Hook](../reference/hooks.md) | Hook 在同一隔离资源 lease 内执行要求的检查并完成精确清理 |
| 单独运行完整 Verify | `python3 scripts/check_changes.py` 或 `python3 scripts/check_repository.py` | 使用调用者确认的隔离 PostgreSQL/Redis；结果不是自动准备动作 |

首次显式创建 ignored Python 环境并安装声明依赖：

```bash
python3 -m venv .local/lexiflow-python
.local/lexiflow-python/bin/pip install -r requirements-dev.txt
```

后续使用该解释器，不依赖系统环境恰好已有 PyYAML/Ruff。Hook 优先选择此 venv，但不自动安装。确认 Java 25：

```bash
python3 -m scripts.environment.java_exec java -version
```

交付 Hook 使用已运行的本机 Podman 和本机已有镜像。只有首次缺失时，由用户显式准备：

```bash
podman info
podman pull docker.io/library/postgres:17
podman pull docker.io/library/redis:7
```

macOS 上如果本机 Podman machine 已存在但没有运行，先显式执行 `podman machine start`；没有安装或创建过引擎时先按本机运行时要求完成安装。Hook 不自动启动引擎、安装依赖或下载镜像；不能连接引擎、镜像缺失时快速返回具体 BLOCKED，不偷偷使用其他资源。

### 1.1.1. macOS Git 入口（可选）

系统 `/usr/bin/git` 会转交实际 Git。需要减少反复启动成本时，可显式准备仓库私有包装脚本；**不安装工具，不修改全局 PATH，不缓存检查结果。** 后续正常 Stop 自动识别，无需每次 export。

在仓库根用 `.local/lexiflow-python/bin/python` 运行以下代码一次；已有不匹配入口会报错停止，不覆盖：

```python
import os, shutil, stat, subprocess, sys
from pathlib import Path
from scripts.environment.git_runtime import generate_prepared_git_wrapper

assert sys.platform == "darwin" and shutil.which("git") == "/usr/bin/git"
target = Path(subprocess.check_output(
    ["/usr/bin/xcrun", "--find", "git"],
    env={"PATH": os.environ["PATH"]}, text=True, timeout=5,
).strip())
assert target.is_absolute() and target.is_file() and os.access(target, os.X_OK)
directory = Path.cwd().resolve()
for part in ("tmp", "quality", "toolchains", "git", "bin"):
    directory /= part
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
    assert not info.st_mode & 0o022
entry = directory / "git"
payload = generate_prepared_git_wrapper(target.resolve(strict=True))
if not list(directory.iterdir()):
    with entry.open("xb") as stream:
        stream.write(payload)
    entry.chmod(0o700)
assert list(directory.iterdir()) == [entry]
assert not entry.is_symlink() and entry.is_file()
assert entry.read_bytes() == payload and os.access(entry, os.X_OK)
```

| 何时使用 | 如何核对／维护 |
| --- | --- |
| macOS、PATH 首选系统 Git、私有目录安全且目标仍匹配当前工具；Check 声明 Git 和同事务复用能力 | [环境选择](../../scripts/environment/git_runtime.py)只读核对，并仅改变 Check 子进程 PATH；[工具链绑定](../../scripts/environment/toolchain.py)同时记录包装脚本、Shell 解释器和真实 Git 的路径与字节 |
| 自定义 wrapper、其他平台、未准备或入口失效，以及没有工具链绑定的 Check | 保持原路径；顶层 Stop／Verify 在创建 Check 子进程前的 Git 也不在此优化范围 |
| 启用、删除入口或更换 Command Line Tools | 是环境变化，不是历史 PASS 延续；重新验证。消费新环境报告期间保留目录，不在交付链中途清理；目录污染或目标失配先查明，不自动修复 |

包装脚本以 `exec` 执行真实 Git 的绝对路径，原样转交参数和退出状态，保持内置命令和默认模板的安装目录；不使用会改变安装目录推导的符号链接。

这一步只减少启动器开销，所有命令、检查与独立验收仍照常执行。实际收益以完整运行报告为准，不把单用例比例作为整轮保证。

## 1.2. Hook 自动拥有测试资源生命周期

正常交付只需执行 [Hook 入口](../reference/hooks.md)，无需 export PostgreSQL/Redis 地址，也不需要运行阶段 helper。

策略在 `harness/test-services.json`，执行能力为 `scripts/environment/test_services.py`。Hook 从本次实际冻结的所选 checks 的 `required_environment` 判断所需服务；日常执行 `development-change`，不是固定串联两种完整 Verify。需要 PostgreSQL 17 / Redis 7 时在一个 lease 内准备，所选检查共用；检查结束或中断后清理。不需要服务的检查不因此创建容器。

- 只允许本机 Unix socket 或 loopback VM 的 Podman 连接；拒绝远端引擎。
- 先解析本机镜像 ID，创建使用固定 ID 和 `--pull=never`，运行中不受 tag 变化影响。
- 唯一容器名、随机 `127.0.0.1` 端口、独立数据和内存上限；不挂宿主目录、开发数据或持久卷。
- PostgreSQL 数据位于临时内存文件系统；Redis 禁用持久化；30 秒内 readiness 不成功即 BLOCKED。
- 测试变量只在 Hook 进程内临时绑定到本次资源；忽略继承的测试地址，不读取 `.env`，不改 shell profile。
- 成功、失败、超时、Ctrl-C、SIGTERM 均只清理与当前 lease 标签匹配的精确容器 ID；不执行 prune、不删除其他容器、不改开发 compose。
- 清理有界并短暂延后终止信号；无法确认清理时不能输出交付 PASS。SIGKILL 无法执行 finally，不能把强杀当正常清理。

生命周期记录、cidfile 和清理结果保存在 ignored `tmp/quality/test-services/<lease>/`。出现清理 BLOCKED 或强杀时，先按记录中的名字核对 `io.lexiflow.test-lease` 标签与精确 ID，再人工处理该次资源；不批量删除历史或其他任务资源。

## 1.3. 显式 Verify 与开发库边界

[开发 compose](../../infra/local/compose.yaml) 拥有固定开发端口和 PostgreSQL 持久卷，用于导入与本地体验，绝不是测试夹具；不得把其地址填入测试变量。

单独运行 `check_changes.py`、`check_repository.py` 或原生 Gradle 时，调用者仍自行提供已确认隔离的资源与下列变量；这些公开 API 不隐式创建服务：

```bash
export LEXIFLOW_POSTGRES_TEST_JDBC_URL='jdbc:postgresql://127.0.0.1:<test-port>/<isolated-db>?user=postgres'
export LEXIFLOW_REDIS_TEST_ENDPOINT='127.0.0.1:<test-port>'
```

这是形状示例，不是可直接粘贴的资源地址。日常交付建议直接使用 Hook 入口统一完成准备、串行 Verify 与清理。

`deliveryFull` 包含静态检查、JUnit、PostgreSQL 集成、runtime smoke 和 API boot JAR；普通 `check` 不代替它。runtime smoke 使用合成输入短暂启动 API 后停止，不证明真实页面、生产部署或容量。更多原生任务见 [Java Reference](../reference/java-checks.md)。
