# LexiFlow

在 YouTube 英文字幕的词语后显示已发布词库提供的简短中文提示，例如 `assignments(作业)`。

## 本地启动

需要 Eclipse Temurin Java 25、Python 3、Node.js/npm、Chrome、`lsof`、`ps`，以及一个正在运行、可连接的本项目 PostgreSQL 数据库。准备合法取得的 ECDICT StarDict（MIT）`stardict.csv`；词库文件不随仓库提供。以下命令均在仓库根目录执行，Gradle 使用仓库自带的 Wrapper。

### 1. 首次配置与词库准备

本机配置保存在 ignored 的 `.local/lexiflow/runtime.json`，Java 命令和 API 启动器共用它，新终端不必重复 `export`。文件只接受 `JDBC_URL` 和 `STARDICT_CSV` 两个非空字符串；显式环境变量优先，不读取 `.env`，不执行配置内容。词库路径必须为绝对路径。不要提交此文件或数据库凭据。

本项目开发库固定使用 `127.0.0.1:15432/lexiflow`，API 使用 `127.0.0.1:18080`。首次配置文件的结构如下；来源路径须指向你合法取得的 `stardict.csv`，不是需要猜测的业务参数：

```json
{
  "JDBC_URL": "jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres",
  "STARDICT_CSV": "/absolute/path/stardict.csv"
}
```

将文件权限设为仅本人可读写（`chmod 600 .local/lexiflow/runtime.json`）。有本项目既存开发库就直接复用，不创建第二个数据库；使用仓库的 Podman 开发资源时，独立启动数据库的命令为 `podman compose -f infra/local/compose.yaml up -d postgres`。这会保留开发数据，不依赖 Redis 或模型服务。

**已有匹配结构和已发布词库时跳到第 2 步，不要每天重建。** 首次空库或明确需要重建本项目三张表时才运行：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconRebuild
```

命令先完整校验来源；已有表时会停在醒目的“等待你的输入”提示。确认已停用 API、已备份且目标正确后，在原终端输入提示中的精确 `REBUILD <database>.<schema>` 并按 Enter。等待期间不会刷日志，也不会删除数据；`Ctrl+C`、空行、错误文本或 EOF 均不授权删除。确认后才重建三张表并完整导入，不删除 CSV 或其他项目对象。

`java_exec` 只选择 Temurin 25 并把原终端交给 Gradle，不是词库编排脚本。各任务可独立运行，例如只校验不写库：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconValidate
```

正确配置 `JAVA_HOME`、`JDBC_URL`、`STARDICT_CSV` 的终端也可以直接运行 `backend/gradlew -p backend lexiconValidate` 等原生任务；无需额外 Python 业务入口。默认 plain console 不显示易误解的动态百分比。四阶段仅实际执行时每 3 分钟报告状态，等待输入保持安静，详见[词库导入操作](docs/development/operations/lexicon-import.md#13-准备数据库并发布)。

### 2. 构建扩展并启动 API

```bash
npm --prefix extension ci
npm --prefix extension run build
python3 -m scripts.environment.start_api
```

启动器会通过 Gradle 编译并运行 API。保持终端运行，确认日志包含 `runtime lexicon=postgres publishedVersion=<非零版本号>`。API 和扩展默认使用本机端口 `18080`，日常无需填写参数。以后启动只需最后一条命令；源码有更新才重新构建扩展。端口冲突时启动器会显示占用进程并等待确认，不会自行杀进程。

### 3. 在 Chrome 使用

打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择仓库内的 `extension/dist`。打开 YouTube 视频并开启英文字幕（CC），即可在命中的词语后看到中文提示。停止 API 时在启动终端按 `Ctrl+C`。
