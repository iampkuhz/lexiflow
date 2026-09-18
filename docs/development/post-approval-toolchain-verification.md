<a id="post-approval-toolchain-verification"></a>

# 批准后的工具链验证

> 目录任务：`LF-TSK-OPS-0001` v2 / 变更 `1.1.0`
> 状态：**批准后完整复现尚未执行**。本清单区分已有 Java harness 底座与后续复现计划。当前已有 Java 25、Gradle Wrapper 9.7.1、Spring Boot 4.1.1、严格依赖锁与 Java 质量工具；事实来源为 `harness/java-product.manifest.yaml` 和 `backend/`。这些不证明双环境复现或产品业务完成。

<a id="entry-condition-and-ownership"></a>

## 准入条件与职责归属

`OPS-0001` 的阶段 1 验收证明 ADR、环境差异与本复现计划；不要求先完成批准后的复现。已有 Java 底座由后续明确的 Java/harness 指令授权。G1 的必需 Gate 收据为 `PASS` 且用户决定为 `APPROVED` 后，`LF-WS-OPS` 在后续阶段补齐完整运行时、Wrapper、锁文件与构建复现。`LF-WS-QLT` 独立运行验证并保存不可覆盖的收据。若官方兼容矩阵、候选稳定版本或本机/CI 能力与 ADR 不一致，结果为 `BLOCKED` 或 `FAIL`，先更新 ADR/OpenSpec，不静默换版本。审批与退出收据顺序见 [第一阶段验收顺序](validation/06-phase1-decision.md)。

所有命令都在唯一的后续任务/运行中执行。版本占位符先由官方来源核验，再被替换为精确值；下面出现的 `9.7.x`、`4.x`、`6.x` 和 `24.x` 都不是允许浮动的锁值。

<a id="1-capture-inventory-and-select-exact-patches"></a>

## 1. 清点工具并选择精确补丁版本

拟执行：

```sh
/usr/libexec/java_home -V
java -version
command -v gradle || true
node --version
python3 --version
```

随后重新读取 ADR-010 中链接的 Oracle、Gradle、Spring Boot、Node、TypeScript、Chrome 与 Python 官方页面，确认候选组合仍受支持。产物记录候选版本、页面 URL、读取日期、兼容结论和选择理由。仓库现已提供 `.local/toolchains/jdk-25/Contents/Home` 下的 Temurin 25，并由 `scripts/toolchain/java_gradle.py` 确定性选择；系统默认 Java 26 不能作为构建运行时，也不能作为 Java 工具链 25 已满足的证据。Node 运行时选 Node 24 的最新受支持补丁；TypeScript 选稳定 6.x 的精确补丁；Python 3.12 只有在本清单的干净环境检查可通过时才保留。

<a id="2-create-runtime-pins-wrapper-and-dependency-locks"></a>

## 2. 建立运行时版本固定、Wrapper 与依赖锁

真源与后续补齐范围（已有 Java 项不能再声称不存在）：

- 后端已有 Gradle Wrapper 9.7.1、发行包校验和、Java 工具链 25、Kotlin DSL、版本目录与严格依赖锁。批准后仍需检查 Wrapper JAR 校验和、完整主代理/包含的构建依赖覆盖、缺锁/错误运行时负例及独立干净环境复现。所有项目使用 `LockMode.STRICT`；禁止只解析根项目后声称多项目依赖已锁。
- 扩展：Node 24 精确运行时固定版本、精确 TypeScript 版本、`package.json#packageManager` 中的精确 npm 版本与唯一包管理器锁文件。首版使用 npm 锁和 `npm ci`；若批准前改用其他工作包管理器，必须先修订 ADR，不能同时保留两套锁。
- Python 工具：Python 3.12 精确运行时固定版本、`pyproject.toml` 和 `uv.lock`；验证只允许冻结同步。uv 自身版本也写入工具链清单。

拟执行命令模板：

