# 1. 发行准备：配置、资料与验收细节

> 上级：[第一次 GitHub Release](../first-release.md)。仅在配置或工程准备时查阅；每次发布按上级简明步骤操作。

## 1.1. GitHub 配置字段

管理员按[仓库管理员准备](../../github-actions.md#13-仓库管理员准备)配置私有与正式发布两个 Environment、唯一 runner、隔离服务、不可变发行与权限。`lexiflow-release-private` 无人工审批，仅给测试能力；`lexiflow-release-promotion` 保留 owner required reviewer，单维护者设置 `prevent_self_review=false`、`can_admins_bypass=false`。两者仅允许正式 `v*` tag。当前电脑可以承载 runner，但使用专用源码与隔离数据，不能让其他 workflow 或不可信 PR 在此执行。所需变量如下：

| 配置位置 | 名称 | 要填什么 |
| --- | --- | --- |
| private / promotion Variables | `LEXIFLOW_RELEASE_WORKSPACE` | 同一专用稳定 checkout 的规范绝对路径；普通 `.git` 目录，不是临时 Actions checkout |
| private / promotion Variables | `LEXIFLOW_RELEASE_NODE_BIN` | 同一已安装 Node 22 的 `bin` 规范绝对路径；不是会话临时 PATH，也不自动下载 |
| private Variables | `LEXIFLOW_RELEASE_REQUEST_FILE` | 下一节私有构建请求的绝对路径，放在源码外 |
| private / promotion Variables | `LEXIFLOW_RELEASE_JAVA_HOME` | Temurin 25 的 `Home` 绝对路径 |
| private / promotion Variables | `LEXIFLOW_RELEASE_TEST_REDIS_ENDPOINT` | 本次隔离测试 Redis 的 `host:port` |
| private / promotion Variables | `LEXIFLOW_RELEASE_ARM64_DOCKER_HOST` | 已确认的本机 Podman Unix endpoint，例如 `unix:///absolute/path/to/podman.sock` |
| private / promotion Variables | `LEXIFLOW_RELEASE_GRADLE_CACHE`、`LEXIFLOW_RELEASE_NPM_CACHE` | 仅含依赖的可用私有缓存绝对路径 |
| private / promotion Secrets | `LEXIFLOW_RELEASE_TEST_JDBC_URL` | 隔离 PostgreSQL 测试 JDBC，含测试账号认证 |
| promotion Secrets | `LEXIFLOW_RELEASE_CONFIG_TOKEN` | 仅对目标仓库具 Administration read 的配置查询凭据，用于只读查询 immutable releases 和正式 Environment 保护；不要替代为源码构建 token |
| repository Variables | `LEXIFLOW_RELEASE_RUNNER_READY` | 全部隔离、工具、保护和路径确认后设字面量 `true` |

`GITHUB_TOKEN` 由 GitHub 自动提供，不另造同名 secret；只有 promotion job 声明 Contents write / Packages write，额外 Actions read 用于重核本次运行及批准历史。确认仓库/组织策略允许这些权限。准备和预检阶段不得注入发布凭据。两个 Environment 的 workspace、Node 工具路径与验证能力必须完全相同；private 在私有构建和手动晋升的只读预检中都不等待批准；正式公开仅 promotion 等待一次真实人审。验证 JDBC 仅用于隔离测试，不是产品数据库凭据；晋升仍须当前环境重核，不能省略或伪造这些值。

手动运行工作流必须新建 dispatch，不重跑已批准 run；旧批准不无限复用。`confirmed_recovery` 默认不勾选。仅当此前发布状态未知、管理员已核实远端精确镜像与事务现场时才显式勾选；它不会跳过候选、tag、PUBLISH 或正式发布 Environment 的本次审批，也不会自动恢复或修改包可见性。远端镜像缺失、digest/config 不一致或现场仍不明确时停止，不要重试推送。

在专用 checkout 按[隔离验证环境](../../verification-environment.md#11-一次性准备运行时与镜像)创建 `.local/lexiflow-python` 并安装 `requirements-dev.txt`。发行 workflow 在构建和两步晋升中显式使用同一 Node 22 与该 venv。原生 正式验收 和晋升通过同一[受控执行环境](../../../../ops/release/workflow/release-execution-environment.mjs)固定工具 PATH、用户、locale、时区与临时根，过滤偶然的会话环境，再传入显式私有验证能力；缺工具或能力立即停止。Node 启动的 Python 子进程沿用该环境。不依赖 launchd 服务的全局 Python 恰好安装 PyYAML，也不在取得发布凭据后临时安装依赖。

<a id="12-首次发行材料没有旧公开-release-也不能省-previous"></a>

## 1.2. 首次发行材料

推 tag 前由维护者交接以下材料，逐项核实；不是从 quick artifact 或本机 `.local/podman` 凑一个目录：

- **完整许可核准表及 notice。** [`distribution-licenses.json`](../../../../ops/release/distribution-licenses.json) 必须受 Git 跟踪，按 `id` 排序，覆盖 `LexiFlow`、`API-runtime`、`PostgreSQL`、`extension-third-party`、`dataset` 五类实际组件；每项 notice 的路径、字节数、摘要须与真实文件一致。只有 LexiFlow/MIT 一个条目不足以发行。负责人先核实可再分发依据、补齐记录并提交，再选择最终发行 commit；不能把「工具要求五类」当作许可已经获批。
- **真实资料快照。** 从已发布且经真实来源审批的 Lexicon 数据导出，包含 `manifest.json`、`approvals.json`、`dataset.ndjson`、`entries.ndjson`、`forms.ndjson`。本机 ECDICT 来源 ZIP/CSV 不是发布快照；测试 synthetic 包也不是正式资料。
- **目标资料和版本说明。** 当前提交的 `ops/release/notes/<version>.md` 必须非空且版本匹配。单容器候选内嵌真实已发布资料，完整验收其初始化、同卷重启、故障和恢复；不要求历史双镜像 previous 候选，也不承诺任意历史数据库迁移。
- **当前正式发行 Task 与真实 actor。** 按 [Delivery Gate](../../../reference/delivery-gate.md) 确认 Catalog 中目标 Task 及正式发行计划，送验、验证、审查角色已分开。普通开发 Hook PASS 不够。

没有上述材料时停在此步，请维护者完成资料准备/许可核准/版本说明和精确目标构建；不能提供「首发跳过」验收开关。这是必须先完成的工程交接，不是后面的 GitHub 按钮可以补办的事情。

### 1.2.1. 导出并核对发布资料

在已确认资料来源数据库与真实审批文件的受控环境，用实际 API JAR 操作。`LEXIFLOW_RELEASE_JDBC_URL` 只用于这次显式导出，应通过私有环境安全注入；它不同于 CI 的测试 JDBC。不要把密码写进 shell 示例、历史或共享文档。

```sh
# 在干净源码根构建实际 JAR；通过既有 Java 工具链入口使用 Java 25。
python3 -m scripts.local.run_with_java_toolchain backend/gradlew -p backend --no-daemon :api:bootJar

# 已安全配置 LEXIFLOW_RELEASE_JDBC_URL，且下列输出父目录已存在。
python3 -m scripts.local.run_with_java_toolchain java \
  -jar backend/product/api/build/internal/boot/api-internal.jar \
  --release-dataset export \
  --output /absolute/private-inputs/dataset.zip \
  --approval-file /absolute/private-inputs/approved-sources.json

shasum -a 256 /absolute/private-inputs/dataset.zip
python3 -m scripts.local.run_with_java_toolchain java \
  -jar backend/product/api/build/internal/boot/api-internal.jar \
  --release-dataset verify \
  --package /absolute/private-inputs/dataset.zip \
  --expected-sha256 '<导出返回且复核一致的64位小写SHA-256>'
```

导出和校验均需结构化 `result=PASS`。审批文件固定结构见 [`DatasetPackageCodec.parseApprovals`](../../../../backend/product/adapters/src/main/java/io/lexiflow/lexicon/platform/persistence/DatasetPackageCodec.java)：`schemaVersion=1` 与 `sources`，每个来源含 `sourceId`、`sourceDigest`、`licenseId`、`redistributionApproved`、`evidenceUrl`。只有真实核准后才能写入肯定审批；不得从测试复制审批或以包摘要代替来源摘要。导出需已发布三表、来源匹配及资料内容校验，空库/未批准来源失败应回到资料准备，不改校验器。

### 1.2.2. 编写 runner 私有构建请求

把 JSON 放到 `LEXIFLOW_RELEASE_REQUEST_FILE` 指定路径。以下只是字段模板；所有占位值替换后才能提交给工作流：

```json
{
  "releaseTag": "v<正式三段版本>",
  "dataset": {
    "path": "/absolute/private-inputs/dataset.zip",
    "sha256": "<64位小写SHA-256>",
    "releaseId": "<负责人核定的资料发行ID>",
    "preparationId": "<负责人核定的准备策略ID>",
    "ruleId": "<负责人核定的规则ID>",
    "sqlVersion": "<负责人核定的SQL身份>"
  },
  "noticesRoot": "/absolute/private-inputs/notices",
  "outputParent": "/absolute/private-output/target",
  "endpoint": "unix:///absolute/path/to/podman.sock"
}
```

四个资料身份由负责人按实际资料与策略登记，不能任填占位名称；格式为 1–128 位英文数字及 `._-`，首字符英文数字。`outputParent` 须已存在且为空，不能在源码中、与源码互为父子；使用规范绝对路径，所有输入禁止 symlink。`endpoint` 只接受实际本机 Unix socket，须和测试 ARM64 endpoint 指向同一经确认的 daemon。notice 根必须含核准表中全部原样文件。

<a id="13-在原-checkout-完成真实独立-formal"></a>

## 1.3. 在原 checkout 完成独立正式验收

在同一 Mac、同一 OS 用户、同一稳定 checkout 中，由具有真实原生身份的独立 agent 执行；聊天启动目录不代替目标仓库根。原生任务不是 Actions job，不会自动继承 Environment secret：在验收宿主私有环境中安全配置同一隔离资源对应的 `LEXIFLOW_POSTGRES_TEST_JDBC_URL`、`LEXIFLOW_REDIS_TEST_ENDPOINT`、`LEXIFLOW_RELEASE_ARM64_DOCKER_HOST`、`LEXIFLOW_JAVA_HOME`、`LEXIFLOW_RELEASE_NODE_BIN` 及受要求的构建缓存，不在提示词或日志中粘贴 secret。下列 `formal` 入口从固定版本目录 `tmp/quality/release-workflow/<softwareVersion>/` 中的 handoff 旁派生 `candidate-runtime-request.json`，并与晋升共用受控环境；不要用临时 shell PATH 或不一致的私有配置执行这条发行链。执行身份与目标分开校验：同一聊天可明确访问多个 checkout，但切换路径不产生独立身份，不能由实现者依次自签。先确认 Catalog 中正式发行 Task 的必需检查与依赖已齐；干净提交通过固定 handoff 送验，不要求 PASS 开发报告，也不为此添加开发差异：

```sh
# 生产者/送验者：从本 checkout 的固定 handoff 派生真实发行主体。
"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs formal submit '<TASK_ID>'

# 不同真实 validator，在自己的原生任务里执行唯一完整发行基线：
"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs formal validate '<返回的SUBMISSION_UUID>'

# 不同于生产者与 validator 的 reviewer，只读冻结发行主体/evidence 后，在自己的任务里：
"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs formal review '<返回的VALIDATION_UUID>'

# 按计划核对 receipt/hash DAG；缺依赖或人类批准不补签、不绕过。
"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs formal check '<同一SUBMISSION_UUID>'

# 负责人只读晋升预检，不发布、不需要发布 token。
"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs resume '<同一SUBMISSION_UUID>' "$TAG"
```

`formal` 只把固定角色命令交给既有 Delivery Gate，保留实际 Codex 身份来源；它不代替审阅或授予独立身份。审查发现问题时可在 `formal review` 的 validation ID 后传入既有 findings JSON 文件的绝对路径，不能发布无发现意见冒充已审查。必须是完整正式发行链、`repository-baseline` 与同一候选的实际运行 stdout 绑定；只读审查无意见时才可形成无 findings 的 review，不把默认空列表当作已经审过。命令返回 `BLOCKED/FAIL` 时保留记录，按具体缺项处理。正式链 PASS 且 `resume` 返回 `status=PASS` 后才进入手动晋升。不要编辑/格式化/切换源码、移动候选/请求、重新构建 JAR/ZIP 或另换验收机；任何输入变化都使后续复核失效。

发行交接使用操作系统稳定宿主标识与实际用户的摘要绑定，不使用可变化的主机名；原始系统标识不写入交接或日志。准备、正式验收和晋升仍分别重新核对同一宿主用户、规范仓库根、精确提交及候选摘要。来源不可用或宿主、用户、路径、候选变化时停止，不允许手填身份兜底；系统重装或主机克隆不承诺复用旧交接。当前实现见 [宿主绑定](../../../../ops/release/host_resources/release_host_identity.py)。

## 1.4. 首次 GHCR 可见性

首次个人 GHCR package 默认为 private。只有完整独立验收、自动预检及唯一一次正式发布批准通过后，才从已验候选推送 `ghcr.io/iampkuhz/lexiflow:<版本>`；绝不以预建包为由提前公开未验候选。

首次新推送经认证读回 digest/config 且与原候选一致后，如果匿名读取明确返回 401/403，发布入口提供一次固定 120 秒的包可见性配置窗口。负责人可在已登录的独立浏览器 **Packages → 本次 package → Package settings** 将它设为 public；这是一项首包设置，不是第二道发行审批。入口不自动修改 GitHub 包设置、不重推镜像、不重试网络未知响应，也不增加已有 HTTP、Check 或 Actions job 的超时。窗口前后仍完整核对当次授权与证据，最终必须匿名读到相同 digest/config 才继续 Release 写入。已有相同镜像的只读恢复不进入该窗口。

若配置能力不可用、窗口结束仍私有，或发生其他未知状态，事务仍失败关闭并保留写入记录；不能把镜像推送成功算整个发行成功。先核实远端已完整推送且精确摘要一致、此前请求及 draft 状态明确，再处理可见性。失败恢复必须新 dispatch，通过自动预检，并取得该次恢复唯一一次正式批准；不重跑旧已批准 run。原事务存在未知事件时，只有已核实现场才设置 `confirmed_recovery`；它不允许重新推送、删除或覆盖未知远端内容。远端缺失或 digest/config 不符继续拒绝。

参考 [GitHub 包可见性配置](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)；认证拉取成功不能替代匿名可读证明。

## 1.5. 失败去向

| 现象 | 处理 |
| --- | --- |
| 没有 Run workflow / job 全部 skipped | 先确认 workflow 在默认分支、有写权限、ref 是原 tag，三个身份参数完全匹配，恢复选项仅按已核实事务显式设置；skipped 不代表 PASS。 |
| source 拒绝 / runner 排队 | 核对正式版本、精确 tag、repository readiness 和唯一 Mac labels/在线状态；不要创建第二台同标签宿主。 |
| `LICENSE_LEDGER_INVALID` / `NOTICE_MISMATCH` | 回到许可核准，不能填占位 license 或重写 notice 摘要掩盖不符；源码改变后重选未使用发行身份。 |
| dataset 校验失败 | 回到材料准备；资料必须由实际 Java 导出并经真实审批。 |
| `WORKFLOW_RESIDUE` / 输入漂移 / 正式验收 缺链 | 保留原目录、日志和链，确定失败原因后由负责人按事务边界处理；不删 handoff、移动源码或复制 PASS receipt。 |
| 18082 验收端口冲突 / Docker endpoint 不可用 | 核对隔离宿主和 Unix socket；不杀用户服务，不更改正式插件端口。 |
| GHCR private / registry 验证失败 | 只有已核实的完整相同镜像与确定远端状态可按上节修可见性后续办；认证/网络未知时先停止。 |
| 同 tag 已有 draft/published Release 或写请求结果未知 | 保留远端现场，负责人核对来源和资产；不自动删 draft、不重复写、不移动 tag、不 force。公开不可变资产不能补换。 |

不上传 正式验收 receipt、运行日志、私有请求、凭据、真实字幕或观看记录。需要更新说明可改 Release notes，但不借此变更不可变制品；资产错误须重新准备新的正式版本和验收链。本文指导执行，不证明 GitHub 设置、材料核准、独立链或真实下载/安装已经完成。

## 1.6. Runner 安装与日常管理

唯一入口是 [`run-release-workflow.mjs`](../../../../ops/release/workflow/run-release-workflow.mjs) 的 `runner` 子命令；仅支持当前 macOS ARM64、固定仓库 `iampkuhz/lexiflow`，安装在源码外的 `~/.local/share/lexiflow/actions-runner`，不把 runner 安装到稳定发行 checkout。默认名称由 `lexiflow-release-` 与宿主名称组成，标签为 `lexiflow-release`；不要另建同标签发行宿主。

首次需已安装 Homebrew 与 Node，并以当前用户完成 `gh auth login --hostname github.com --git-protocol ssh`。若已登录且能管理仓库 runner，无需再登录；权限不足由仓库管理员授权，不把长期 PAT 或网页注册 token 发到聊天、写入命令历史或 Git。

| 子命令 | 作用与边界 |
| --- | --- |
| `deps` | 按唯一 [Brewfile](../../../../ops/release/runner/Brewfile) 安装缺失的 CLI，不升级已装包；保留 Docker 配置其他字段，补充 Homebrew Compose plugin 搜索路径，不改 Docker daemon/context。 |
| `install` | 从官方 API 选择 macOS ARM64 包并核对 SHA-256，安装到独占私有目录；不覆盖未知安装。 |
| `register` | 通过 `gh api` 获取短期 token 并注册，不替换同名远端 runner；注册前后及中断协调时核对唯一发行标签宿主，冲突或列表不完整则停止。 |
| `setup` | 顺序组合 `install`、`register` 与 `start`；不代替 `deps`、资料准备或发行人审。 |
| `start` / `stop` | 复用官方 `svc.sh` 管理同用户 launchd 服务；启动前核对唯一发行标签宿主；停止只操作本机已确认绑定，核对远端 busy 状态，忙碌或无法确认时不强杀。 |
| `status` | 核对唯一发行标签宿主、本机注册、服务及 GitHub 实际在线状态；停止状态不是 Online。 |
| `doctor` | 只读检查工具、权限与连接；不是完整 Verify、正式验收 或 RUNNER_READY 凭据。 |

日常只用：

```sh
node ops/release/workflow/run-release-workflow.mjs runner status
node ops/release/workflow/run-release-workflow.mjs runner stop
node ops/release/workflow/run-release-workflow.mjs runner start
```

服务按登录用户运行，不用 `sudo`、不另用 `brew services` 管同一进程；登录后自动启动，休眠/离线时不能执行发行。官方 runner 默认自动更新，不执行 `brew upgrade runner`。安装凭据仅留在源码外的私有官方目录，临时注册 token 不由工具保存或打印。[官方服务管理](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=mac)

遇到权限不足先修复 `gh` 登录/授权；未知目录、绑定不符或部分注册失败先核对本地与远端实际状态，不清空目录、加 `--replace` 或盲目重试。若官方配置已完整生成、只是后续确认中断，`register` 会只读核对本机 ID、仓库与远端绑定后恢复状态，不再次注册；缺件或绑定不符仍停止。`deps` 不安装 Python venv 依赖、Playwright、Java 替代发行工具链或启动/清理 Podman machine；这些按[验证环境](../../verification-environment.md)准备。本机 API 与数据库不会由 runner 管理命令暂停或清除。GitHub 的受保护 Environment、immutable releases 和发行变量仍由管理员单独配置。
