# 1. Docker 发行安装与生命周期

> 位置：[运行与环境](../operations.md) → Docker 发行安装。取得渠道、版本、许可证、平台和配套扩展以对应的已验收发行材料为准；实施与验收进度仅见[执行状态](../../roadmap/master-plan/phase2/status.md)。

## 1.1. 准备发行目录与主机

从可信发行渠道取得完整发行包，先用该渠道的可信摘要验证整包或入口，再解包。同目录的 checksum 不能独立证明来源真实性；不要在核实来源前执行其中的脚本。不要重命名或移动它：私有安装记录会绑定 `lexiflow.sh` 的入口绝对路径和发行包身份。目录应包含 `lexiflow.sh`、`manifest.json`、`manifest.json.sha256` 及清单列出的制品；实际文件名和摘要以清单为准。不要自行补文件或修改摘要。

当期目标宿主仅为 M 芯片 macOS，容器架构为 `linux/arm64`；其他宿主与 amd64 留待后续。主机需要 Docker Engine、Docker Compose plugin、可访问 daemon 的当前用户、POSIX `sh`，以及 `sha256sum` 或 `shasum`。发行入口会核对 daemon、Compose 和 Linux 架构；仓库实现识别 `linux/amd64`、`linux/arm64`，但这不等于任一发行已对这些平台发布或验收。仅在发行材料明确验证该主机组合后继续。普通用户不需要源码工具链、Node.js、Python、Java、Gradle 或词库 CSV。不要把 macOS 开发环境或 Windows 原生 shell 当作该发行入口已支持的证明。

在 shell 中设置 `RELEASE_DIR` 为发行目录、`ROOT` 为安装私有数据绝对路径。`ROOT` 的父目录必须存在，首次使用时 `ROOT` 不得存在；入口会创建它。`ROOT` 不是发行目录，也不能是其子项。路径中的空格须保留在双引号中。以下均为占位示例，不执行：

```sh
RELEASE_DIR='/absolute/path/to/verified-release'
ROOT='/absolute/path/to/private/lexiflow-data'
sh "$RELEASE_DIR/lexiflow.sh" verify
```

`verify` 不需要 `ROOT`，检查入口、清单、摘要及发行制品；成功可能静默，退出码为成功判据。失败时确认目录完整、位置未变、摘要工具可用，然后从可信渠道重新取得完整发行包；不要跳过检查。保留核验过的发行目录原路径供后续所有生命周期操作使用。

## 1.2. 首次初始化与启动

```sh
sh "$RELEASE_DIR/lexiflow.sh" prepare "$ROOT" &&
sh "$RELEASE_DIR/lexiflow.sh" activate "$ROOT" &&
sh "$RELEASE_DIR/lexiflow.sh" status "$ROOT"
```

`prepare` 检查 Docker、导入发行镜像、启动匹配的 PostgreSQL、初始化随包资料，并验证候选服务。首次安装不会先创建活动 API；更新时也不会停止当前活动服务。只有 `prepare` 成功进入 `prepared` 后才执行 `activate`。激活会停止旧活动版本（如有）并启动新版本；失败时入口尝试恢复旧活动版本。

`status` 输出四项本地记录：`phase active previous candidate`。key 是发行身份，不是秘密或运行健康状态；`idle` 只表示生命周期记录处于稳定态。确认服务就绪应在加载对应 Chrome 扩展后打开弹窗，看到“正式就绪”，并核对软件版本、API 协议和已发布资料版本。扩展不能连接、协议不匹配或资料未就绪时，按弹窗提示检查发行与扩展配套关系；不要以容器存在或 `status` 返回成功替代就绪判据。

## 1.3. 安装匹配的 Chrome 扩展

在 `manifest.json` 中定位 `role` 为 `extension` 的制品，按该项 `path`、字节数和 SHA-256 核对对应文件；核对 `metadata` 所标版本身份与服务发行一致。扩展制品为 ZIP，解压到固定目录。Chrome 打开 `chrome://extensions`，开启开发者模式，选择“加载已解压的扩展程序”，指向解包后包含扩展 `manifest.json` 的目录。扩展更新时加载对应新文件、在扩展管理页重新加载，并刷新 YouTube 标签页。

此方式是开发者模式本机加载说明，不是 Chrome Web Store 发布或审核状态声明。若发行没有匹配且摘要可核验的扩展制品，停止安装并向发行提供方索取，不使用仓库 `extension/dist` 冒充已发布扩展。

