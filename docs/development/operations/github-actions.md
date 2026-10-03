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

`node ops/release/pipeline.mjs verify /absolute/request.json` 接收仅含 `candidateDirectory`（绝对路径）与 `candidateSha256` 的请求。入口只读核对现有 marker、规范 manifest、摘要 sidecar、全部制品和许可 notice，以及扩展 ZIP 内嵌身份；目录缺失、额外文件、非普通文件、symlink 或读取中漂移均拒绝。返回 `scope=candidate-integrity-only`，不执行候选脚本或容器，也不表示正式发布许可。

运行适配器和晋升入口应消费同一候选完整性结果，并在实际使用前再次核对，不能把一次核验作为之后可变目录的永久信任。clean SNAPSHOT 可用于候选机制验证；正式发布还必须匹配精确正式标签及独立验收报告中绑定的同一候选摘要。


## 1.5. 本机候选运行输入

内部库 `runLocal(argv, { candidateDirectory, candidateSha256 })` 将固定候选接入原本机安装与升级事务；不提供任意命令或平台绕过，源码用户的 `install` / `upgrade` 命令保持不变。候选模式首装不编译源码，而是核验并加载 ARM64 镜像、解包固定扩展、用候选词库 ZIP 初始化。API 端口固定为 18080，冲突时停止，不能停止未知服务或重写扩展权限。

升级仅加载候选 API 并准备扩展；PostgreSQL、已有资料、密码及端口保持不变。候选、manifest 与归档摘要写入安装记录，不执行候选中的 shell 入口。候选完整性、替身回归、真实同卷运行和独立 Formal 是不同证据，前两项不能取代后两项。


## 1.6. 实际候选原生验收

`eng.release.candidate-runtime` 及其 change 视图消费 `LEXIFLOW_CANDIDATE_RUNTIME_REQUEST` 指定的绝对 JSON 路径。请求只包含 `previous` 和 `target`，每项均为 `candidateDirectory` 与 `candidateSha256`；不接受安装目录、命令或成功声明。target 必须与执行仓库的干净源码完整身份一致。两个候选构建和 API 镜像不同，数据库结构相容；干净 SNAPSHOT 运行不等于正式标签验收。

Check 在 Apple Silicon macOS 的既有 Podman 上调用共享安装入口，在自身临时安装中执行首装、重复安装、no-op、同卷升级、真实启动失败自动恢复及重试。固定 18080 被占用则停止，不修改扩展权限、不停止未知服务、不更改 machine。保留断言覆盖 PostgreSQL 容器和卷、合成标记、资料、密码、端口、扩展及记录；资源清理必须重新核验归属与进程静止。

运行结果通过 Verify 的 stdout 附件摘要绑定候选、manifest、完整 build identity 和实际阶段；原始私有运行日志只留 ignored 本机目录，不上传 CI。缺候选、缺宿主、失败恢复未观察到或清理未完成均不得 PASS。quick CI 显式排除此 Check；完整 Verify、候选来源许可与独立 Formal receipt 仍分别必需。


## 1.7. 联合消费候选与既有验收

```sh
python3 -m scripts.delivery_gate consume-candidate --submission-id <uuid> --candidate-directory /absolute/candidate
```

接口从已有完整 Formal 链绑定的 repository-baseline 报告取得唯一实际候选 Check 的 stdout，核对附件字节、摘要、执行关联和冻结输入，再核对全部运行阶段与清理结果。候选摘要和完整身份由该证据派生，不能由调用者另传；固定只读入口复用候选完整性校验，并要求目录制品与干净源码对应受验身份。

最终候选核验结束后再重核链、报告及 stdout，防止核验耗时期间证据被替换。源码身份采用固定两轮 Git 成员、字节与文件元数据核对，拒绝可观测的读取中漂移；这不是全局文件系统锁，消费者不保证返回后输入仍不变。消费过程不补签 receipt、不运行构建或容器。缺少证据、内容漂移、无关 PASS、合成桥接或快速检查不能通过。输出只证明同一候选与既有验收链的关联；clean SNAPSHOT 不因此取得正式发布资格，公开晋升仍须精确标签、来源许可、远端不可替换配置和发布授权。


## 1.8. 正式候选晋升入口

```sh
python3 -m scripts.environment.release_promotion --submission-id <uuid> --candidate-directory /absolute/candidate
```

默认核验并准备公开资产，不写 GitHub。只有用户另行批准后才加 `--publish`；workflow 或 token 的存在不代替该授权。入口复用同宿主的联合证据，只接受干净正式版本和精确 tag、单一 ARM64 候选。`ops/release/distribution-licenses.json` 必须先由负责人依据真实许可核准，并与候选的每个许可条目、来源及 notice 摘要一致；空表或测试占位材料阻断。程序的字段/摘要核对不是法律许可判断。

