# 1. 第一次 GitHub Release：按顺序操作

> 位置：[仓库生命周期](../../README.md) → [GitHub Actions](../github-actions.md) → 第一次发行。本文是维护者操作手册；下载者按[发行安装步骤](../docker-release.md)操作。

本项目**不是点击 GitHub「Draft a new release」并上传本机 ZIP 就完成发行**。实际顺序是：管理员准备 → 资料/许可和 previous 候选 → 正式源码与 tag → `Release validation` 准备候选 → 原 checkout 的独立 Formal → 原 tag 手动晋升 → 核对公开资产。源码入口为 [`release.yml`](../../../.github/workflows/release.yml) 与 [`run-release-workflow.mjs`](../../../ops/release/workflow/run-release-workflow.mjs)。

以下命令供负责人在各步骤条件满足后人工执行，不是文档已执行的证明。`<...>` 和 `/absolute/...` 都必须替换，不能原样运行。遇到必需项缺失或 `FAIL` / `BLOCKED` 就停止；不跳 Gate、不手造 PASS、不移动已使用的 tag。

## 1.1. 一次性管理员准备：先于推 tag

1. 将经过审核的发行源码和 `.github/workflows/release.yml` 集成到 GitHub 默认分支，等待对应 `Quick checks` 成功。手动 `workflow_dispatch` 要求 workflow 已在默认分支；本机 commit 尚未 push 不满足这个条件。参见 [GitHub 手动 workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。
2. 在仓库 **Settings → Actions → Runners → New self-hosted runner** 按 [GitHub 自托管 runner 注册步骤](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners) 为 Mac ARM64 注册，准备唯一专用 Apple Silicon Mac，补自定义标签 `lexiflow-release`；工作流要求标签 `self-hosted, macOS, ARM64, lexiflow-release`。不使用日常开发目录、实际用户安装或普通 PR runner。工具、隔离 PostgreSQL/Redis、稳定 checkout 与真实 Codex 宿主按[管理员准备](../github-actions.md#13-仓库管理员准备)配置；不要让不可信 PR 或普通分支在此宿主执行。
3. 在 **Settings → Environments** 预先创建 `lexiflow-release-validation` 与 `lexiflow-release-promotion`。两者设 Required reviewers、Prevent self-review、禁止管理员 bypass，并设置 **Selected branches and tags → Tag → `v*`**；同时用 tag ruleset 限制正式标签创建/修改者。每个 Environment 至少需要能独立批准的负责人。GitHub 的 reviewers 列表不是「所有人都必须批准」，本流程由两个 Environment 分别把关。计划或私有仓库不支持必需保护时，先解决权限/计划，不以无保护 Environment 替代。参见 [GitHub Environment 配置与可用性](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)。
4. 在 **Settings → General → Releases** 启用 **Enable release immutability**，在第一次公开发布前完成；它仅适用于随后发布的 Release。参见 [GitHub 不可变发行设置](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes)。
5. 将下表配置到指定位置。路径都指向 runner 上同一 OS 用户可读的真实资源；不提交到 Git，不打印 secret，不让测试数据库指向用户安装。

| 配置位置 | 名称 | 要填什么 |
| --- | --- | --- |
| 两个 Environment 的 Variables | `LEXIFLOW_RELEASE_WORKSPACE` | 同一专用稳定 checkout 的规范绝对路径；普通 `.git` 目录，不是临时 Actions checkout |
| validation Variables | `LEXIFLOW_RELEASE_REQUEST_FILE` | 下一节私有构建请求的绝对路径，放在源码外 |
| validation Variables | `LEXIFLOW_RELEASE_JAVA_HOME` | Temurin 25 的 `Home` 绝对路径 |
| validation Variables | `LEXIFLOW_RELEASE_TEST_REDIS_ENDPOINT` | 本次隔离测试 Redis 的 `host:port` |
| validation Variables | `LEXIFLOW_RELEASE_ARM64_DOCKER_HOST` | 已确认的本机 Podman Unix endpoint，例如 `unix:///absolute/path/to/podman.sock` |
| validation Variables | `LEXIFLOW_RELEASE_GRADLE_CACHE`、`LEXIFLOW_RELEASE_NPM_CACHE` | 仅含依赖的可用私有缓存绝对路径 |
| validation Secrets | `LEXIFLOW_RELEASE_TEST_JDBC_URL` | 隔离 PostgreSQL 测试 JDBC，含测试账号认证 |
| promotion Secrets | `LEXIFLOW_RELEASE_CONFIG_TOKEN` | 仅对目标仓库具 Administration read 的配置查询凭据，用于查询 immutable releases；不要替代为源码构建 token |
| repository Variables | `LEXIFLOW_RELEASE_RUNNER_READY` | 全部隔离、工具、保护和路径确认后设字面量 `true` |

`GITHUB_TOKEN` 由 GitHub 自动提供，不另造同名 secret；只有 promotion job 声明 Contents write / Packages write。确认仓库/组织策略允许这些权限。准备和预检阶段不得注入发布凭据。两个 Environment 的 workspace 必须完全相同；validation 在手动晋升时也会再次要求批准。

## 1.2. 首次发行材料：没有旧公开 Release 也不能省 previous

推 tag 前由维护者交接以下材料，逐项核实；不是从 quick artifact 或本机 `.local/podman` 凑一个目录：

- **完整许可核准表及 notice。** [`distribution-licenses.json`](../../../ops/release/distribution-licenses.json) 必须受 Git 跟踪，按 `id` 排序，覆盖 `LexiFlow`、`API-runtime`、`PostgreSQL`、`extension-third-party`、`dataset` 五类实际组件；每项 notice 的路径、字节数、摘要须与真实文件一致。只有 LexiFlow/MIT 一个条目不足以发行。负责人先核实可再分发依据、补齐记录并提交，再选择最终发行 commit；不能把「工具要求五类」当作许可已经获批。
- **真实资料快照。** 从已发布且经真实来源审批的 Lexicon 数据导出，包含 `manifest.json`、`approvals.json`、`dataset.ndjson`、`entries.ndjson`、`forms.ndjson`。本机 ECDICT 来源 ZIP/CSV 不是发布快照；测试 synthetic 包也不是正式资料。
- **不同源码提交的 previous 完整 ARM64 候选。** 由维护者从独立、干净的前置源码使用现有 [images / assemble / verify pipeline](../../../ops/release/workflow/run-release-build-pipeline.mjs) 准备，交接实际候选目录和 `candidate.json` 的摘要。它可保持私有，不要求先公开另一个 GitHub Release；但必须包含可真实运行的 API、PostgreSQL、配套扩展、资料、SQL 与许可材料。不能让 previous 和 target 指向同一 commit，也不能使用 `test_tools` 的合成候选冒充。首次公开发行同样需要真实升级/失败恢复验收。
- **当前正式发行 Task 与真实 actor。** 按 [Delivery Gate](../../reference/delivery-gate.md) 确认 Catalog 中目标 Task 及正式发行计划，送验、验证、审查角色已分开。普通开发 Hook PASS 不够。

没有上述材料时停在此步，请维护者完成资料准备/许可核准/previous 构建；现有 `prepare` 命令要求 previous，不提供「首发跳过」开关。这是必须先完成的工程交接，不是后面的 GitHub 按钮可以补办的事情。

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

导出和校验均需结构化 `result=PASS`。审批文件固定结构见 [`DatasetPackageCodec.parseApprovals`](../../../backend/product/adapters/src/main/java/io/lexiflow/lexicon/platform/persistence/DatasetPackageCodec.java)：`schemaVersion=1` 与 `sources`，每个来源含 `sourceId`、`sourceDigest`、`licenseId`、`redistributionApproved`、`evidenceUrl`。只有真实核准后才能写入肯定审批；不得从测试复制审批或以包摘要代替来源摘要。导出需已发布三表、来源匹配及资料内容校验，空库/未批准来源失败应回到资料准备，不改校验器。

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
  "previous": {
    "candidateDirectory": "/absolute/private-output/previous/releases/<候选目录>",
    "candidateSha256": "<previous candidate.json 的64位小写SHA-256>"
  },
  "endpoint": "unix:///absolute/path/to/podman.sock"
}
```

四个资料身份由负责人按实际资料与策略登记，不能任填占位名称；格式为 1–128 位英文数字及 `._-`，首字符英文数字。`outputParent` 须已存在且为空，不能在源码中、与源码互为父子，或与 previous 目录互为父子；使用规范绝对路径，所有输入禁止 symlink。`endpoint` 只接受实际本机 Unix socket，须和测试 ARM64 endpoint 指向同一经确认的 daemon。notice 根必须含核准表中全部原样文件。

## 1.3. 提交正式源码并推送精确 tag

1. 负责人完成审核与集成，将发行 commit 推到 GitHub 默认分支。发行版本仅由 [`ops/release/version.txt`](../../../ops/release/version.txt) 决定：必须是正式 `x.y.z`，不能有 `-SNAPSHOT`；修改版本/许可/文档后先提交再构建，不在验证窗口改源码。选择尚未使用的版本，不能覆盖已有 tag/Release/GHCR 版本。
2. 在干净、已同步的源码根检查目标 commit 和 tag。确认本地及远端均无该 tag、同名 Release；查询失败时不是「不存在」。下一块包含真实 Git 写入，仅在负责人决定发行该 commit 后执行。

```sh
# 本机源码根，先确认 status 没有任何 tracked/untracked 改动。
git status --short
git rev-parse HEAD
VERSION=$(tr -d '\r\n' < ops/release/version.txt)
TAG="v$VERSION"
# 人工确认 VERSION 是正式三段版本、TAG 未使用，且 HEAD 正是已推送的发行 commit。
git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}"

