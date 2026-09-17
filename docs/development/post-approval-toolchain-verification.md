# Post-approval Toolchain Verification

> Catalog task：`LF-TSK-OPS-0001` v2 / change `1.1.0`  
> 状态：**批准后完整复现尚未执行**。本清单区分已有 Java harness 底座与后续复现计划。当前已有 Java 25、Gradle Wrapper 9.7.1、Spring Boot 4.1.1、严格依赖锁与 Java 质量工具；事实来源为 `harness/java-product.manifest.yaml` 和 `backend/`。这些不证明双环境复现或产品业务完成。

## Entry condition and ownership

`OPS-0001` 的 Phase 1 验收证明 ADR、环境差异与本复现计划；不要求先完成批准后的复现。已有 Java 底座由后续明确的 Java/harness 指令授权。G1 的 required Gate receipt 为 `PASS` 且用户决定为 `APPROVED` 后，`LF-WS-OPS` 在后续阶段补齐完整 runtime、Wrapper、lockfile 与构建复现。`LF-WS-QLT` 独立运行验证并保存不可覆盖的 receipt。若官方兼容矩阵、候选稳定版本或本机/CI 能力与 ADR 不一致，结果为 `BLOCKED` 或 `FAIL`，先更新 ADR/OpenSpec，不静默换版本。审批与退出收据顺序见 [Phase 1 acceptance order](phase-1-acceptance-order.md)。

所有命令都在唯一的后续 task/run 中执行。版本占位符先由官方来源核验，再被替换为精确值；下面出现的 `9.7.x`、`4.x`、`6.x` 和 `24.x` 都不是允许浮动的 lock 值。

## 1. Capture inventory and select exact patches

拟执行：

```sh
/usr/libexec/java_home -V
java -version
command -v gradle || true
node --version
python3 --version
```

随后重新读取 ADR-010 中链接的 Oracle、Gradle、Spring Boot、Node、TypeScript、Chrome 与 Python 官方页面，确认候选组合仍受支持。产物记录候选版本、页面 URL、读取日期、兼容结论和选择理由。仓库现已提供 `.local/toolchains/jdk-25/Contents/Home` 下的 Temurin 25，并由 `scripts/toolchain/java_gradle.py` 确定性选择；系统默认 Java 26 不能作为构建运行时，也不能作为 Java Toolchain 25 已满足的证据。Node 运行时选 Node 24 的最新受支持 patch；TypeScript 选稳定 6.x 的精确 patch；Python 3.12 只有在本清单的干净环境检查可通过时才保留。

## 2. Create runtime pins, Wrapper and dependency locks

真源与后续补齐范围（已有 Java 项不能再声称不存在）：

- 后端已有 Gradle Wrapper 9.7.1、distribution checksum、Java Toolchain 25、Kotlin DSL、version catalog 与严格依赖锁。批准后仍需检查 Wrapper JAR checksum、完整 main/included build dependency coverage、缺 lock/错误 runtime 负例及独立干净环境复现。所有 project 使用 `LockMode.STRICT`；禁止只解析 root project 后声称多项目依赖已锁。
- Extension：Node 24 精确 runtime pin、精确 TypeScript version、`package.json#packageManager` 中的精确 npm version 与唯一 package-manager lockfile。首版使用 npm lock 和 `npm ci`；若批准前改用其他 package manager，必须先修订 ADR，不能同时保留两套 lock。
- Python 工具：Python 3.12 精确 runtime pin、`pyproject.toml` 和 `uv.lock`；验证只允许 frozen sync。uv 自身版本也写入工具链清单。

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

首次 Wrapper 使用从 Gradle 官方 distribution URL 下载且经官方 SHA-256 核验的一次性 distribution，不依赖全局 Gradle；临时目录在证据采集完成后删除。Wrapper 生成执行两次，以同时更新 scripts/JAR/properties；之后所有后端命令只能使用 `./gradlew`。

后续 build logic 必须为 main build 的 root 和每个 subproject 应用 `lockAllConfigurations()` 与 `LockMode.STRICT`，并提供根聚合任务 `resolveAndLockAllProjects`：该任务遍历每个 project 的全部 `isCanBeResolved` configuration 并实际 resolve；若有 included build，则调用其同名聚合任务。`verifyDependencyLocks` 执行相同的全项目 resolution，但不带 `--write-locks`，依靠 strict mode 在任一 resolvable configuration 没有 lock state、版本不匹配或出现额外 dependency 时失败。plugins DSL/version catalog 中的 plugin version 也必须精确固定；若出现 `buildscript` classpath，则对该 configuration 单独启用 locking，或者 Gate 拒绝它。

