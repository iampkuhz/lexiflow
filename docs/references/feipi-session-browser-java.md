# Reference Audit: feipi-session-browser-java

> 审计日期：2026-09-16  
> 来源 checkout：`/Users/zhehan/Documents/tools/llm/feipi-session-browser-java`

LexiFlow 复用参考项目中已经证明有效的工程边界，但不复制其会话浏览器业务模型、Java 模块名、本地配置或历史运行产物。

## Reused constraints

| Reference owner | Applied to LexiFlow |
|---|---|
| `AGENTS.md` | 短入口、OpenSpec-first、隐私、显式 Gate、精确 Git 范围。 |
| `harness/agent-policy.manifest.yaml` | 以参考仓库九字段基线为起点，演进为版本化分层 handoff；保留唯一 Task/agent/run identity、不重叠写范围和真实 PASS/BLOCKED/FAIL。 |
| `docs/development/qoder-subtasks.md` | 单 Qoder run、completion-first callback、精确父 Session、主 LLM 不轮询。 |
| `scripts/harness/qoder_task*.py` | 非阻塞 worker、全局 Qoder CLI preflight、原子 completion、ack/supersession。 |
| `harness/agent-runtime.manifest.yaml` | Session 和 checkout 由客户端拥有；Harness 不创建第二套仓库写入授权状态。 |
| `openspec/specs/agent-harness/spec.md` | 非平凡变更先规划；普通写入不依赖 active-state token；Git mutation 不自动发生。 |
| Java architecture tests | ports/adapters 依赖、core 禁止具体数据库/provider、composition root 是唯一装配点。 |
| Gate control plane | 单一公开入口、incremental/full、未触发不算 PASS、每次 run 的证据不可覆盖。 |

## Wrapper and build evidence inspected

以下是参考 checkout 的实际证据，不表示 LexiFlow 已经创建或执行相同构建：

| Evidence path under `feipi-session-browser-java/java/` | Observed property | LexiFlow use |
|---|---|---|
| `gradle/wrapper/gradle-wrapper.properties` | `distributionUrl` 固定 Gradle `9.6.0`；`validateDistributionUrl=true`；无重试。 | 复用“Wrapper 是唯一入口、校验 distribution”的模式；LexiFlow 在批准后按 ADR 重新核验并锁定 9.7.x，不复制 9.6.0。 |
| `build.gradle.kts` 与 `gradle/dependency-locks/root.lockfile` | root build 对所有 configuration 启用 dependency locking，并把 lock 固定在显式路径。 | LexiFlow 后端须覆盖完整解析图，并在 clean build 中证明 lock 被消费。 |
| `gradle/libs.versions.toml` | 依赖及质量工具集中在 version catalog。 | LexiFlow 复用单一 catalog 的版本所有权，不复制参考项目的业务依赖或具体版本。 |
| `gradle/build-logic/src/main/kotlin/feipi.java-base.gradle.kts` | 所有 `JavaCompile` 使用 `options.release.set(25)`；archive 使用可复现顺序和时间戳策略。 | LexiFlow 使用显式 Java Toolchain 25，并以 release 25 作为编译产物约束；不能仅依赖运行 Gradle 的 JVM。 |
| `settings.gradle.kts`、`tests/architecture/build.gradle.kts` 与 `tests/architecture/src/test/` | Architecture Test 是独立 Gradle module，包含 ArchUnit 与 build-file dependency guard。 | LexiFlow 后续根构建必须提供独立 `architectureTest` 入口，覆盖字节码依赖和 build dependency 两层。 |

参考 Wrapper 当前是 9.6.0，而 LexiFlow 提案是 9.7.x；这是有意重新选型，不是可直接复制的 lock。批准后的复现步骤见 [Post-approval toolchain verification](../development/post-approval-toolchain-verification.md)。

## Local runtime inventory

2026-09-16 在当前 Codex shell 盘点到：

| Tool | Observed state | Decision impact |
|---|---|---|
| Java | 初始审计时 `/usr/libexec/java_home -V` 仅列出 Temurin `26.0.2.1`；当前 LexiFlow 仓库已具备 `.local/toolchains/jdk-25/Contents/Home` 下的 Temurin `25.0.4.1` LTS。 | **已落实**：`scripts/toolchain/java_gradle.py` 固定并校验 Temurin 25；系统默认 Java 26 被拒绝，不能成为产品构建证据。 |
| Gradle | PATH 中没有全局 `gradle`。 | 不是产品缺陷；首次生成 Wrapper 需要受控 bootstrap，之后只允许 `./gradlew`。 |
| Node | PATH 默认 `/opt/homebrew/bin/node` 为 `26.8.2`；NVM 已有 `24.12.0`。 | Node 24 已存在但未成为默认值；后续必须通过 repo runtime pin 选择 24.x，不能继承 PATH 的 Node 26。 |
| Python | pyenv shim 解析为 Python `3.12.11`。 | 满足当前 Phase 1 Harness 基线；仍须处理 3.12 security-only、source-only 安装和升级阈值。 |

本清单只记录观测，不改变机器配置。除 JDK 25 缺失外，Node 的默认版本漂移和 Python 3.12 的生命周期风险也必须由 repo pin 与后续 clean-environment Gate 显式控制。

## LexiFlow-specific mapping

- Session Browser 的 source/index/domain 隔离映射为 Content、Lexicon、Vocabulary、Learning、Semantic 与 Enrichment 的公开 contract。
- Java core 禁止 JDBC/SQLite/provider 依赖映射为 LexiFlow core 禁止 HTTP、PostgreSQL、Redis、YouTube/Chrome 与模型 SDK。
- Qoder worker 的 300 秒首次 watchdog、600 秒后续间隔和 24 小时寿命保持不变，并增加本仓库 synthetic test。
- Acceptance Case 注册表用于连接长期要求、任务和未来产品测试，但映射本身不被当作产品通过证据。

## Intentionally not copied

- `.idea/`、`.local/`、`tmp/`、个人 settings、真实 session 与任何运行凭证。
- Session Browser 的产品模块、SQLite 数据模型、UI 和发布脚本。
- 参考 Gate 框架的全部复杂度。LexiFlow 第一版只验证当前仓库结构、规划 DAG、机器契约和 OpenSpec traceability，等有产品 workload 后再按证据增加 Gate。
- 自动 Git mutation、仓库 writer lease、全局任务 daemon 或把 OpenSpec active change 当文件写权限。

## Known boundary

Qoder 的 allowed/forbidden paths 通过 prompt 约束，不构成 OS 沙箱。主 Agent 必须复核实际 diff。`codex queue` 的 queued receipt 只证明消息被排入目标 Session，不证明任务输出满足验收。