发行归档只包含已验收 payload；随包保留扩展 ZIP，不把 ZIP 描述为一键安装文件。独立公开资产仅为发行 tar.gz、其摘要、候选 marker、manifest 和 manifest 摘要，不上传 Formal receipt、运行日志、字幕或观看记录。缺件、多余项、路径异常、读取漂移及大小越界均停止。

远端先验证已有 tag 对应同一 commit、不可变发行已启用，任何同 tag 的 draft 或 published release 都拒绝覆盖。创建 draft 后上传固定资产，逐项检查远端名称、字节数与 SHA-256，完整复核后才 publish 并确认 immutable 与 latest。失败不删除草稿或移动 tag；写请求结果未知时保留不确定状态，先由负责人查明，不自动重试。参见 [GitHub 不可变发行](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)及 [Release API](https://docs.github.com/en/rest/releases/releases)。

发布凭据与配置查询分开：`GITHUB_TOKEN` 仅需目标仓库 Contents write；`LEXIFLOW_RELEASE_CONFIG_TOKEN` 仅作 Administration read 的不可变配置查询，不需要管理写权限。后者不能用普通 Actions 的 `contents:write` 权限冒充；无此查询权限时阻断，不自动启用配置。两者仅供固定 GitHub HTTPS 目的地，不跟随重定向、不传入构建子进程或日志。参见 [不可变配置查询权限](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository)。


## 1.9. 构建资料与本机交接

构建编排不负责自动批准来源或生产数据库资料。负责人先通过既有 `ReleaseDatasetCommand export` 从已发布资料和真实 `approval-file` 导出发布 ZIP；该包包含 `manifest.json`、`approvals.json`、`dataset.ndjson`、`prepared.ndjson`、`lookup.ndjson`，由实际构建的 JAR 校验。`fetch-ecdict.sh` 生成的是源码词库 ZIP，不是这一发布格式；不能用它、空 ZIP 或合成机制 fixture 替换。

构建请求只提供发布标签、资料包绝对路径与摘要及资料身份、已核准 notice 根、独立输出父目录、真实 previous 候选定位与摘要，以及已运行的 Podman Unix endpoint。JAR、扩展、SQL、Compose、基础镜像锁和许可关联由固定源码入口派生，不接受调用者传任意 shell、构建身份、PASS 或完整 descriptor。首次正式发布也必须准备真实且不同的 previous API 候选；若没有，应先完成该输入，不把 target 复制一份冒充升级验收。

候选与原始构建日志保留在该宿主的私有目录。交接文件仅是查找原候选和 runtime request 的定位器，不是验收 receipt。已有交接或不完整准备现场阻断新的准备；不要靠删除现场、另建安装或换端口继续。负责人先核实归属和完成状态，再决定是否开始新的发行。原生验收与手动晋升之间不得修改源码、候选或资料，不能迁移 checkout 或仅拷贝验收记录。


本机编排入口：

```sh
node ops/release/workflow.mjs prepare /absolute/private-request.json
```

请求的固定字段为 `releaseTag`、`dataset`、`noticesRoot`、`outputParent`、`previous`、`endpoint`。其中 `dataset` 仅含 `path`、`sha256`、`releaseId`、`preparationId`、`ruleId`、`sqlVersion`；`previous` 仅含 `candidateDirectory`、`candidateSha256`。`outputParent` 是本次独立、已存在且为空的私有构建输出父目录，不是用户安装目录，也不能在源码目录内。notice 相对路径与摘要来自受跟踪的许可核准表，不另传许可声明；表内条目按 `id` 排序，与规范 manifest 的许可顺序一致。

准备完成后，固定 `tmp/quality/release-workflow/candidate-runtime-request.json` 供完整 Verify 使用；Actions 通过 `GITHUB_ENV` 传入 `LEXIFLOW_CANDIDATE_RUNTIME_REQUEST`，本机原生任务也必须设置该变量指向原文件。`handoff.json` 绑定原 checkout、宿主、源码身份和候选定位；两者均是私有本机文件，不上传。检查成功不代表原生验收已经完成。

原生链完成后可在原 checkout 只读核对：

```sh
node ops/release/workflow.mjs resume <submission-uuid> <release-tag>
```

显式批准发布时，从原正式标签手动运行工作流，填写同一标签、submission UUID 和 `PUBLISH`。工作流先执行无发布凭据的 dry-check，再由 `lexiflow-release-promotion` 独立人工审批；只有发布步骤注入 GitHub Contents write 与 `LEXIFLOW_RELEASE_CONFIG_TOKEN` secret（Administration read）。管理员必须先启用仓库 immutable releases；流程不代为修改设置。直接本机调用加 `--publish` 同样须另获发布授权。
