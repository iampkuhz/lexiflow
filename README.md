# LexiFlow

看 YouTube 英文视频时，生词旁直接显示中文短释，不用切走查词。

> 效果示意：虚构字幕中的生词提示。

![LexiFlow 在英文字幕中显示中文短释](assets/readme/lexiflow-demo.gif)

<a id="docker-安装与使用"></a>

## Podman Compose 安装与使用

M 芯片 Mac 使用者请阅读[部署与使用指南](docs/user/podman-macos.md)：从源码构建 ARM64 镜像和扩展、从 ECDICT 获取精简词库、启动应用和 PostgreSQL、加载 Chrome 扩展，以及通过本机端口查看数据。源码构建需要 Java 25、Node.js 与 npm；无需安装本机 PostgreSQL。

按指南自行构建本地验证安装，不必等待预制包；这不等于正式发行验收。CSV 与词库压缩包不纳入 Git，在使用者本机生成。PostgreSQL 默认仅通过宿主 `127.0.0.1:15432` 提供验证用访问，API 使用 `127.0.0.1:18080`，不开放公网访问。发行与跨机验证进展见[执行状态](docs/roadmap/master-plan/phase2/status.md)。

既有 Docker 发行入口的操作仍见[生命周期指南](docs/development/operations/docker-release.md)，不得与 Podman 验证项目混用同一数据空间。

## 本地启动

macOS 源码开发者进入[本机编译、配置与启动](docs/development/operations/local-experience.md#12-初始化本地配置再启动确定性-api)。开发工具链和词库重建只用于开发库，不用于 Docker 发行安装数据。