```sh
GRADLE_VERSION="<GRADLE_9_7_PATCH>"
GRADLE_SHA256="<OFFICIAL_SHA256>"
GRADLE_WRAPPER_JAR_SHA256="<OFFICIAL_WRAPPER_JAR_SHA256>"
NODE_VERSION="<NODE_24_PATCH>"
NPM_VERSION="<NPM_PATCH_BUNDLED_WITH_SELECTED_NODE>"
TYPESCRIPT_VERSION="<TYPESCRIPT_6_PATCH>"
PYTHON_VERSION="<PYTHON_3_12_PATCH>"
BOOTSTRAP_DIR=$(mktemp -d)
curl --fail --location --proto '=https' --tlsv1.2 "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" --output "${BOOTSTRAP_DIR}/gradle-bin.zip"
printf '%s  %s\n' "${GRADLE_SHA256}" "${BOOTSTRAP_DIR}/gradle-bin.zip" | shasum --algorithm 256 --check
unzip -q "${BOOTSTRAP_DIR}/gradle-bin.zip" -d "${BOOTSTRAP_DIR}"
"${BOOTSTRAP_DIR}/gradle-${GRADLE_VERSION}/bin/gradle" wrapper --gradle-version "${GRADLE_VERSION}" --distribution-type bin
./gradlew wrapper --gradle-version "${GRADLE_VERSION}" --distribution-type bin --gradle-distribution-sha256-sum "${GRADLE_SHA256}"
printf '%s  %s\n' "${GRADLE_WRAPPER_JAR_SHA256}" "gradle/wrapper/gradle-wrapper.jar" | shasum --algorithm 256 --check
JDK25_HOME=$(/usr/libexec/java_home -v 25)
"${JDK25_HOME}/bin/java" -version
JAVA_HOME="${JDK25_HOME}" ./gradlew --version
JAVA_HOME="${JDK25_HOME}" ./gradlew javaToolchains
JAVA_HOME="${JDK25_HOME}" ./gradlew resolveAndLockAllProjects --write-locks
JAVA_HOME="${JDK25_HOME}" ./gradlew verifyDependencyLocks

printf '%s\n' "${NODE_VERSION}" > .nvmrc
. "$HOME/.nvm/nvm.sh"
nvm install "${NODE_VERSION}"
nvm use "${NODE_VERSION}"
node --version
npm --version
test "$(npm --version)" = "${NPM_VERSION}"
npm --prefix clients/chrome-extension pkg set "packageManager=npm@${NPM_VERSION}"
npm --prefix clients/chrome-extension install --save-exact --package-lock-only "typescript@${TYPESCRIPT_VERSION}"
npm --prefix clients/chrome-extension ci

uv python install "${PYTHON_VERSION}"
uv python pin "${PYTHON_VERSION}"
uv lock --python "${PYTHON_VERSION}"
uv sync --frozen --python "${PYTHON_VERSION}"
```

首次 Wrapper 使用从 Gradle 官方发行包 URL 下载且经官方 SHA-256 核验的一次性发行包，不依赖全局 Gradle；临时目录在证据采集完成后删除。Wrapper 生成执行两次，以同时更新 scripts/JAR/properties；之后所有后端命令只能使用 `./gradlew`。

后续构建逻辑必须为主构建的根和每个子项目应用 `lockAllConfigurations()` 与 `LockMode.STRICT`，并提供根聚合任务 `resolveAndLockAllProjects`：该任务遍历每个项目的全部 `isCanBeResolved` 配置并实际解析；若有包含的构建，则调用其同名聚合任务。`verifyDependencyLocks` 执行相同的全项目解析，但不带 `--write-locks`，依靠严格模式在任一可解析配置没有锁状态、版本不匹配或出现额外依赖时失败。插件 DSL/版本目录中的插件版本也必须精确固定；若出现 `buildscript` 类路径，则对该配置单独启用锁定，或者 Gate 拒绝它。

锁生成后的差异必须证明每个有可解析依赖的项目都有对应锁状态，没有动态版本、重复包管理器锁或未解释的传递性变更。当前仓库已有 Java 25 启动器、Gradle Wrapper 与严格依赖锁，并完成本机构建；这里的命令用于批准后的独立干净环境复现，现有文件不证明该复现已经完成。

<a id="3-verify-clean-reconstruction"></a>

## 3. 验证干净环境重建

在一次性 CI 检出副本或其他无缓存、无未跟踪文件的临时副本中，从同一修订号执行；不得用当前工作树的 Gradle、npm、uv 缓存作为唯一成功证据：

```sh
python3 scripts/toolchain/java_gradle.py --no-daemon --refresh-dependencies clean deliveryFull
. "$HOME/.nvm/nvm.sh"
nvm install
nvm use
./scripts/toolchain/verify-runtime-pins
npm --prefix clients/chrome-extension ci
npm --prefix clients/chrome-extension run typecheck
npm --prefix clients/chrome-extension test
npm --prefix clients/chrome-extension run build
uv sync --frozen --python "<PYTHON_3_12_PATCH>"
uv run --frozen python -m unittest discover -s tests
```

`scripts/toolchain/verify-runtime-pins` 是后续 OPS 任务必须创建的纯校验入口：它读取 `.nvmrc` 与 `package.json#packageManager`，要求 `.nvmrc` 是精确 Node 24 补丁，并逐字比较 `node --version` 和 `npm --version`；不匹配立即非零退出，且必须在 `npm ci` 前执行。干净 CI 还必须执行一个隔离负例并恢复正确运行时：

