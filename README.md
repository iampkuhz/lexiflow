# LexiFlow

在 YouTube 英文字幕的词语后提供来自已发布词库的简短中文提示，例如 `assignments(作业)`。**英文立即显示；观看请求不调用模型，也不等待事后分析。**

## 本地启动

按 **本地编译 → 初始化配置与词库 → 启动** 执行。以下命令均在仓库根目录运行，每一步成功后再继续；这里不创建数据库服务，也不包含容器环境流程。

### 1. 本地编译

需要 **Eclipse Temurin Java 25、Python 3、Node.js/npm、Chrome**，以及启动器使用的 `lsof`、`ps`。Gradle 使用仓库 Wrapper，不需要另装 Gradle。找不到 Java 25 时，用 `LEXIFLOW_JAVA_HOME` 指定其安装目录。

```bash
python3 -m scripts.environment.java_exec java -version
python3 -m scripts.environment.java_exec backend/gradlew -p backend :apps:api:classes
npm --prefix extension ci
npm --prefix extension run build
```

这些命令只编译 API 和扩展，不会建表或导入词库。

### 2. 配置一次，初始化并导入

使用已有且可连接的本项目 PostgreSQL 开发库。在同一终端只设置两个值（改成你的实际地址与文件路径）：

```bash
export JDBC_URL='jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres'
export STARDICT_CSV='/absolute/path/stardict.csv'
```

这些任务用于合法取得的 **ECDICT StarDict / MIT** 文件；来源不随仓库提供。环境变量同时供初始化、发布和启动使用；新终端需重新设置，不自动读取 `.env`，不把凭据提交到仓库。

**首次使用空 schema，依次执行下面两条命令：**

```bash
# 建表：自动使用仓库的 infra/postgres/schema.sql。
python3 -m scripts.environment.java_exec backend/gradlew -p backend postgresInit

# 真正导入数据库并发布；等待完成后再启动 API。
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconPublish
```

已有匹配结构的已发布词库，直接进入第 3 步；已有匹配结构但尚未发布，只执行 `lexiconPublish`。日常启动不重复初始化或导入。

**`refusing to initialize a non-empty schema` 表示已有数据，初始化没有执行。** 若同时出现 `lexicon_hint_lookup` 缺失，先按[结构检查](docs/development/operations/lexicon-import.md#15-已有开发库结构不匹配时)核对连接；确认本项目开发数据可丢弃后人工重建为空 schema，再执行以上两条命令。不会自动清库，也不要只补一个字段。

如果只是想先检查 CSV，可单独执行下面的命令；**它不导入数据，也不代替 `lexiconPublish`**，不是日常启动必经步骤：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconValidate
```

### 3. 启动 API，加载扩展

在设置过 `JDBC_URL` 的同一终端执行：

```bash
python3 -m scripts.environment.start_api
```

启动器通过 Gradle `bootRun` 运行本地源码，会检查需要重新编译的部分；不会初始化数据库或发布词库。保持该终端运行，日志应出现 `runtime lexicon=postgres publishedVersion=<非零版本号>`。另开终端检查：

```bash
curl --fail http://127.0.0.1:18080/actuator/health
```

在 Chrome 的 `chrome://extensions` 开启开发者模式，加载第 1 步生成的 `extension/dist`，再打开 YouTube 英文字幕。health 成功不等于词库已发布；端口冲突、扩展刷新与停止方式见[本地体验](docs/development/operations/local-experience.md)。

正常启动只读取 `JDBC_URL`；未配置时返回 `BLOCKED`，不会静默使用有限演示词库。API 与扩展默认共用本机端口 18080；确需改端口时，在运行启动器与构建扩展的环境中使用同一个 `LEXIFLOW_API_PORT`。

## 从哪里继续

- **理解产品与系统：**[产品说明](docs/product/product-brief.md) → [架构总览](docs/architecture/overview.md)。
- **修改与交付：**[工程地图](docs/development/overview.md) → [开发交付](docs/development/change-delivery.md)。
- **没有效果或检查失败：**[按阶段排障](docs/development/troubleshooting.md)。
- **完整导航与当前状态：**[文档首页](docs/README.md)、[路线图状态](docs/roadmap/master-plan/status.md)。

规则真源为 [AGENTS.md](AGENTS.md) 与 [Harness](harness/README.md)；它们不是用户操作手册。
