# 1. M 芯片 Mac：Podman Compose 部署和使用

本指南面向在 M 芯片 Mac 上自行构建并试用的使用者。PostgreSQL 和应用是两个独立容器；Chrome 默认也在这台 Mac 上。以下生成的是**本地验证安装，不是正式发行包**。无需等待提供方预制镜像。

## 1.1. 准备工具与源码

需要 Git、Java 25 JDK、Node.js 22 或以上与 npm、Podman 和可用的 Podman Compose provider，以及 Chrome。首次构建需访问 Maven、npm、GitHub 和容器镜像源。Python/Git/7zip 下载工具另在一次性容器中运行，不要求 Mac 安装 Python 或 PostgreSQL。

```sh
uname -m
java -version
node --version
npm --version
podman version
podman machine list
podman compose version
```

确认架构 `arm64`、Java 主版本 25、Node 至少 22。Mac 已安装多个 JDK 时，使用 `export JAVA_HOME=$(/usr/libexec/java_home -v 25)` 选择 JDK。Podman machine 未创建时运行 `podman machine init`，未运行时运行 `podman machine start`；不要重新初始化已有 machine。Compose provider 不可用时先安装 `podman-compose` 并确认上述版本命令成功。不需要 Docker Desktop。

建议为 Podman VM 准备至少 4 CPU、8 GiB 内存和 20 GiB 可用磁盘，这是试验准备建议，不代表资源验收已通过。词库首次上游下载远大于最终精简 ZIP，不能只预留几 MiB。

将**包含本指南及 `ops/podman/` 的完整源码**放在本机目录。若从 Git 获取，使用交付方确认包含这些修改的分支/提交；仅克隆尚未包含修改的旧分支不够。若使用源码快照，应包含 `backend/`、`extension/`、`ops/`、`infra/`、`scripts/` 及 Gradle wrapper，不要复制 `.env`、`.local/`、密码、数据库、真实字幕。以下从该源码仓库根目录打开终端执行，同一终端保留变量；任一步失败就停止，不执行后续步骤。

## 1.2. 构建应用和扩展

```sh
export SOURCE="$PWD"
export JAVA_HOME=$(/usr/libexec/java_home -v 25)
export PATH="$JAVA_HOME/bin:$PATH"
(cd backend && ./gradlew --no-daemon :api:bootJar)
(cd extension && npm ci && LEXIFLOW_API_PORT=18080 npm run build)
```

这一步不连接数据库，不要求正式发行的干净 Git 工作区，也不使用 `-Prelease=true` 或正式打包命令。不要把本地构建当作正式验收。构建会使用仓库统一版本文件；不要手工修改 JAR 或扩展的版本号。

在一个全新目录组装本次验证安装，拒绝复用已有目录：

```sh
export KIT="$HOME/Applications/LexiFlow-validation"
test ! -e "$KIT" && mkdir -p "$KIT"
```

若目录已存在，上一步非零退出：停止，选择新的空目录并重新设置 `KIT`，不要覆盖已有安装。然后执行：

```sh
mkdir -p "$KIT/build/api" "$KIT/build/postgres" "$KIT/infra/postgres" \
  "$KIT/ops/podman" "$KIT/ops/dataset" "$KIT/scripts/environment"
VERSION=$(cat "$SOURCE/ops/release/version.txt")
cp "$SOURCE/backend/product/api/build/libs/api-$VERSION.jar" "$KIT/build/api/lexiflow-api.jar"
cp "$SOURCE/ops/docker/Dockerfile" "$KIT/build/api/Dockerfile"
cp "$SOURCE/ops/docker/entrypoint.sh" "$KIT/build/api/entrypoint.sh"
cp "$SOURCE/ops/docker/Dockerfile.postgres" "$KIT/build/postgres/Dockerfile"
cp "$SOURCE/ops/docker/bootstrap.sh" "$KIT/build/postgres/bootstrap.sh"
cp "$SOURCE/ops/podman/compose.validation.yaml" "$KIT/compose.yaml"
cp "$SOURCE/infra/postgres/schema.sql" "$KIT/infra/postgres/schema.sql"
cp "$SOURCE/ops/podman/fetch-ecdict.sh" "$SOURCE/ops/podman/source-tools.Containerfile" "$KIT/ops/podman/"
cp "$SOURCE/ops/dataset/ecdict-source.lock.json" "$KIT/ops/dataset/"
cp "$SOURCE/scripts/environment/ecdict_bundle.py" "$KIT/scripts/environment/"
cp -R "$SOURCE/extension/dist" "$KIT/extension"
cd "$KIT"
```

`cp` 报 JAR 不存在时停止，核对前面的 Gradle 是否成功，不选用旧 JAR。镜像构建上下文仅包含 JAR 和启动脚本，不上传整个源码、词库、密码或本地数据。

## 1.3. 构建本机 ARM64 镜像