```sh
. "$HOME/.nvm/nvm.sh"
nvm install 26
nvm use 26
if ./scripts/toolchain/verify-runtime-pins; then exit 1; fi
nvm use
./scripts/toolchain/verify-runtime-pins
npm --prefix clients/chrome-extension ci
```

负例只有在错误 Node 被拒绝、切回 `.nvmrc` 后校验通过且随后 `npm ci` 成功时才闭环；预期失败和恢复通过的输出都写入证据。不得把 shell 中碰巧可用的 Node/npm 当作运行时固定版本已生效。

Gradle 锁也必须在同一一次性检出副本中完成缺失状态负例；`LOCK_UNDER_TEST` 必须由 `dependency-locks.json` 选择一个含非空可解析配置的子项目锁：

```sh
LOCK_UNDER_TEST="<NON_EMPTY_SUBPROJECT_GRADLE_LOCKFILE>"
LOCK_HASH=$(shasum --algorithm 256 "${LOCK_UNDER_TEST}" | awk '{print $1}')
LOCK_BACKUP=$(mktemp)
mv "${LOCK_UNDER_TEST}" "${LOCK_BACKUP}"
if ./gradlew --no-daemon verifyDependencyLocks; then exit 1; fi
mv "${LOCK_BACKUP}" "${LOCK_UNDER_TEST}"
test "$(shasum --algorithm 256 "${LOCK_UNDER_TEST}" | awk '{print $1}')" = "${LOCK_HASH}"
./gradlew --no-daemon verifyDependencyLocks
```

该负例只有在缺锁被严格模式拒绝、原文件哈希恢复且全项目重新解析通过时才闭环，并且不得在主工作树执行。根构建必须保留 `architectureTest` 诊断入口，并验证领域/应用不依赖 Spring、HTTP、PostgreSQL、Redis、Chrome 或供应商 SDK；交付只选择包含该检查和模块构建依赖护栏的 `check`/`deliveryFull` 聚合任务，不再并列重复执行 `architectureTest`。若后续测试布局需要调整命令，先更新本计划和目录任务版本，再执行，不能把“命令不存在”记为通过。

同一修订号至少执行两次：一次在开发机补齐的目标运行时上，一次在干净 CI 环境中。任一必需命令未运行、跳过、依赖未锁、使用错误运行时或只能借助未声明全局工具时，结果不能是 `PASS`。

<a id="4-evidence-and-rollback-proof"></a>

## 4. 证据与回退证明

每个运行写入新的 `tmp/toolchain-verification/<run_id>/`，不得覆盖旧目录：

- `inventory.txt`：实际 `java`、Gradle Wrapper、Node、npm、TypeScript、Python 与 uv 版本及解析路径；
- `official-sources.md`：官方 URL、读取日期、候选版本和兼容性判断；
- `runtime-pins.txt`：所有运行时固定版本、Wrapper 发行包 URL/SHA-256 和锁文件 SHA-256；
- `dependency-locks.json`：逐项目、逐可解析配置的锁状态路径、哈希、严格核验，以及缺锁负例与恢复结果；
- `runtime-negative-check.txt`：错误 Node/npm 被拒绝、重新激活 `.nvmrc` 后通过并继续 `npm ci` 的完整结果；
- `commands.log`：逐条命令、开始/结束时间与退出码；
- `tests.json`：清理后构建、扩展、Python 和架构测试的必需/跳过/pass/fail 计数；
- `rollback-check.md`：上一组已通过的运行时版本固定项/锁哈希，以及恢复后重新执行最小 Gate 的结果；
- `receipt.json`：只允许 `PASS`、`BLOCKED`、`FAIL`，并列出所有必需检查。

这些文件是本地运行证据，不提交可能包含机器路径或运行数据的原始日志。G1/G2 Gate 只引用经过脱敏的摘要与哈希。进程退出 0、依赖已下载、某一子构建通过或本机成功均不能替代两套环境的完整收据。

<a id="5-continuing-version-review"></a>

## 5. 持续版本审查

`LF-WS-OPS` 在每个阶段 Gate、每个发布候选和每 30 天复核一次生命周期、兼容矩阵与安全公告；`LF-WS-QLT` 检查复核日期和证据。补丁升级使用独立任务并重跑本清单；主版本/次版本升级先创建 OpenSpec 变更。回滚必须恢复上一组完整运行时版本固定项、Wrapper 和锁后重跑最小 Gate，不能混用新旧依赖图。
