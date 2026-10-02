# 1. M 芯片 Mac：Podman Compose 部署和使用

本指南用于本机源码构建与试用。目录创建、文件复制、镜像构建参数、词库准备和数据库初始化由脚本处理；无需手工组装验证包。这不是正式发行或自动更新入口。

## 1.1. 准备一次

需要 M 芯片 Mac、Java 25 JDK、Node.js 22 或以上（含 npm）、Podman 和可用的 Compose provider，以及 Chrome。首次安装需要访问 Maven、npm、GitHub 和容器镜像源；无需安装本机 PostgreSQL、Python 或 Docker Desktop。

在包含本指南和 `ops/podman/local.mjs` 的源码仓库根目录打开终端：

```sh
java -version
node --version
podman info
podman compose version
```

Java 必须是 25，Node 至少 22。多个 JDK 并存时可选择：

```sh
export JAVA_HOME=$(/usr/libexec/java_home -v 25)
export PATH="$JAVA_HOME/bin:$PATH"
```

Podman machine 未创建时运行 `podman machine init`，已有但未运行时使用 `podman machine start`，不要重新初始化已有 machine。Compose provider 不可用时先安装 `podman-compose` 并确认版本命令成功。脚本不会自动修改系统工具或启动未知虚拟机。

建议 Podman VM 至少 4 CPU、8 GiB 内存、20 GiB 可用磁盘；这只是试验准备建议，不代表资源验收通过。首次上游词库下载大于最终约 6.44 MiB 精简压缩包。

## 1.2. 首次部署：一条命令

```sh
node ops/podman/local.mjs install
```

脚本依次完成：检查工具和端口 → 构建 Java 与扩展 → 构建 ARM64 镜像 → 从固定 ECDICT 来源下载、校验、精简 → 创建密码和 PostgreSQL → 首次导入 → 启动应用并检查正式就绪。

默认文件放在仓库内 `.local/podman/`，已经被 Git 忽略。无需创建 `Applications` 目录，无需执行 `cp` 或填写镜像 ID。数据库是 Compose 中的独立 PostgreSQL 服务，数据在持久卷；CSV 和词库 ZIP 不进入 Git 或应用镜像。

执行过程中显示六个阶段及当前子步骤（例如“拉取 Java 25 基础镜像”“构建 API 镜像”），子步骤完成时显示耗时。长命令每 20 秒补一条经过时间及最近输出时间；这是等待状态，不是百分比，也不保证网络正在前进。原始下载/编译输出不刷屏，实时写入启动时给出的 `operation-*.log`。需要详细诊断时，可在另一个终端执行 `tail -f "输出的日志绝对路径"`，不要分享未经检查的原始日志。失败会非零退出并给出失败子步骤和日志路径。

已经运行的旧脚本不会自动获得这些提示；不要在运行中修改安装源码、同时启动第二次安装或删除锁。新脚本取消时会等待本次子进程退出并释放锁；初始化已经开始却未确认完成时仍保留现场，不自动重导。

**成功判据：脚本输出“就绪”，并列出 API、Chrome 扩展目录和数据库连接信息。** 仅容器 running 不算成功。

默认端口为 API `18080`、数据库 `15432`，都只监听 `127.0.0.1`。若已被占用，可以首次安装时指定空闲端口：

```sh
node ops/podman/local.mjs install --api-port 18081 --db-port 15433
```

扩展也会自动使用指定的 API 端口。已有安装不能直接改端口；不要停止归属不明的服务来腾端口。

需要指定另一处全新目录时可加 `--dir /绝对路径`，脚本会创建它；后续每个命令也须带同一个 `--dir`。不要指定旧手工安装、源码根目录或已有未知目录。路径可以包含空格，但不要包含换行、冒号、`$`、双引号或反斜杠。

## 1.3. 加载 Chrome 扩展

