# 1. 第一次 GitHub Release：当前 Mac 操作简表

> 位置：[GitHub Actions](../github-actions.md) → 第一次发行。详细字段、资料导出、验收命令及故障处理只在[发行准备页](first-release/preparation.md)；下载者看[安装步骤](../docker-release.md)。

## 1.1. 先弄清：谁打包，包放在哪里

**你触发，GitHub Actions 编排，这台 Mac 自动构建，GitHub 保存并分发产物。**不需要手工压 ZIP、`podman save` 或上传镜像；但首次需要准备经批准的资料和许可，不是从空输入就能发行。

| 东西 | 怎么生成 | 发布到哪里 |
| --- | --- | --- |
| API JAR、API/PostgreSQL 镜像、配套插件 ZIP | Actions 调用本机 runner 的共享构建代码；同一候选只构建一次，晋升不重建 | 两份镜像到 GHCR；插件 ZIP 到 GitHub Release |
| 完整安装 `tar.gz`、checksum、清单 | 晋升时从通过验收的原候选组装 | GitHub Release 的 Assets |
| 已发布词库快照和许可 notice | 首次由维护者准备，构建时验证并纳入安装包 | 随完整发行包交付 |

当前 [`release.yml`](../../../.github/workflows/release.yml) 的 source 检查在 GitHub 托管 Linux 上跑；构建、真实 Podman 验收和发布在注册的 **self-hosted Mac ARM64 runner** 上跑。因此发布期间电脑须在线、runner 运行、Podman 可用。Self-hosted 意味着 Actions 使用自己的机器，不是你手动打包。[GitHub runner 说明](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners)

如果希望关掉这台 Mac 后 GitHub 仍能完成全部构建与发行，需要另改 runner/工作流及验收证据接入；**当前机制不是纯 GitHub 托管发布**。本手册只精简操作，不改变这些机制。

## 1.2. 这台电脑只需先准备一次

