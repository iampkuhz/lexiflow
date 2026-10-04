# 1. M 芯片 Mac：Podman Compose 部署和使用

本指南用于本机源码构建与试用。目录创建、文件复制、镜像构建参数、词库准备和数据库初始化由脚本处理；无需手工组装验证包。它支持在原安装中手动升级；不自动下载新源码，也不代表正式发行已通过验收。

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

源码安装和升级优先使用 `JAVA_HOME/bin/java`，并将该 JDK 的 `bin` 放到构建进程 `PATH` 首位；未设置 `JAVA_HOME` 时沿用 `PATH`。SDKMAN 的 `current` 路径可直接使用，无需注册到 `/usr/libexec/java_home`。显式 `JAVA_HOME` 无效时停止，不静默换用其他 JDK。Java 检查失败会区分找不到程序、权限、超时、退出失败、版本无法识别和非 Java 25；请按错误原因处理，不要因此重建数据库。

Podman machine 未创建时运行 `podman machine init`，已有但未运行时使用 `podman machine start`，不要重新初始化已有 machine。Compose provider 不可用时先安装 `podman-compose` 并确认版本命令成功。脚本不会自动修改系统工具或启动未知虚拟机。

建议 Podman VM 至少 4 CPU、8 GiB 内存、20 GiB 可用磁盘；这只是试验准备建议，不代表资源验收通过。首次上游词库下载大于最终约 6.44 MiB 精简压缩包。

## 1.2. 首次部署：一条命令

```sh
node ops/podman/local.mjs install
```

脚本依次完成：检查工具和端口 → 构建 Java 与扩展 → 构建 ARM64 镜像 → 从固定 ECDICT 来源下载、校验、精简 → 创建密码和 PostgreSQL → 首次导入 → 启动应用并检查正式就绪。

脚本不要求切换 Podman 的 rootful/rootless 模式，也不修改 machine、代理或系统防火墙。API 和 PostgreSQL 自动连接用于本机端口发布的普通 bridge；初始化使用内部网络。发布端口仍仅绑定 `127.0.0.1`。普通 bridge 允许容器出站，不是网络级断网隔离；产品不会因此增加数据外发。

默认文件放在仓库内 `.local/podman/`，已经被 Git 忽略。无需创建 `Applications` 目录，无需执行 `cp` 或填写镜像 ID。数据库是 Compose 中的独立 PostgreSQL 服务，数据在持久卷；CSV 和词库 ZIP 不进入 Git 或应用镜像。

执行过程中显示六个阶段及当前子步骤（例如“拉取 Java 25 基础镜像”“构建 API 镜像”），子步骤完成时显示耗时。长命令每 20 秒补一条经过时间及最近输出时间；这是等待状态，不是百分比，也不保证网络正在前进。原始下载/编译输出不刷屏，实时写入启动时给出的 `operation-*.log`。需要详细诊断时，可在另一个终端执行 `tail -f "输出的日志绝对路径"`，不要分享未经检查的原始日志。失败会非零退出并给出失败子步骤和日志路径。

已经运行的旧脚本不会自动获得这些提示；不要在运行中修改安装源码、同时启动第二次安装或删除锁。安装进程持有内核锁；`.lock-guard` 文件会保留，但文件存在不表示仍被锁定，勿手动删除。正常取消时会等待本次子进程退出并释放锁；初始化已经开始却未确认完成时仍保留现场，不自动重导。

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

# 持续查看新产生的服务日志（Ctrl+C 退出）
node ops/podman/local.mjs logs
```

字幕增量、收尾与视频开始日志默认开启，无需开关；新安装直接生效，已有安装执行 `upgrade` 后生效。遗留 `--caption-debug` 参数仅提示已默认开启，不再创建另一套调试环境。

日志含字幕正文、中文提示、视频身份和播放位置，仅用于本机诊断；不要上传、放入 Git、CI 制品或未经清理的公开错误报告。默认不额外保存全量字幕 JSONL；`logs` 持续显示新产生的 PostgreSQL/API 日志，不回放历史日志；按 `Ctrl+C` 只退出查看，不停止服务。查看期间不占用安装锁、不触发升级恢复，也不将字幕正文复制到操作日志。API 容器可读日志使用 Podman `k8s-file`，单文件上限 10 MB；达到上限的保留行为由引擎负责，不把日志当作完整观看记录。[Podman 日志选项](https://docs.podman.io/en/latest/markdown/podman-run.1.html#log-opt-name-value)。显式分析 JSONL 与控制台不同，默认关闭；分析文件最多 16 MiB，达到上限停止追加，需用户自行审阅并删除后再采集。

字幕日志固定为 `时间|级别|中文事件|video=真实视频ID|分:秒.毫秒|正文`，分隔符两侧不补空格；正文为英文夹注中文提示。日常不展示 UUID、subtitle/topic/segment 内部键，也不另造短编号；内部业务身份与去重不变。播放时间如 `09:03.129`，超过一小时显示累计分钟（如 `60:00.001`），不回绕。正常请求的计数、耗时、`NO_HINT` 和 `NO_NEW_SEGMENTS` 仅在 DEBUG 输出；WARN/ERROR 保留真实完整关联 ID 和错误原因，无关联 ID 时显示 `-`。增量显示本次返回的提示，收尾用于记录扩展已确认的行展示；中断或未完成不可当作完整翻译。字幕中的换行与竖杠转义，终端自动折行不改变日志文件的单行结构。容器工具可能添加自己的服务名前缀，该前缀不属于 API 日志正文。

合成示例：

```text
10-03 22:55:33|INFO|字幕增量|video=abcdefghijk|09:03.129|a breakthrough（突破）
10-03 22:55:35|INFO|字幕收尾|video=abcdefghijk|09:05.420|This is a breakthrough（突破）.
```

完整安装后再次执行 `install` 只启动已安装的应用，不会更新。脚本会明确打印已安装版本、本地源码版本、“本次未更新”和所需的唯一 `upgrade` 命令；拉取源码不代表安装已经更新。`status` 返回非零时不要认作可用，正常停止后查询不就绪是预期现象。

拉取所需源码后，在原仓库执行一条命令原地升级：

```sh
node ops/podman/local.mjs upgrade
```

它沿用安装目录、端口、密码、PostgreSQL 卷、已发布词库和 Chrome 扩展目录。旧服务运行期间准备新版，切换时只重建 API，不重新导入数据库；同一构建明确提示无需升级。升级成功后，在 `chrome://extensions` **重新加载原插件**，再刷新视频页面，不删除重装或选择新目录。

