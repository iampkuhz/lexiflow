# LexiFlow

在 YouTube 英文字幕的词语后显示已发布词库提供的简短中文提示，例如 `assignments(作业)`。

## 本地启动

需要 Eclipse Temurin Java 25、Python 3、Node.js/npm、Chrome、`lsof`、`ps`，以及一个正在运行、可连接的本项目 PostgreSQL 数据库。准备合法取得的 ECDICT StarDict（MIT）`stardict.csv`；词库文件不随仓库提供。以下命令均在仓库根目录执行，Gradle 使用仓库自带的 Wrapper。

### 1. 配置并发布词库

在同一终端设置实际数据库地址和 CSV 绝对路径：

```bash
export JDBC_URL='jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres'
export STARDICT_CSV='/absolute/path/stardict.csv'
```

首次使用空 schema，或需要明确重建本项目词库表时，执行：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconRebuild
```

该命令先完整校验来源；已有本项目词库表时，显示数据库与 schema，并要求输入精确确认文本后才删除三张表、重建结构、完整导入。它不删除来源文件或其他项目数据；运行前须停用 API、核对目标和备份，完成后处理旧缓存及本机偏好引用。已有匹配结构且已发布词库的数据库无需重复执行。新终端需重新设置 `JDBC_URL`；命令不会自动读取 `.env`。不要将数据库凭据或词库文件提交到仓库。找不到 Java 25 时，设置 `LEXIFLOW_JAVA_HOME` 为其安装目录。

`lexiconRebuild` 是单个 Gradle 任务，内部四个阶段会分别打印开始、完成和内部步骤；长时间执行或等待确认时每 3 分钟打印一次状态与耗时。Gradle 的 `EXECUTING` 百分比不是导入进度；详细解释见[词库导入操作](docs/development/operations/lexicon-import.md#13-准备数据库并发布)。

### 2. 构建扩展并启动 API

```bash
npm --prefix extension ci
npm --prefix extension run build
python3 -m scripts.environment.start_api
```

启动器会通过 Gradle 编译并运行 API。保持终端运行，确认日志包含 `runtime lexicon=postgres publishedVersion=<非零版本号>`。API 和扩展默认使用本机端口 `18080`；如需改端口，在构建扩展和启动 API 前设置相同的 `LEXIFLOW_API_PORT`。

### 3. 在 Chrome 使用

打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择仓库内的 `extension/dist`。打开 YouTube 视频并开启英文字幕（CC），即可在命中的词语后看到中文提示。停止 API 时在启动终端按 `Ctrl+C`。