首次拉取 Java 25 和 PostgreSQL 17 基础镜像，随后使用本机精确 ID 构建并记录；基础标签可能更新，因此保留本次记录。不要拿普通 PostgreSQL 镜像直接替代配套镜像，否则缺少应用账号初始化。

```sh
podman pull --platform linux/arm64 docker.io/library/eclipse-temurin:25-jre
podman pull --platform linux/arm64 docker.io/library/postgres:17-bookworm
JAVA_BASE=$(podman image inspect --format '{{.Id}}' docker.io/library/eclipse-temurin:25-jre)
PG_BASE=$(podman image inspect --format '{{.Id}}' docker.io/library/postgres:17-bookworm)
printf 'java=%s\npostgres=%s\n' "$JAVA_BASE" "$PG_BASE" > base-images.txt
podman build --platform linux/arm64 --pull=never \
  --build-arg JAVA_RUNTIME_IMAGE="$JAVA_BASE" -t localhost/lexiflow-api:validation build/api
podman build --platform linux/arm64 --pull=never \
  --build-arg POSTGRES_RUNTIME_IMAGE="$PG_BASE" -t localhost/lexiflow-postgres:validation build/postgres
API_IMAGE=$(podman image inspect --format '{{.Id}}' localhost/lexiflow-api:validation)
PG_IMAGE=$(podman image inspect --format '{{.Id}}' localhost/lexiflow-postgres:validation)
INSTALL_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
printf 'LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE=%s\nLEXIFLOW_POSTGRES_IMAGE=%s\nLEXIFLOW_INSTALLATION_ID=%s\nLEXIFLOW_RELEASE_KEY=local-validation-%s\n' \
  "$API_IMAGE" "$PG_IMAGE" "$INSTALL_ID" "$INSTALL_ID" > release.env
podman image inspect --format '{{.Architecture}}' "$API_IMAGE" "$PG_IMAGE"
```

两个镜像架构都应是 `arm64`。配置绑定精确 ID，后续重打同名标签不会偷偷改变这个安装。CSV 和词库 ZIP 不进入 Git，也不进应用镜像，在下一节本机生成。

## 1.4. 从 GitHub 准备精简词库

下面的 Python、Git 和解压工具只安装在一次性工具容器内，不安装到 Mac。

```sh
mkdir -p data
chmod 700 data
podman build --platform linux/arm64 \
  -t lexiflow-source-tools:local \
  -f ops/podman/source-tools.Containerfile ops/podman
podman run --rm --platform linux/arm64 --memory 2g \
  -v "$PWD/ops:/kit/ops:ro" -v "$PWD/scripts:/kit/scripts:ro" \
  -v "$PWD/data:/data" \
  lexiflow-source-tools:local \
  /kit/ops/podman/fetch-ecdict.sh /kit /data
```

脚本只从 `https://github.com/skywind3000/ECDICT.git` 取得来源锁指定的 commit，核对原文件摘要后按配套规则生成精简词库；不是下载随时变化的最新版，也不是按字母顺序截取前若干词。它使用独立新目录，不覆盖之前的词库。

执行成功后会给出输出目录。容器内的 `/data/某目录` 对应本机当前目录下的 `data/某目录`。将其**本机绝对路径**填到 `release.env`：

```text
LEXIFLOW_SOURCE_DIR=/Users/你的用户名/Applications/LexiFlow-validation/data/脚本实际输出目录
```

目录中应有 `stardict.csv` 和精简源 ZIP。这里使用 CSV 导入本机数据库；**精简源 ZIP 不是应用的已发布安装资料包**，不要改名为 `dataset.zip` 交给其他初始化入口。

脚本报摘要错误、解压失败或未打印成功结果时停止，不手动跳过校验。上游许可随来源保留；本机使用过程不等于批准将词库重新对外分发。

## 1.5. 创建本机密码

只在首次安装执行。若已有 `secrets` 目录，不要覆盖或重新生成密码，否则会与已有数据库密码不一致。

```sh
umask 077
mkdir secrets
openssl rand -hex 32 > secrets/postgres-password
openssl rand -hex 32 > secrets/app-password
chmod 700 secrets
chmod 444 secrets/postgres-password secrets/app-password
```

私有父目录 `secrets` 只允许当前 Mac 用户访问；内部文件需供非 root 容器只读挂载。不要把目录上传、发给别人、截图分享，也不要把密码放进 `release.env`。

后续命令都从验证包目录执行，并固定使用项目名 `lexiflow-validation`。不要混用 `lexiflow.sh` 管理同一安装，也不要在已有同名项目上重复首次安装。

## 1.6. 启动数据库并导入词库

先验证配置：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml config
```

确认数据库映射为 `127.0.0.1:15432:5432`，应用为 `127.0.0.1:18080:8080`；不能变成 `0.0.0.0`。此检查不证明服务已经启动。

启动数据库：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml up -d postgres
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml exec postgres \
  pg_isready -h 127.0.0.1 -U lexiflow -d lexiflow
```

