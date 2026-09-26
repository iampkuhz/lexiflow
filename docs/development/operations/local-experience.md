# 1. 本地体验：API 与 Chrome 扩展

> 位置：[工程地图](../overview.md) → [运行与环境](../operations.md) → 本地体验。首次准备从[根 README](../../../README.md#本地启动)开始；输出是本机可使用的浏览器/API，不是正式验收证据。


以下命令在仓库根目录执行。需要 **Eclipse Temurin Java 25、Python 3、Node.js/npm 和 Chrome**，以及本机 `lsof`、`ps`；Gradle 使用仓库自带 Wrapper。正常视频体验需要可连接且已发布词库的 PostgreSQL 开发库；有限演示词库仅供隔离自动测试使用。

## 1.1. 确认 Java

```bash
python3 -m scripts.environment.java_exec java -version
```

如果提示找不到 Java 25，安装 Eclipse Temurin 25 后用 `export LEXIFLOW_JAVA_HOME=/absolute/path/to/jdk-25` 指定；不要使用系统里的其他 Java 版本替代。

## 1.2. 初始化本地配置，再启动确定性 API

首次使用先完成[根 README 的本地步骤](../../../README.md#本地启动)：编译 API/扩展 → 配置已有开发库连接与来源路径 → 校验来源 → 初始化空 schema → 发布词库。本页不要求安装容器工具，也不提供数据库服务创建流程；API 不负责建表或升级结构。

已有数据库结构不会随代码更新。遇到 `relation "lexicon_hint_lookup" does not exist` 时，停止启动重试，进入[开发库检查与重建](lexicon-import.md#15-已有开发库结构不匹配时)；不要只补一张表或一个字段。日常启动可以复用匹配的已发布数据库，不重复初始化。


当前 API 不读取、不需要、也不会调用模型服务。在设置过 `JDBC_URL` 的终端，完整词库启动方式：

```bash
python3 -m scripts.environment.start_api
```

启动前先确认数据库运行且词库已发布。启动器仅从 `JDBC_URL` 获取本地数据库地址；缺失或空白时返回 `BLOCKED`，不转入演示词库。它不会自动读取 `.env`，也不会继承被终止的旧 API 环境变量。

默认端口为 `18080`。如果已被占用，启动器会列出 PID、进程名称等身份信息，并询问：

```text
是否终止上述进程（SIGTERM）并启动 API？[y/N]
```

只有输入 `y` 或 `yes` 才会终止列出的进程；回车、`n`、取消输入或非交互环境均不会杀进程。确认后最多等待 10 秒，端口释放后再启动；不会自动 `kill -9`。

确需换端口时，在启动 API 与构建扩展的环境中共同设置一次 `LEXIFLOW_API_PORT`，例如 `export LEXIFLOW_API_PORT=18081`，再执行零参数启动与构建命令。地址与 host permission 来自同一端口值，不允许远端主机；质量检查可能重建扩展，实际安装前应在相同环境中再次构建。

保持 API 终端运行，在另一个终端检查：

```bash
curl --fail http://127.0.0.1:18080/actuator/health
```

健康响应只说明 API 已启动，不代表词库已发布。没有候选时返回 `NO_PENDING`，只保留英文；不会用词典内容以外的模型结果补充提示。

启动日志应看到：

```text
runtime lexicon=postgres publishedVersion=<非零版本号>
```

若日志出现 `lexicon=builtin-demo`，说明进程没有通过正常本地启动器接上数据库；不要将它当作完整词库体验。`publishedVersion=0` 表示数据库没有已发布词库。

## 1.3. 构建并加载扩展

```bash
cd extension
npm ci
npm run build
```

1. 在 Chrome 地址栏打开 `chrome://extensions`，开启“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择本仓库的 `extension/dist` 文件夹，而不是 `extension`。
3. 打开 YouTube 视频，选择英文字幕并开启 CC。英文先显示；命中合格词段后看到同一行中的 `word(中文)`。被提示的英文词段带底线；短语连同词间空格属于同一标记，跨源字幕分行时仍保留分行。
4. 更新代码后重新运行 `npm run build`，在扩展管理页刷新扩展，再刷新 YouTube 页面。

停止时，在 API 终端按 `Ctrl+C`；扩展可在管理页禁用。扩展不保存字幕，也不会补发旧字幕。

## 1.4. 没有效果时看哪里

- **API 终端**：先查看 `runtime lexicon`。每次有效请求打印 `hint_result apiMs=... english="..." final="..."`；`final` 与 `english` 相同时没有可展示提示，不能据此断言完全没有词库候选。此路径不会调用模型。
- **YouTube 页面开发者工具 → Console**：筛选 `[LexiFlow]`，查看 `caption` 阶段的 `state`、`sequence`、`elapsedMs`。
- **扩展管理页 → LexiFlow → Service worker**：查看 `api` 阶段结果和请求耗时。

已发布的近三百万词条不等于每个词都会出现在字幕里：基础词、未处理或多义且缺可信短释的词条、结构不完整的短语、同区间歧义、重叠落选及每段第 4 个及之后的候选都不会展示。有来源排名的非基础单词优先于未排名短语；短释仍不保证特定语境的词义正确，不能把不提示解释成词库没有该词。

API 终端日志会打印英文原文和词段插入后的中英结果，可能包含真实字幕；只在本机查看，不复制到共享日志或提交仓库。它不打印视频地址、内容身份、模型输入输出或密钥；扩展日志仍不含原文。字幕变化、拖动进度、关闭字幕和页面跳转都会清除旧提示；迟到结果直接放弃，英文不会被阻塞。


下一步：[扩展 E2E](extension-e2e.md)验证工程链路；遇到故障先看[按阶段排障](../troubleshooting.md)。需要准备资料时进入[离线词库导入](lexicon-import.md)。
