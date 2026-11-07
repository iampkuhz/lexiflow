# 1. M 芯片 Mac：单容器部署和使用

> 入口：[LexiFlow](../../README.md)。正式使用不需要源码、脚本、Compose、Java、Node、Python 或单独数据库。

<a id="13-加载-chrome-扩展"></a>

## 1.1. 两步使用

前提是 macOS Apple Silicon、已安装并运行的 Podman、已安装的 Chrome，以及能够匿名拉取 GHCR 和下载 GitHub Release 的网络。这不是全新机器从零安装只需两步的承诺。

1. 在[正式 Release](https://github.com/iampkuhz/lexiflow/releases)复制该版本正文提供的完整 `podman run` 命令。它使用明确版本 `ghcr.io/iampkuhz/lexiflow:<softwareVersion>`，自动拉取唯一镜像、建立命名卷和启动一个容器。不要换成 `latest`；内容摘要与完整身份见同页 `release.json`。
2. 下载同页 `lexiflow-extension-<softwareVersion>.zip`，按同页 `release.json` 的 SHA-256 校验，解压到稳定目录；在 `chrome://extensions` 开启开发者模式，点击“加载已解压的扩展程序”并选择含 `manifest.json` 的目录。启用后无需填写地址、密码或修改文件。

安装只需要运行镜像、扩展 ZIP 和用于核验的 `release.json`。第三方对应源码、GitHub 自动生成的项目源码归档和发行证明不是运行依赖，不必下载或解压。

正式容器为 `lexiflow-release`，资料卷为 `lexiflow-release-data`；API 仅映射 `127.0.0.1:18080:8080`。数据库不映射宿主端口，所有持久事实在该独立卷内。运行命令资源上限为 2 CPU/2 GiB，Podman machine 还需要容纳其他项目；实际镜像大小、初始化时间和验证环境以对应 Release 的测量为准。

插件弹窗显示初始化中、正式就绪、服务不可用或版本不兼容。软件版本、`caption-hints.v2` 协议和发布资料身份都必须匹配同一发行；不能把容器存在或进程存活当作正式就绪。下载的 ZIP 不是 Chrome Web Store 安装，开发者模式和加载解压目录属于第二步；本阶段不提供商店上架或浏览器策略修改。

## 1.2. 日常启停与隐私

```sh
podman ps --filter name=lexiflow-release
podman logs --tail 100 lexiflow-release
podman stop --time 45 lexiflow-release
podman start lexiflow-release
```

停止、启动及系统重启不重新导入匹配的已发布资料。首次初始化中断不会被标为成功；再次启动只在已识别的本项目卷上恢复核验，不清空未知数据。关键组件失败会使容器退出或健康检查失败，不能继续宣称就绪。

英文先显示，后端不可用或不兼容时仍显示英文。观看只查询已发布资料，不触发模型。本机显式词段抑制偏好只留在扩展，不上传、不推断熟悉度、无账号同步。

服务诊断日志可能包含字幕正文及视频定位，只可本机查看，不上传、不提交。不要上传数据库、卷、密码、观看记录、模型输入输出或私有运行材料。分析文件默认不开启。

## 1.3. 更新和数据保护

更新先准备、再切换，不需要重新下载完整安装包或重新导入旧版资料。

1. **旧服务继续运行时准备新制品。** 从新版本的 `release.json` 取得 `image.reference`、`image.configId` 和扩展摘要。先用 `podman image inspect --format '{{.Id}}|{{.Os}}|{{.Architecture}}' '<image.reference>'` 核对本机是否已有该摘要的镜像；仅在确认镜像缺失时执行 `podman pull '<image.reference>'`。确认镜像 ID 与 `image.configId` 相同（比较时可忽略 `sha256:` 前缀），且系统和架构是 `linux|arm64`。不匹配就停止。引擎可复用已缓存的相同层。校验新扩展 ZIP 后解压到另一目录，暂不启用。
2. **保留回退组合再切换。** 确认旧容器和资料卷归属，先完成所需备份，保留旧镜像、容器、卷及扩展目录。停旧容器，使用新版本 Release 的启动命令，将 `--name` 和挂载的卷名替换为本次安装专属的新名称，将 `--pull=always` 改为 `--pull=never` 并使用已核验的 `image.reference`。保留 loopback 端口。新空卷从镜像内离线初始化；运行健康且身份匹配后，禁用旧扩展并加载新扩展。若保留原扩展实例的本机偏好有要求，不删除旧扩展或手工覆盖其目录。
3. **失败时不清卷。** 停止本次新容器，再启动保留的旧容器，停用新扩展并重新启用旧扩展。保留新卷用于诊断。确认旧容器恢复就绪后才算回退完成。不要使用 `--replace`、改归属标记或让不同资料身份复用旧卷；任意历史数据库原地迁移不在支持范围内。

同名容器存在时 Podman 会清晰拒绝创建；重新启动已有容器用 `podman start`。已有端口被占用时先识别其归属，不停止未知服务。已有非空未知卷、资料或结构不兼容时入口失败关闭，保留现场。完全空的同名卷会被初始化；纯 `podman run` 无法区分它是本次自动新建还是预先存在，不保证拒绝所有未知空卷。

## 1.4. 失败定位

| 现象 | 处理 |
| --- | --- |
| 不能连接 Podman | 先完成基础环境安装并启动 Podman machine；应用命令不管理引擎。 |
| 拉取失败 | 检查 GHCR 网络及该 Release 的匿名可读性；不以其他来源或漂移 tag 替代已验 digest。 |
| 容器名或18080占用 | 核对实际资源所有者；不自动接管、替换或删除。 |
| 初始化中 | 等待实际资料导入；日志与容器健康提供进度，未就绪不能算成功。 |
| 容器退出或 unhealthy | 查看精确容器日志；不要删除卷重试。关键进程/资料核对失败必须修复原因。 |
| 版本不兼容 | 使用同一不可变 Release 的扩展和镜像，核对 `release.json` 身份；英文仍可显示。 |
| 英文有而提示无 | 查看弹窗状态、已发布资料身份、英文字幕源及显式本机抑制偏好；不能用模型补全绕过。 |

## 1.5. 与开发环境分开

正式使用是 `lexiflow-release`/`18080`/`lexiflow-release-data`；开发调试是 `lexiflow-dev`/`18081`/独立开发数据。测试另用唯一归属资源及固定验收端口 `18082`。三者不共享卷，不因名字相似清理彼此。源码调试和开发库重建仅按[本机开发](../scenarios/local-development.md)执行，不用于正式数据。