## 1.4. 更新、回退与停止

状态有 active、previous、candidate 三个逻辑槽，但最多保存两份发行记录：active + candidate 或 active + previous，不能再准备第三份。更新前用当前入口读取状态，确认 `previous` 为 `none`；否则先按卸载/删除小节处理旧 previous。取得并固定新发行目录后，使用新目录入口对同一个 `ROOT` 执行 `prepare`，成功后执行 `activate`。新包准备期间旧服务持续运行；切换成功后新包成为 active、旧活动包进入 previous。

回退使用 `status` 第三个输出字段，即 `previous` 的完整 key，并从该旧版本原发行目录调用 `recover "$ROOT"`。恢复后旧版本成为 active，刚才版本成为 previous。两代发行目录必须保持原路径；入口会按安装记录委派到匹配发行，不可只替换可执行文件。停止服务：

```sh
sh "$RELEASE_DIR/lexiflow.sh" stop "$ROOT"
```

成功后 `phase` 应为 `stopped`；数据保留。由当前 active 对应发行目录重新运行 `activate "$ROOT"` 会启动现有活动包。`recover` 不是任意错误的通用重试，也不应用来跳过失败阶段。

## 1.5. 删除旧版本或卸载

销毁前先执行 `status`，确认操作对象的槽位及完整 key。`delete` key 参数不是任意版本号：

| 要删除的对象 | key 来源 | 前置条件 |
| --- | --- | --- |
| active | `status` 第 2 个字段 | 必须先 `stop`，状态为 `stopped` |
| previous | `status` 第 3 个字段 | 不得是 active |
| candidate | `status` 第 4 个字段 | 仅 `preparing` / `prepared` 阶段可删除 |

使用该 key 对应发行入口，并明确确认数据永久删除：

```sh
KEY='<从 status 对应槽复制的完整 key>'
sh "$RELEASE_DIR/lexiflow.sh" delete "$ROOT" "$KEY" --confirm-delete-data
```

此操作会删除对应安装记录及该版本的数据/恢复数据，不可撤销；绝不提供裸 `rm -rf` 或 Docker prune 作为替代。遇到 `deleting` 时只能以同一个 key 重试同一确认操作；不匹配或失败则保留现场寻求维护者帮助。

卸载按顺序处理：先停止 active，并按槽位规则明确删除各份安装资料；确认其记录和资源已清理后，才移除不再需要的发行目录；最后按需在 Chrome 扩展管理页移除扩展。不要先删除发行入口，否则其安装记录无法通过核验。后端 `stop`/`delete` 不会清除浏览器本机提示偏好，扩展弹窗中的“恢复全部提示”是单独的用户操作。不要收集或共享真实字幕、观看 URL、密钥或本机运行记录作为诊断材料。

## 1.6. 阶段失败时的安全去向

| 当前阶段/结果 | 安全动作 |
| --- | --- |
| `preparing` | `status` 确认 candidate 与当前发行一致；同一发行可重试 `prepare`。不要编辑 `ROOT` 内状态。 |
| `prepared` | 所需制品已准备，核对候选发行后执行其 `activate`。 |
| `switching` / `LF_RECOVERY_REQUIRED` | 保留两代发行目录和 `ROOT`，对照 active/candidate 槽使用原发行入口 `recover "$ROOT"`；若仍失败，停止并联系维护者。 |
| `starting` / `stopping` | 使用当前 active 对应入口 `recover "$ROOT"`。恢复为 stopped 后可用 active 入口 `activate` 重启。 |
| `deleting` | 仅用同一 key 和原发行入口重试确认删除；状态或路径不符时停止并求助。 |
| `LF_INSTALLATION_UNVERIFIED` | 已存在的安装目录无法通过只读身份与状态核对；保留目录原状。确认当前操作已停止后，选择未占用的新私有路径重新 `prepare`；旧目录由维护者在产品工具之外核对，不自动修复、删除或接管。 |
| 状态文件无效、锁冲突或其他未知失败 | 保留现场并记录固定错误码与所处阶段；不要手改 state、猜 PID、拆锁或删除 Docker 资源。 |

生命周期入口不自动清理用户文件。示例命令和状态值是操作说明，不是已执行安装证据。实际平台兼容性、初始化、更新/恢复和独立验收以各自发行记录为准；文档检查通过仅证明链接与格式等静态约束。