# 仅在上面查询成功且确认没有同名 tag/Release 后执行；不 force、不移动。
git tag -a "$TAG" -m "LexiFlow $VERSION"
node ops/release/integrity/source-build-identity.mjs --tag "$TAG"
# 上一步成功后才推标签，触发 Release validation 的 source job。
git push origin "refs/tags/$TAG"
```

标签值也要与私有请求的 `releaseTag` 完全一致。不要先在 GitHub Releases 网页创建同名 draft：晋升器拒绝覆盖既有 draft 或 published Release。

## 1.4. 固定 runner checkout，再批准候选准备

GitHub **Actions → Release validation** 找到这个 tag 的 push run；`source` 应通过，然后 `prepare` 等待 `lexiflow-release-validation` 审批。**先不要批准。** 在专用 Mac 上准备稳定源码路径；首次使用可手工新建独立 clone，后续不换路径：

```sh
# WORKSPACE 必须是管理员配置的独立绝对路径；首次 clone 时该路径尚不存在。
WORKSPACE='/absolute/dedicated/lexiflow-release'
git clone git@github.com:iampkuhz/lexiflow.git "$WORKSPACE"
cd "$WORKSPACE"
git fetch origin --tags
# TAG 在此终端重新设为本次精确正式标签。
TAG='v<正式三段版本>'
git checkout --detach "$TAG"
git status --short
node ops/release/integrity/source-build-identity.mjs --tag "$TAG"
```

已有专用 checkout 只能在确认没有在途工作流/原生验收且未承载需要保留的交接后由负责人准备；不重新 clone 覆盖、不 `clean/reset` 清理证据。确认以下条件后，由独立审核者在 Actions run 的 **Review deployments** 批准 validation（[GitHub 审批操作](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments)）：

- `HEAD` 等于 source job 的完整 commit，工作树干净，当前源码内没有 `.local/lexiflow/runtime.json`。
- `tmp/quality/release-workflow` 尚不存在；若已经有 handoff/prepare residue，先查明上一事务，不删除后机械重跑。
- 私有请求可读、输出父目录空、许可和 dataset/previous 字节已核验。
- 已装好全部工具与隔离服务，`podman info`、`podman compose version`、指向同一 Unix endpoint 的 `docker info`/`docker compose version` 成功；candidate-runtime 所需 `127.0.0.1:18080` 空闲。不得停止现有用户服务腾端口，应使用独立 runner 环境。
- 负责人在同 OS 用户、同源码路径具可读取真实 actor 元数据的 Codex 环境；不是复制一份 Gate JSON。

工作流自动运行 `prepare` 和完整 Repository Verify，不需要另手跑一次 `prepare`。绿色 prepare job 只表示候选与工程 Verify，不表示公开 Release 已创建。将候选目录、摘要及开发 report ID 从私有 handoff/报告交给验收负责人；保留原路径、原字节和测试环境。`handoff.json` 与 `candidate-runtime-request.json` 留在原 checkout 的 ignored `tmp/quality/release-workflow/`，不要上传。

## 1.5. 在原 checkout 完成真实独立 Formal

在同一 Mac、同一 OS 用户、同一稳定 checkout 的真实 Codex 任务里执行。原生任务不是 Actions job，不会自动继承 Environment secret：在验收宿主私有环境中安全配置同一隔离资源对应的 `LEXIFLOW_POSTGRES_TEST_JDBC_URL`、`LEXIFLOW_REDIS_TEST_ENDPOINT`、`LEXIFLOW_RELEASE_ARM64_DOCKER_HOST`、`LEXIFLOW_JAVA_HOME` 及受要求的构建缓存，不在提示词或日志中粘贴 secret。不同角色不能在一个实现者会话里依次自签。先按[任务送验步骤](../run-repository-gates.md)确认正式发行 Task、开发报告和依赖要求，再通过唯一 CLI 交接：

```sh
# 生产者/送验者：TASK_ID 与 CHANGE_REPORT_ID 来自本次实际 Task 和 PASS 开发报告。
python3 -m scripts.acceptance submit --task-id '<TASK_ID>' --change-report-id '<CHANGE_REPORT_ID>'

