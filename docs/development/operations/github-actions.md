# 1. GitHub 检查与正式发行边界

> 位置：[运行与环境](../operations.md) → GitHub Actions。普通工程反馈与正式发行是两层；任何快速检查都不是 Formal receipt。

## 1.1. 分支与 PR：共享快速入口

`.github/workflows/ci.yml` 在分支 push、`pull_request` 和手动运行时调用：

```sh
python3 -m scripts.verification.ci quick --base <完整commit>
```

新分支没有可比较的前序 commit 时使用 `--all`；本机可用 `plan` 只查看选择，不执行检查。检查仍由 `harness/module-checks.yaml` 的模块命令负责，`harness/ci-policy.yaml` 仅决定 CI 范围：版本、文档与 policy 始终核对；受影响模块连同必要依赖执行编译、静态检查、业务测试及扩展构建/浏览器测试。真实发行生命周期留到标签流程，不默认导入真实词库。

快速入口保留输入冻结、缺环境 BLOCKED、测试跳过拒绝和覆盖缺口；输出固定说明 `formal_eligible=false`。托管 Linux runner 只是隔离的工程检查环境，不新增产品交付平台，也不代替 M 芯片 Mac/Podman 验收。临时 PostgreSQL/Redis 仅服务本次合成测试，不能接到用户安装。

