# LexiFlow

看 YouTube 英文视频时，生词旁直接显示中文短释，不用切走查词。

> 效果示意：虚构字幕中的生词提示。

![LexiFlow 在英文字幕中显示中文短释](assets/readme/lexiflow-demo.gif)

<a id="docker-安装与使用"></a>

## Podman Compose 安装与使用

准备 Java 25、Node.js 22+ 和已运行的 Podman/Compose 后，在仓库根目录执行：

```sh
node ops/podman/local.mjs install
```

脚本自动构建镜像、下载精简词库、初始化 PostgreSQL 并启动应用。成功后在 `chrome://extensions` 开启开发者模式，按输出的固定目录加载已解压扩展。

已有安装使用以下命令；重复 `install` 只核对身份、提示升级，不执行升级。

```sh
node ops/podman/local.mjs upgrade  # 原目录升级，保留数据库、密码和端口
node ops/podman/local.mjs version  # 区分安装、本地源码与可查询的 GitHub 正式版
node ops/podman/local.mjs logs     # 持续查看本机新日志，Ctrl+C 退出
```

升级后重新加载原扩展并刷新视频；无需重装。自定义安装目录须继续使用原 `--dir /absolute/install-dir`。

字幕增量与收尾日志默认开启，包含字幕正文和视频定位信息，仅用于本机诊断；不要上传或提交日志。启停、恢复与隐私细节见[部署与使用指南](docs/user/podman-macos.md)。

CSV、词库 ZIP、密码与安装文件不进入 Git。本机可运行不等于正式验收通过，进展见[执行状态](docs/roadmap/master-plan/phase2/status.md)。

既有 Docker 发行入口的操作仍见[生命周期指南](docs/development/operations/docker-release.md)，不得与 Podman 验证项目混用同一数据空间。

## 本地启动

macOS 源码开发者进入[本机编译、配置与启动](docs/development/operations/local-experience.md#12-初始化本地配置再启动确定性-api)。开发工具链和词库重建只用于开发库，不用于 Docker 发行安装数据。