等待检查输出 `accepting connections`。再首次导入：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml run --rm initialize
```

必须看到导入成功且命令退出码为 0。该操作会在本项目数据库建表并发布词库；不是日常启动命令。不要在已存在的库上反复运行；失败时先看本节末尾的排查，不自行删表。

当前精简词库的参考结果：来源记录 266,285、发布词条 265,999、词形记录 320,100。以后配套词库规则变化时，以对应包说明为准。

## 1.7. 启动并验证应用

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml up -d --no-deps api
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml ps
curl --fail --silent --show-error http://127.0.0.1:18080/actuator/health/readiness
curl --fail --silent --show-error http://127.0.0.1:18080/api/v1/runtime-status
```

等待 readiness 返回成功，并确认状态中 `mode` 为 `formal`、`ready` 为 `true`、`reason` 为 `OK`，软件/协议/资料版本符合配套包说明。仅 liveness 成功、容器显示 running、端口能连接，都不代表词库正式就绪。

随后保留本安装目录的 `extension/` 文件夹，打开 Chrome `chrome://extensions`，开启开发者模式，选择“加载已解压的扩展程序”，指向本安装目录下含 `manifest.json` 的 `extension/` 目录。打开扩展弹窗确认正式就绪，再打开 YouTube 视频并开启英文字幕。英文应正常显示；有符合规则的词段时才出现中文提示。不是每个词都会翻译。

## 1.8. 查看 PostgreSQL 数据

可使用 DBeaver、DataGrip 或其他 PostgreSQL 客户端。连接参数：

| 项目 | 值 |
| --- | --- |
| Host | `127.0.0.1` |
| Port | `15432` |
| Database | `lexiflow` |
| User | `lexiflow` |
| Password | 本机 `secrets/app-password` 的内容 |
| Schema | `lexiflow_release` |

应用账号不是超级用户，但具有本应用数据的写权限；查看数据时请把客户端设成只读会话，不直接编辑表。例如执行：

```sql
BEGIN READ ONLY;
SELECT count(*) FROM lexiflow_release.lexicon_prepared_entry;
SELECT count(*) FROM lexiflow_release.lexicon_hint_lookup;
SELECT * FROM lexiflow_release.lexicon_dataset;
ROLLBACK;
```

从另一台电脑连接时，**不要把数据库绑定到全部网卡**。在查看数据的电脑上建立 SSH 隧道（Mac 需已允许该账号远程登录）：

```sh
ssh -N -L 15433:127.0.0.1:15432 用户名@部署Mac的地址
```

然后数据库客户端连接自己电脑的 `127.0.0.1:15433`，其余参数不变。SSH 窗口需保持打开，不需要将数据库端口暴露到局域网或公网。

## 1.9. 停止、再启动和移除

日常停止，数据保留：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml stop
```

再次使用：先按 1.6 启动并检查 `postgres`，再按 1.7 启动 `api`；**不要重跑首次词库导入**。

只移除这个验证项目的容器与网络，保留数据卷：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml down
```

仅当明确决定丢弃**这个独立验证项目**全部数据库数据时，才运行下面的破坏性命令：

```sh
podman compose --env-file release.env -p lexiflow-validation -f compose.yaml down -v
```

不要用于已有正式安装，不使用 `podman system prune`。以上命令不删除本机词库下载目录、密码文件或 Chrome 偏好。更新应用前保留旧镜像、目录和数据；尚无经此 Podman Compose 路径实测的更新回滚步骤，不在旧卷上试装不匹配的新包。

## 1.10. 常见问题

- **配置提示变量未设置**：检查本机生成的 `release.env`，以及 `LEXIFLOW_SOURCE_DIR` 是否为真实本机绝对路径。
- **镜像找不到**：先确认 1.3 的构建成功，再核对配置中的精确镜像 ID；不要替换成任意网上镜像。
- **数据库无法启动/端口占用**：确认 `15432` 没有被其他服务使用；不要停止归属不明的进程。
- **导入报空间不足**：检查 Podman 虚拟机磁盘空间。本指南使用持久卷，不使用开发测试中曾不足的 256 MiB 临时存储。
- **容器不能读密码或CSV**：检查私有父目录与挂载文件权限，词库最终目录需可被容器 UID 10001 读取；不要改成全局可写。
- **readiness 不成功**：先看导入命令是否成功、数据库是否健康，再看日志；不要以 liveness 替代 readiness。
- **日志排查**：运行 `podman compose --env-file release.env -p lexiflow-validation -f compose.yaml logs --tail=100 postgres api`。分享前检查日志，不发送密码文件、完整配置、数据库内容或观看记录。
- **扩展未就绪**：确认 Chrome 所在机器能访问本机 `127.0.0.1:18080`。本指南默认 Chrome 与部署在同一台 Mac；远程浏览器方案需另行配置，不直接开放 API 到公网。