PR 没有发布权限，不使用 `pull_request_target`、self-hosted 或发布 Environment，不上传 `tmp/`、API 原始日志、字幕正文或观看记录。Action 固定完整 commit SHA；源码不能在取得 token 后随意替换。参考 [GitHub 安全使用说明](https://docs.github.com/en/actions/reference/security/secure-use)。

### 1.1.1. 下载与加载扩展

Quick checks 成功后，同一 run 使用现有 `extension/scripts/release.mjs` 构建并校验 ZIP，在隔离 Playwright Chromium profile 加载 service worker 和 popup，再上传 `lexiflow-chrome-extension-<完整commit>` artifact，保留 14 天。此结果是自动化加载 smoke，不是人工 Chrome 安装或正式 Release 验收。run 的 event、分支及完整 commit 必须与目标源码对应；PR run 可能对应 GitHub 合并测试 commit，而非 PR head。

Actions 网页下载的是外层 artifact ZIP；先解开它，得到内层 `lexiflow-extension-<softwareVersion>.zip` 与同名 `.zip.sha256`。可使用 `gh run download <run-id> --name lexiflow-chrome-extension-<完整commit> --dir <目录>` 直接解开外层。下载需要 GitHub 登录及仓库读取权限；过期后不能将旧下载入口承诺为永久发行。校验内层：

```sh
cd <下载目录>
shasum -a 256 -c lexiflow-extension-<softwareVersion>.zip.sha256
```

随后将内层 ZIP 解压到稳定目录；该目录根部必须有 `manifest.json`、`build-identity.json` 与运行资源。打开 `chrome://extensions`，启用开发者模式，点击“加载已解压的扩展程序”，选择这个目录，而不是外层 artifact 目录。API 仍需单独按本机安装说明运行；ZIP 不包含后端或词库。更新后替换该解压目录并在扩展管理页重新加载。

开发者可加载可信的解压扩展；ZIP 仅是 Chrome Web Store 上传格式，须经商店审核才可作为普通用户安装方式。GitHub ZIP、自签名 CRX 与自动生成的 Source code ZIP 都不是通用安装包；自托管还受企业策略约束，本项目不修改浏览器策略。参见[Chrome 分发规则](https://developer.chrome.com/docs/extensions/how-to/distribute)、[加载解压扩展](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world#load-unpacked)及[商店上传](https://developer.chrome.com/docs/webstore/publish)。

## 1.2. 正式标签：完整验证与独立验收

`.github/workflows/release.yml` 将 `v*` 标签准备与显式手动晋升分开。标签阶段首先由共享版本入口验证标签指向的 commit、干净源码与三段正式版本完全一致。`2.0.0-SNAPSHOT`、dirty 输入或标签漂移均拒绝；手动续办只能消费原标签、原 checkout 已有的候选和原生验收证据，不能另传源码或候选目录。

标签来源检查之后进入唯一专用 Apple Silicon macOS/Podman runner，在同一稳定 checkout 构建 JAR、扩展和单 ARM64 镜像，组装真实候选，随后串行执行 Change Verify 和 Repository Verify。构建与验证不启用发布权限，不自动签发 validation/review/check，也不将 Actions 绿灯等同于正式发行。

候选准备、完整 Verify 与原生验收之间保留原路径和字节。负责人在该 checkout 的真实 Codex 任务中按 [Delivery Gate](../change-delivery/delivery-gate.md) 送验，交给不同原生 actor 验证和审查，最后核对 receipt/hash DAG。原生验证执行自身冻结检查，审查与条件核对不重跑；不要为节省时间复用普通 Actions 的退出码作为原生 receipt。手动晋升不再 checkout 或清理工作树，缺链、输入漂移或原生来源不可读时阻断。

正式晋升还必须核对同候选制品摘要、来源与许可、干净安装、同卷保留资料升级/失败恢复，以及真实独立 Formal 链。缺少任一项不得创建公开 Release 或更新正式版指针。已有发行生命周期的合成两代资料测试不能替代 local `upgrade` 的同卷验收。

候选构建在镜像标签之外核对 JAR 内嵌身份；组装时核对扩展 ZIP 内嵌身份及 `manifest.json` 的完整 `buildIdentity`。源码摘要、build ID、展示版本、Chrome 数字版本和 commit 必须一致。ZIP/JAR 仅接受有界 ZIP32 stored/deflate，不接受 ZIP64、加密或歧义条目；错误不能通过重写 checksum 消除。干净 SNAPSHOT 可形成私有候选，但不满足正式标签晋升条件。

## 1.3. 仓库管理员准备

1. 使用唯一、独立、受控的 Mac runner，标签为 `self-hosted`、`macOS`、`ARM64`、`lexiflow-release`；该标签不能匹配多台机器。专用工作目录不得被其他 workflow 使用、清理或切换源码。不要使用日常开发目录或实际用户安装；禁止不可信 PR 和普通分支进入该宿主。公共仓库更需严格限制专用 runner 的执行来源。
2. 预装 Node.js 22+、Python 与 `requirements-dev.txt`、Temurin 25、Podman Compose、Docker 兼容客户端及扩展依赖/Playwright。只使用已确认的 Podman Unix endpoint；流程不创建或更改 machine。
3. 创建 `lexiflow-release-validation` 和 `lexiflow-release-promotion` 两个 Environment，均要求独立人工批准、禁止自批，并只允许受保护正式标签。限制标签创建/移动与 workflow 修改权限；Environment 本身不是 Formal 验收。参考 [GitHub Environment 配置](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)。
4. 配置同一固定 checkout 的绝对路径 `LEXIFLOW_RELEASE_WORKSPACE` 和私有构建请求文件绝对路径 `LEXIFLOW_RELEASE_REQUEST_FILE`；工作流不在该路径执行 checkout，负责人须先在已授权的干净源码目录准备精确标签和 commit。两处 Environment 的 workspace 必须一致。设置 Environment 变量 `LEXIFLOW_RELEASE_JAVA_HOME`、`LEXIFLOW_RELEASE_TEST_REDIS_ENDPOINT`、`LEXIFLOW_RELEASE_ARM64_DOCKER_HOST`、`LEXIFLOW_RELEASE_GRADLE_CACHE`、`LEXIFLOW_RELEASE_NPM_CACHE`；将隔离 PostgreSQL 测试 JDBC 放入 `LEXIFLOW_RELEASE_TEST_JDBC_URL` secret，不能指向实际安装。缓存仅为构建依赖，不包含用户脚本或私有数据。
5. 在同一 OS 用户与稳定 checkout 配置真实 Codex 运行环境，确保历史 actor 元数据、Gate records 与 Verify 附件可重读。仅复制 JSON、UUID 或将原始记录上传为 Actions artifact 不满足此条件。
6. 确认上述隔离和保护后，再将 repository variable `LEXIFLOW_RELEASE_RUNNER_READY` 设为 `true`。它仅是管理员配置确认，不是测试、机器身份或独立验收证据。

读取既有链使用 `python3 -m scripts.delivery_gate consume-existing --submission-id <uuid>`；缺 check 不会补签，且通过只说明该链可核验，仍须核对同候选 runtime 输出。Formal receipt 依赖真实原生 actor/session 元数据和固定输入附件，不能跨机器仅复制 UUID 或填写一个 PASS JSON。执行状态及缺失资源见变更状态页；实际 GitHub 配置、发布和标签操作需要单独授权。


## 1.4. 候选完整性消费

`node ops/release/pipeline.mjs verify /absolute/request.json` 接收 `candidateDirectory`（绝对路径）与 `candidateSha256`。入口只读核对 marker、规范 manifest、摘要 sidecar、全部制品和许可 notice，以及扩展 ZIP 内嵌身份；缺失、额外文件、非普通文件、symlink 或漂移均拒绝。返回 `scope=candidate-integrity-only`，不执行候选脚本或容器；结果不永久信任可变目录，消费前须重核实际字节。clean SNAPSHOT 可用于机制验证；正式发布还须匹配精确正式标签及独立验收报告中的同一候选摘要。


## 1.5. 本机候选运行输入

内部库 `runLocal(argv, { candidateDirectory, candidateSha256 })` 将固定候选接入原本机安装与升级事务，源码用户的 `install` / `upgrade` 命令不变。候选模式首装核验并加载 ARM64 镜像、解包固定扩展、用候选词库 ZIP 初始化。API 端口固定 18080，冲突时停止，不停止未知服务或改写扩展权限。升级仅加载候选 API 并准备扩展；PostgreSQL、资料、密码及端口不变。候选完整性、替身回归、真实同卷运行和独立 Formal 是不同证据，前两项不取代后两项。


## 1.6. 实际候选原生验收

`eng.release.candidate-runtime` 消费 `LEXIFLOW_CANDIDATE_RUNTIME_REQUEST` 指定的绝对 JSON 路径。请求只含 `previous` 和 `target`（各为 `candidateDirectory` + `candidateSha256`），target 须与干净源码完整身份一致。

Check 在 Apple Silicon macOS Podman 上调用共享安装入口，执行首装、重复安装、no-op、同卷升级、真实启动失败自动恢复及重试。18080 被占用则停止。断言覆盖 PostgreSQL 容器和卷、标记、资料、密码、端口、扩展及记录。运行结果通过 Verify stdout 附件绑定候选、manifest、build identity 和阶段。缺候选、缺宿主、恢复未观察到或清理未完成均不得 PASS。故障注入和清理仅限逐项核实归属的本次资源；无法确认进程静止或归属时保留现场。quick CI 排除此 Check。


## 1.7. 联合消费候选与既有验收

```sh
python3 -m scripts.delivery_gate consume-candidate --submission-id <uuid> --candidate-directory /absolute/candidate
```

从既有完整 Formal 链的 repository-baseline 报告取得候选 Check stdout，核对附件字节、摘要、冻结输入及运行阶段。候选摘要由证据派生，不由调用者另传。最终核验后重核链与 stdout 防止替换。源码身份采用两轮 Git 成员、字节与元数据核对，但这不是全局文件系统锁，也不保证返回后输入不再变化。消费过程不补签 receipt、不运行构建或容器；clean SNAPSHOT 不因此取得发布资格，公开晋升仍须精确标签、来源许可和发布授权。


## 1.8. 正式候选晋升入口

```sh
python3 -m scripts.environment.release_promotion --submission-id <uuid> --candidate-directory /absolute/candidate
```

默认核验并准备公开资产，不写 GitHub。用户另行批准后才加 `--publish`；workflow 或 token 存在不代替授权。入口复用联合证据，只接受干净正式版本、精确 tag 和单一 ARM64 候选。`ops/release/distribution-licenses.json` 须由负责人依据真实许可核准，与候选许可条目、来源及 notice 摘要一致；空表或测试占位阻断。

发行归档只含已验收 payload，随包保留扩展 ZIP。独立公开资产为发行 tar.gz 及摘要、Chrome 扩展 ZIP 及摘要、候选 marker、manifest 及摘要；独立 ZIP 从候选原样复制，晋升时不重新构建。不上传 Formal receipt、运行日志、字幕或观看记录。

远端先验证 tag 对应同一 commit 且不可变发行已启用，同 tag 的 draft 或 published release 拒绝覆盖。创建 draft 后上传固定资产，逐项核对名称、字节数与 SHA-256，完整复核后才 publish。失败不删除草稿或移动 tag；写请求未知时保留状态由负责人查明。参见 [GitHub 不可变发行](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)及 [Release API](https://docs.github.com/en/rest/releases/releases)。

发布凭据分离：`GITHUB_TOKEN` 仅需 Contents write；`LEXIFLOW_RELEASE_CONFIG_TOKEN` 仅作 Administration read 的不可变配置查询。两者仅供固定 GitHub HTTPS 目的地，不跟随重定向、不传入构建子进程或日志。参见 [不可变配置查询权限](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository)。


## 1.9. 构建资料与本机交接

负责人先通过 `ReleaseDatasetCommand export` 从已发布资料和真实 `approval-file` 导出发布 ZIP（含 `manifest.json`、`approvals.json`、`dataset.ndjson`、`entries.ndjson`、`forms.ndjson`），由实际 JAR 校验。`fetch-ecdict.sh` 生成的是源码词库 ZIP，不是此发布格式。

构建请求提供发布标签、资料包路径与摘要、notice 根、输出父目录、previous 候选及 Podman endpoint。JAR、扩展、SQL、Compose、基础镜像锁和许可关联由固定源码入口派生，不接受任意 shell 或完整 descriptor。首次发布也须准备真实且不同的 previous API 候选。

本机编排入口：

```sh
node ops/release/workflow.mjs prepare /absolute/private-request.json
```

请求字段：`releaseTag`、`dataset`（含 `path`/`sha256`/`releaseId`/`preparationId`/`ruleId`/`sqlVersion`）、`noticesRoot`、`outputParent`、`previous`（含 `candidateDirectory`/`candidateSha256`）、`endpoint`。`outputParent` 须已存在且为空，不在源码目录内。notice 路径与摘要来自受跟踪许可核准表，按 `id` 排序。

准备完成后固定 `tmp/quality/release-workflow/candidate-runtime-request.json` 供 Verify 使用；`handoff.json` 绑定 checkout、宿主和候选定位，均为私有本机文件不上传。

原生链完成后可只读核对：

```sh
node ops/release/workflow.mjs resume <submission-uuid> <release-tag>
```

发布时从原正式标签手动运行工作流，填写同一标签、submission UUID 和 `PUBLISH`。先 dry-check 再由 `lexiflow-release-promotion` 人工审批；只有发布步骤注入 Contents write 与 `LEXIFLOW_RELEASE_CONFIG_TOKEN`。管理员须先启用仓库 immutable releases。