1. 打开 `chrome://extensions`，启用“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择脚本成功输出的 Chrome 加载目录（默认 `.local/podman/extension`）。
3. 打开扩展弹窗确认正式就绪；再打开 YouTube 视频并开启英文字幕。
4. 英文应正常显示，符合提示规则的词段出现中文提示，并不是每个单词都翻译。

Chrome 默认与 Podman 在同一台 Mac；不要为了远程浏览器直接将 API 暴露到公网。

## 1.4. 日常使用：不用重新部署

```sh
# 查看容器及应用是否正式就绪
node ops/podman/local.mjs status

# 停止，保留数据库和词库
node ops/podman/local.mjs stop

# 再次启动，不重建镜像、不下载、不重新导入
node ops/podman/local.mjs up

# 查看最近的服务日志
node ops/podman/local.mjs logs
```

完整安装后再次执行 `install` 也只会启动已有安装，不会重新初始化或更新版本。`status` 返回非零时不要认作可用；正常停止后查询不就绪是预期现象。

不与 `lexiflow.sh` 或旧手工 Compose 命令管理同一安装。不要移动受管目录、手改 `release.env`、密码或状态文件；脚本会检查身份和配置一致性。拉取新源码不会自动更新已有安装；需要验证新版本时另选全新 `--dir` 与空闲端口，旧数据不会自动迁移。此入口不提供自动卸载或更新回滚。

## 1.5. 查看 PostgreSQL

数据库客户端使用脚本输出的端口（默认如下）：

| 项目 | 值 |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `15432` |
| Database | `lexiflow` |
| User | `lexiflow` |
| Password | `.local/podman/secrets/app-password` 文件内容 |
| Schema | `lexiflow_release` |

该账号可以写应用数据；调试查看时设为只读会话，例如：

```sql
BEGIN READ ONLY;
SELECT count(*) FROM lexiflow_release.lexicon_prepared_entry;
SELECT count(*) FROM lexiflow_release.lexicon_hint_lookup;
SELECT * FROM lexiflow_release.lexicon_dataset;
ROLLBACK;
```

从另一台电脑查看数据库，使用 SSH 隧道（Mac 需允许相应账号远程登录）：

```sh
ssh -N -L 15433:127.0.0.1:15432 用户名@部署Mac的地址
```

然后客户端连接本机 `127.0.0.1:15433`，其他参数不变；非默认数据库端口需修改隧道右侧端口。不要将 PostgreSQL 绑定到全部网卡。

## 1.6. 失败时怎么处理

- **工具缺失或 Podman 未启动**：准备工具/启动 machine 后重新执行 `install`。
- **编译、镜像下载或 ECDICT 下载失败**：查看输出指定的 `operation-*.log`，恢复网络后执行同一条 `install`；数据库初始化前允许重试，已有密码不重置。
- **提示初始化状态不明**：停止自动重试，保留目录与数据库。脚本不会重复导入、删除表或清库；提供去除敏感内容后的失败步骤和日志用于诊断。
- **应用未就绪**：运行 `logs` 查服务错误；初始化已成功则可用 `up` 重试，不要重跑 Java 导入命令。
- **旧脚本取消后提示安装锁**：确认原安装及其子进程都已结束，更新脚本后执行以下两条命令（只适用于建库前，默认目录）：

  ```sh
  node ops/podman/local.mjs recover --confirm-stopped
  node ops/podman/local.mjs install
  ```

  自定义安装目录时，两条命令都加原来的 `--dir /绝对路径`。恢复只处理安装锁并重新准备建库前步骤，保留密码、词库和数据；不要手动删除 `.lock` 或修改 `state.json`。新锁若仍有活跃安装/子进程，会拒绝恢复；初始化中断不适用此恢复命令。
- **身份或配置异常**：保留现场，不绕过校验。
- **扩展不可用**：先 `status`，再确认加载的是此次安装输出的目录，而不是旧源码 `extension/dist`。

密码文件、完整数据库、观看记录和未经检查的日志不要分享。脚本不会自动 `prune` 或删除数据；运行空间和持久卷需要保留，不要用清理源码缓存的方式清理它们。