lock 生成后的 diff 必须证明每个有可解析依赖的 project 都有对应 lock state，没有动态版本、重复 package-manager lock 或未解释的 transitive change。当前仓库已有 Java 25 launcher、Gradle Wrapper 与严格依赖锁，并完成本机构建；这里的命令用于批准后的独立干净环境复现，现有文件不证明该复现已经完成。

## 3. Verify clean reconstruction

在一次性 CI checkout 或其他无缓存、无未跟踪文件的临时副本中，从同一 revision 执行；不得用当前工作树的 Gradle、npm、uv cache 作为唯一成功证据：

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

`scripts/toolchain/verify-runtime-pins` 是后续 OPS task 必须创建的纯校验入口：它读取 `.nvmrc` 与 `package.json#packageManager`，要求 `.nvmrc` 是精确 Node 24 patch，并逐字比较 `node --version` 和 `npm --version`；不匹配立即非零退出，且必须在 `npm ci` 前执行。干净 CI 还必须执行一个隔离负例并恢复正确 runtime：

```sh
. "$HOME/.nvm/nvm.sh"
nvm install 26
nvm use 26
if ./scripts/toolchain/verify-runtime-pins; then exit 1; fi
nvm use
./scripts/toolchain/verify-runtime-pins
npm --prefix clients/chrome-extension ci
```

负例只有在错误 Node 被拒绝、切回 `.nvmrc` 后校验通过且随后 `npm ci` 成功时才闭环；预期失败和恢复通过的输出都写入 evidence。不得把 shell 中碰巧可用的 Node/npm 当作 runtime pin 已生效。

Gradle lock 也必须在同一 disposable checkout 中完成缺失状态负例；`LOCK_UNDER_TEST` 必须由 `dependency-locks.json` 选择一个含非空 resolvable configuration 的 subproject lock：

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

该负例只有在缺 lock 被 strict mode 拒绝、原文件 hash 恢复且全项目重新解析通过时才闭环，并且不得在主工作树执行。根构建必须保留 `architectureTest` 诊断入口，并验证 Domain/Application 不依赖 Spring、HTTP、PostgreSQL、Redis、Chrome 或 provider SDK；交付只选择包含该检查和模块 build dependency guard 的 `check`/`deliveryFull` 聚合任务，不再并列重复执行 `architectureTest`。若后续测试布局需要调整命令，先更新本计划和 catalog task version，再执行，不能把“命令不存在”记为通过。

同一 revision 至少执行两次：一次在开发机补齐的目标 runtime 上，一次在干净 CI 环境中。任一 required command 未运行、skipped、依赖未锁、使用错误 runtime 或只能借助未声明全局工具时，结果不能是 `PASS`。

## 4. Evidence and rollback proof

每个 run 写入新的 `tmp/toolchain-verification/<run_id>/`，不得覆盖旧目录：

- `inventory.txt`：实际 `java`、Gradle Wrapper、Node、npm、TypeScript、Python 与 uv 版本及解析路径；
- `official-sources.md`：官方 URL、读取日期、候选版本和兼容性判断；
- `runtime-pins.txt`：所有 runtime pin、Wrapper distribution URL/SHA-256 和 lockfile SHA-256；
- `dependency-locks.json`：逐 project、逐 resolvable configuration 的 lock state 路径、hash、strict verification，以及缺 lock 负例与恢复结果；
- `runtime-negative-check.txt`：错误 Node/npm 被拒绝、重新激活 `.nvmrc` 后通过并继续 `npm ci` 的完整结果；
- `commands.log`：逐条命令、开始/结束时间与退出码；
- `tests.json`：clean build、Extension、Python 和 Architecture Test 的 required/skipped/pass/fail 计数；
- `rollback-check.md`：上一组已通过的 runtime pins/lock hashes，以及恢复后重新执行最小 Gate 的结果；
- `receipt.json`：只允许 `PASS`、`BLOCKED`、`FAIL`，并列出所有 required check。

这些文件是本地运行证据，不提交可能包含机器路径或运行数据的原始日志。G1/G2 Gate 只引用经过脱敏的摘要与 hashes。进程退出 0、依赖已下载、某一子构建通过或本机成功均不能替代两套环境的完整 receipt。

## 5. Continuing version review

`LF-WS-OPS` 在每个 Phase Gate、每个 release candidate 和每 30 天复核一次 lifecycle、兼容矩阵与安全公告；`LF-WS-QLT` 检查复核日期和 evidence。patch 升级使用独立 task 并重跑本清单；major/minor 升级先创建 OpenSpec change。回滚必须恢复上一组完整 runtime pins、Wrapper 和 locks 后重跑最小 Gate，不能混用新旧 dependency graph。