# 不同真实 validator，在自己的原生任务里：
python3 -m scripts.acceptance validate --submission-id '<返回的SUBMISSION_UUID>'

# 不同于生产者与 validator 的 reviewer，只读冻结 diff/evidence 后，在自己的任务里：
python3 -m scripts.acceptance review --validation-id '<返回的VALIDATION_UUID>'

# 按计划核对 receipt/hash DAG；缺依赖或人类批准不补签、不绕过。
python3 -m scripts.acceptance check --submission-id '<同一SUBMISSION_UUID>'

# 负责人只读晋升预检，不发布、不需要发布 token。
node ops/release/workflow/run-release-workflow.mjs resume '<同一SUBMISSION_UUID>' "$TAG"
```

必须是完整正式发行链、`repository-baseline` 与同一候选的实际运行 stdout 绑定；只读审查无意见时才可形成无 findings 的 review，不把默认空列表当作已经审过。命令返回 `BLOCKED/FAIL` 时保留记录，按具体缺项处理。正式链 PASS 且 `resume` 返回 `status=PASS` 后才进入手动晋升。不要编辑/格式化/切换源码、移动候选/请求、重新构建 JAR/ZIP 或另换验收机；任何输入变化都使后续复核失效。

## 1.6. 原 tag 手动晋升与首次 GHCR 可见性

用已登录且对仓库有写权限的 GitHub CLI，从负责人终端发起手动 workflow；`gh` 是人工操作工具，不是构建 runner 的额外产品依赖：

```sh
TAG='v<本次已准备的正式版本>'
SUBMISSION_ID='<本次完整Formal的SUBMISSION_UUID>'
gh workflow run release.yml --repo iampkuhz/lexiflow --ref "$TAG" \
  -f release_tag="$TAG" \
  -f submission_id="$SUBMISSION_ID" \
  -f publish_confirmation=PUBLISH