1. **自动配置 runner。** 在源码根执行下方 `runner deps` 和 `runner setup`；工具通过已登录的 `gh` 获取短期 token，校验并安装官方 macOS ARM64 包、注册 `lexiflow-release` 标签并启动服务，无需网页复制命令。缺登录或权限时按[Runner 管理](first-release/preparation.md#16-runner-安装与日常管理)操作。以完成原生验收的同一 OS 用户运行，只用于受保护发行，不接不可信 PR。
2. **留专用源码与资料目录。** 同一电脑可以有独立发行 clone；不要把 `LEXIFLOW_RELEASE_WORKSPACE` 指向日常开发 checkout 或现有 `.local/podman` 安装。验证期间不编辑/移动该源码；输入、候选和证据保持原路径。首次正式 tag 已推送后，在 validation 人审前让这个 clone 的 HEAD 对齐该 tag。
3. **核对工具和测试环境。** 运行 `runner doctor`；Homebrew 管理公共 CLI，既有工具链提供 Temurin Java 25。工程 Python 依赖、Playwright、隔离 PostgreSQL/Redis 与实际 Docker/Podman endpoint 仍按[验证环境](../verification-environment.md)准备。完整候选验收需要本机 18080 空闲；若日常 API 占用，先安排暂停与恢复窗口，不杀未知进程、不清库、不让验收接到日常数据。
4. **配置 GitHub。** 建立 `lexiflow-release-validation`、`lexiflow-release-promotion` 两个要求 owner 人工批准的 Environment，只允许正式 `v*` tag；单维护者允许本人审批，管理员 bypass 保持关闭；开启 immutable releases。按[配置表](first-release/preparation.md#11-github-配置字段)填写本机路径、测试资源与 secret；确认全部准备后才设 `LEXIFLOW_RELEASE_RUNNER_READY=true`。
5. **完成首发材料和验收准备。** 核准五类组件的许可与 notice、导出真实已发布资料、准备不同源码提交的私有 `previous` 候选和构建请求，并安排独立 Formal 验证/审查。首发没有旧公开 Release 也需要 previous，用于真实升级/失败恢复测试；详细工作交给[准备页](first-release/preparation.md#12-首次发行材料没有旧公开-release-也不能省-previous)，不要伪造材料或跳 Gate。

```sh
node ops/release/workflow/run-release-workflow.mjs runner deps
node ops/release/workflow/run-release-workflow.mjs runner setup
node ops/release/workflow/run-release-workflow.mjs runner doctor
```

Runner Online 只说明机器已接入，不代表五步准备或 Formal 已完成；工具不会自动设 `LEXIFLOW_RELEASE_RUNNER_READY=true`。

这五步是首次配置，不是每次重做。GitHub 默认分支必须已有审核通过的 workflow；只存在本机的 commit 不能触发远端流程。GitHub owner 可批准自己的发行，但原生 Formal 的 producer、validator、reviewer 仍须是不同真实 actor；本人审批不能代替独立验证或审查。

## 1.3. 每次发布：只走这个顺序

| 步骤 | 你做什么 | 自动发生什么 |
| --- | --- | --- |
| 1. 固定版本 | 审核并提交源码/许可，推到默认分支；`ops/release/version.txt` 为正式 `x.y.z`，同版本 tag/Release/GHCR 未使用 | 分支 Quick checks |
| 2. 推 tag | 在干净目标 commit 创建并推 `v<版本>` | `Release validation` 的 source 检查，随后等待 validation 人审 |
| 3. 准备并批准 | 专用 clone 对齐同 tag；更新私有请求、确认输出目录空和输入齐全，再由 owner 人工批准 | 本机 runner 自动构建候选、跑开发检查和 Repository Verify |
| 4. 独立验收 | 在原 checkout 由不同原生 actor 完成 Formal，取得 `submission_id` | 运行冻结验收并形成可核验链；不是 Actions 自动签证 |
| 5. 晋升 | 原 tag 手动运行 workflow，填写 submission 和 `PUBLISH`，依次通过两道 Environment 批准 | 只读预检 → 推 GHCR → 上传并核对 Release 资产 → 公开发布 |

下列只在上述前置已满足、由负责人决定发布后运行；占位值必须替换。不创建同名 draft，不 force/move tag。

```sh
# 干净的目标源码根；该 commit 已审核并推送，版本和远端身份未使用。
TAG='v<正式版本>'
git tag -a "$TAG" -m "LexiFlow $TAG"
node ops/release/integrity/source-build-identity.mjs --tag "$TAG"
# 身份核对成功后才运行这一行：
git push origin "refs/tags/$TAG"

# 等准备与独立 Formal 全部通过后，触发晋升；不是紧接着推 tag 就执行。
gh workflow run release.yml --repo iampkuhz/lexiflow --ref "$TAG" \
  -f release_tag="$TAG" -f submission_id='<本次SUBMISSION_UUID>' \
  -f publish_confirmation=PUBLISH
```

在 **Actions → Release validation** 看进度和 **Review deployments**。必须以 tag 作为 `--ref`，不能只填 release_tag 却在分支运行。首次 GHCR 镜像可能 private，需要管理员将两份 package 设为 public；只有远端状态确定且原候选匹配才能按[可见性处理](first-release/preparation.md#14-首次-ghcr-可见性)续办。失败/缺件停止，不删草稿、重传未知写请求或绕过验收。

## 1.4. 其他机器如何获取

**可以从 GitHub 拉取，但包和镜像分两个渠道：**

- **GitHub Releases → Assets**：完整安装 `tar.gz`、独立 Chrome 插件 ZIP、checksum 和清单。自动附加的 Source code ZIP 不是安装包，也不是插件包。
- **GHCR**：`ghcr.io/iampkuhz/lexiflow-api` 和 `ghcr.io/iampkuhz/lexiflow-postgres`。两份 package public 后可匿名拉取。[GitHub Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

从可信同一 Release 验证 `registry-images.json` 和摘要，取每份镜像的 `manifestDigest`：

```sh
podman pull 'ghcr.io/iampkuhz/lexiflow-api@sha256:<API manifestDigest 的64位摘要>'
podman pull 'ghcr.io/iampkuhz/lexiflow-postgres@sha256:<PostgreSQL manifestDigest 的64位摘要>'
```

镜像本身不等于完整安装：还要同一发行的资料、SQL、入口与扩展。安装按[Docker 发行安装](../docker-release.md)执行；不用重新编译或准备源码 CSV。插件 ZIP 校验后解压到固定目录，再在 Chrome「加载已解压的扩展程序」。

当前只交付 `linux/arm64` 镜像，验收宿主范围是 Apple Silicon macOS + Podman；其他 Apple Silicon Mac 可按发行材料安装。**不能把「能拉到镜像」说成 Intel/Windows/Linux 宿主已支持或已验收。**正式完成以公开 Release 的资产、摘要及匿名镜像读取均复核成功为准；文档不证明实际发行已完成。