失败会尝试恢复原 API、插件和配置；取消后等待原命令退出，再执行同一条 `upgrade`。恢复未确认时保留事务并明确报错，不把失败当成功。 准备文件只有在内容与写入证据一致时才自动清理；遇到未知文件或证据不完整会保留同一事务并停止，不会反复创建新升级目录。请保留现场，不要手动删除事务或工作目录。SQL 结构变化且没有兼容证明时停止；恢复应用不是回滚数据库，不自动清库。历史自定义安装只需加原来的 `--dir /绝对路径`，不要新建环境或换端口。

查询已安装、本地源码和 GitHub 最新正式版：

```sh
node ops/podman/local.mjs version
```

网络失败或还没有正式发行时显示“未知”，不影响本机使用。开发版本如 `2.0.0-SNAPSHOT.gabc1234`，未提交改动附加 `dirty` 和输入摘要；同一输入不会反复增长版本号。Chrome 展示完整 `version_name`，数字 `version` 单独映射。安装记录在受管目录 `installation-records/`，包含时间、原版本、结果、完整 commit、构建/制品摘要与独立的词库资料身份；该目录属于本机私有运行数据，不上传。

不与 `lexiflow.sh` 或手工 Compose 命令管理同一安装。不要移动受管目录或手改配置、密码、状态及升级事务；脚本会拒绝身份和配置漂移。ZIP 是扩展交付归档，不是通用的一键安装程序。

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

先用一条命令自检，无需记住多条排查命令；尚未安装也可以执行：

```sh
node ops/podman/local.mjs doctor
```

需要把结果发给协助排查的人时，生成简短摘要：

```sh
node ops/podman/local.mjs doctor --json
```

自定义安装目录时加同一个 `--dir /绝对路径`。报告只含固定检查项、状态、工具版本和下一步，不含密码、完整配置、宿主路径、数据库内容或原始日志。自检不会创建安装目录、抢安装锁、下载或启动/停止容器。`PASS` 只是本机检查通过；`BLOCKED` 表示缺工具、未安装、服务未启动等前置问题；`FAIL` 表示状态损坏、归属或协议不符。未安装时返回非零是预期，不代表代码坏了。


- **工具缺失或 Podman 未启动**：准备工具/启动 machine 后重新执行 `install`。
- **编译、镜像下载或 ECDICT 下载失败**：查看输出指定的 `operation-*.log`，恢复网络后执行同一条 `install`；数据库初始化前允许重试，已有密码不重置。
- **提示初始化状态不明**：停止自动重试，保留目录与数据库。脚本不会重复导入、删除表或清库；提供去除敏感内容后的失败步骤和日志用于诊断。
- **应用未就绪**：最多等待 120 秒，每 20 秒显示固定原因，超时后进行一次内部健康检查（命令预算合计最多 10 秒，取消清理可能额外耗时）。`HOST_PORT_UNREACHABLE` 表示内部已就绪而宿主访问失败，不是词库未导入；其他情况运行 `logs` 查服务错误。
- **已有安装卡在第六步**：原命令退出后，更新源码并执行 `node ops/podman/local.mjs up`（自定义目录加原 `--dir`）。脚本识别精确已知的内部网络模板后自动补充发布网络，并重建服务连接；沿用原数据库卷、密码、端口和词库，不再次导入。修复过程短暂停服；中断后再次运行同一 `up` 续办。未知配置或归属不符仍拒绝自动覆盖，不要求手工修改 Compose、状态或摘要。
- **旧脚本取消后提示安装锁**：确认原安装及其子进程都已结束，更新脚本后执行以下两条命令（只适用于建库前，默认目录）：

  ```sh
  node ops/podman/local.mjs recover --confirm-stopped
  node ops/podman/local.mjs install
  ```

  自定义安装目录时，两条命令都加原来的 `--dir /绝对路径`。恢复只处理安装锁并重新准备建库前步骤，保留密码、词库和数据；不要手动删除 `.lock` 或修改 `state.json`。新锁若仍有活跃安装/子进程，会拒绝恢复；初始化中断不适用此恢复命令。
- **身份或配置异常**：保留现场，不绕过校验。
- **扩展不可用**：先 `status`，再确认加载的是此次安装输出的目录，而不是旧源码 `extension/dist`。

密码文件、完整数据库、观看记录和未经检查的日志不要分享。脚本不会自动 `prune` 或删除数据；运行空间和持久卷需要保留，不要用清理源码缓存的方式清理它们。