```

**必须显式 `--ref "$TAG"`**。只在默认分支点 Run workflow 或填 `release_tag` 而 ref 仍是分支，不能通过 job 条件。CLI 避免网页 ref 选择不明；GitHub 手动 workflow 的前提和参数见[官方操作说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)。

在 **Actions → Release validation** 找到本次手动 run，核对 ref、commit 与 submission：先由独立审核者批准 validation，让 `promotion-precheck` 只读核对既有候选/链；成功后再独立批准 `lexiflow-release-promotion`。不要向 prepare/precheck 传 token，不用 `gh release create` 绕过晋升器。`PUBLISH` 是明确发布意图，不是验收结论。

首次 GHCR package 通常为 private。发布从已验候选加载并推送 `ghcr.io/iampkuhz/lexiflow-api:<版本>` 与 `ghcr.io/iampkuhz/lexiflow-postgres:<版本>`，然后验证匿名 digest 读取；它**不会自动改 package 可见性**。如果此处因 private 失败：

1. 先查明报告与远端实际状态。只有确认两份镜像均已完整推送、摘要与本候选一致，且没有已创建 draft/未知写请求，才继续；不能看到失败就盲目重跑。
2. 管理员从个人/组织 **Packages → 对应 package → Package settings** 将两份都设为 public，并核对仓库关联、Actions package 访问权限。依据 [GitHub Container registry 配置](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)，不要把认证拉取成功当作匿名可读。
3. 修复可见性后，重新消费原候选/原 Formal，通过只读 `resume` 后再手动发起原 tag 的同参数 workflow，并重新取得两道 Environment 批准。同版本 tag 只允许复用相同镜像，禁止覆盖不同内容。若已有 draft/远端写入未知，按下一节停下，由负责人先查明，不自动删除/重新上传。

## 1.7. 完成判据与失败处理

只有晋升 job 返回 PASS 且公开远端状态复核成功，才称 GitHub Release 发布完成。进入仓库 **Releases → 本次 tag → Assets** 核对：

- 发行 `tar.gz` 及摘要、**独立 Chrome 扩展 ZIP 及 `.zip.sha256`**。
- `candidate.json`、`manifest.json` 及其摘要、`registry-images.json` 及摘要；实际文件名以晋升结果与清单为准。
- 完整 commit 与 tag 精确对应、资产名称/字节数/摘要与已验候选一致，Release 显示 Immutable；两份 GHCR 镜像可按 `manifestDigest` 匿名获取。

GitHub 自动生成的 **Source code (zip)/(tar.gz)** 不是发行安装包，也不是插件 ZIP。插件更新不从 source archive 或本机 `extension/dist` 拼装。下载公开插件 ZIP 后可运行 `shasum -a 256 -c <ZIP>.sha256`，解压到稳定目录，再按[Chrome 加载与正式就绪核对](../docker-release.md#13-安装匹配的-chrome-扩展)操作；公开发行镜像/资料与插件必须同一构建身份。用户安装仍使用完整已核验离线包，不是单个 API 镜像。

| 现象 | 处理 |
| --- | --- |
| 没有 Run workflow / job 全部 skipped | 先确认 workflow 在默认分支、有写权限、ref 是原 tag，三个 inputs 完全匹配；skipped 不代表 PASS。 |
| source 拒绝 / runner 排队 | 核对正式版本、精确 tag、repository readiness 和唯一 Mac labels/在线状态；不要创建第二台同标签宿主。 |
| `LICENSE_LEDGER_INVALID` / `NOTICE_MISMATCH` | 回到许可核准，不能填占位 license 或重写 notice 摘要掩盖不符；源码改变后重选未使用发行身份。 |
| `PREVIOUS_NOT_DISTINCT` / dataset 校验失败 | 回到材料准备；previous 必须不同提交，资料必须由实际 Java 导出并经真实审批。 |
| `WORKFLOW_RESIDUE` / 输入漂移 / Formal 缺链 | 保留原目录、日志和链，确定失败原因后由负责人按事务边界处理；不删 handoff、移动源码或复制 PASS receipt。 |
| 18080 冲突 / Docker endpoint 不可用 | 核对隔离宿主和 Unix socket；不杀用户服务，不更改正式插件端口。 |
| GHCR private / registry 验证失败 | 只有已核实的完整相同镜像与确定远端状态可按上节修可见性后续办；认证/网络未知时先停止。 |
| 同 tag 已有 draft/published Release 或写请求结果未知 | 保留远端现场，负责人核对来源和资产；不自动删 draft、不重复写、不移动 tag、不 force。公开不可变资产不能补换。 |

不上传 Formal receipt、运行日志、私有请求、凭据、真实字幕或观看记录。需要更新说明可改 Release notes，但不借此变更不可变制品；资产错误须重新准备新的正式版本和验收链。本文指导执行，不证明 GitHub 设置、材料核准、独立链或真实下载/安装已经完成。
