# 1. 本地体验：API 与 Chrome 扩展

> 位置：[工程地图](../overview.md) → [运行与环境](../operations.md) → 本地体验。Docker 用户先看[根 README 的安装主线](../../../README.md#docker-安装与使用)；源码开发从下方本地步骤开始，输出是本机可使用的浏览器/API，不是正式验收证据。


以下 macOS 源码开发命令在仓库根目录执行。需要 **Eclipse Temurin Java 25、Python 3、Node.js/npm 和 Chrome**，以及本机 `lsof`、`ps`；Gradle 使用仓库自带 Wrapper。正常视频体验需要可连接且已发布词库的 PostgreSQL 开发库；有限演示词库仅供隔离自动测试使用。这里的开发数据库初始化与重建不适用于 Docker 发行安装数据。

## 1.1. 确认 Java

```bash
python3 -m scripts.environment.java_exec java -version
```

如果提示找不到 Java 25，安装 Eclipse Temurin 25 后用 `export LEXIFLOW_JAVA_HOME=/absolute/path/to/jdk-25` 指定；不要使用系统里的其他 Java 版本替代。

## 1.2. 初始化本地配置，再启动确定性 API

先在仓库根编译 API，并创建本机配置目录：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend :api:bootJar
mkdir -p .local/lexiflow
```

将以下配置写入 `.local/lexiflow/runtime.json`，把来源文件路径改为已合法取得的 ECDICT StarDict CSV 绝对路径，并按实际开发数据库填写连接：

```json
{
  "JDBC_URL": "jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres",
  "STARDICT_CSV": "/absolute/path/stardict.csv"
}
```

```bash
chmod 600 .local/lexiflow/runtime.json
```

已有匹配的已发布词库时直接启动，不重复导入。首次准备开发库可按[数据库准备和发布步骤](lexicon-import.md#13-准备数据库并发布)执行；使用仓库容器配置时需先安装并启动 Podman，再启动 `infra/local/compose.yaml` 的 PostgreSQL。初始化或结构重建须先核对目标和备份，不能用于发行安装资料；API 启动本身不建表、不重建结构。随后按下节构建扩展，不提交本机配置或词库文件。

已有数据库结构不会随代码更新。遇到 `relation "lexicon_hint_lookup" does not exist` 时，停止启动重试，进入[开发库检查与重建](lexicon-import.md#15-已有开发库结构不匹配时)；不要只补一张表或一个字段。日常启动可以复用匹配的已发布数据库，不重复初始化。


当前 API 不读取、不需要、也不会调用模型服务。完成本机配置后，在任意新终端，完整词库启动方式：

```bash
python3 -m scripts.environment.start_api
```

启动前先确认数据库运行且词库已发布。启动器从 `.local/lexiflow/runtime.json` 读取 `JDBC_URL`，显式环境变量优先；缺失或空白时返回 `BLOCKED`，不转入演示词库。它不会自动读取 `.env`，也不会继承被终止的旧 API 环境变量。配置文件格式错误或包含未知键时明确停止，不打印配置值。

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
3. 打开 YouTube 视频，选择英文字幕并开启 CC。英文先显示；命中合格词段后看到同一行中的 `word(中文)`。仅作为一个整体提示的多词词组带黄色底线，包含词间空格；单词提示不画线，相邻的独立单词提示不合并画线。跨源字幕分行时仍保留分行；行内下划线与行距确保不被下一行背景遮挡。
4. 更新代码后重新运行 `npm run build`，在扩展管理页刷新扩展，再刷新 YouTube 页面。

停止时，在 API 终端按 `Ctrl+C`；扩展可在管理页禁用。扩展不保存字幕，也不会补发旧字幕。

## 1.4. 没有效果时看哪里

- **API 终端**：先查看 `runtime lexicon`。字幕增量、`final`、`interrupted` 与视频开始事件默认输出为 `MM-dd HH:mm:ss|LEVEL|关联ID|事件|定位字段|正文` 单行日志；增量正文将英文与实际显示的中文提示放在同一行。控制台不依赖分析文件开关，也不会默认创建或写入字幕 JSONL。`start_api` 默认仅绑定 `127.0.0.1`；为兼容旧调用可传 `--caption-debug`，它只提示字幕日志已默认开启，不再转成 API 参数。容器 `api-debug` 保留为与 `api` 相同的启动别名。日志包含字幕正文及视频定位，只可本机查看，不得外发或放入 CI artifact。失败、队列溢出或日志 I/O 均不阻塞英文和提示。此路径只查询已发布资料，不会调用模型。
- **片段分析 JSONL**：仅在显式启用 `lexiflow.segment-analysis.enabled=true` 时写入，默认路径为 ignored 的 `tmp/analysis/caption-segments.jsonl`，可用 `LEXIFLOW_SEGMENT_LOG_PATH` 指定私有路径。单文件上限为 16 MiB，达到上限后停止追加，不会无界增长；它含英文及提示分析结果，须按敏感本机数据处理，不得外发或进入 CI artifact。`NO_HINT` 表示没有可展示提示，不等于词库没有候选。
- **YouTube 页面开发者工具 → Console**：筛选 `[LexiFlow]`，查看 `caption` 阶段的 `state`、`sequence`、`elapsedMs`。
- **扩展管理页 → LexiFlow → Service worker**：查看 `api` 阶段结果和请求耗时。

已发布的近三百万词条不等于每个词都会出现在字幕里：基础词、未完成准备或缺合法已发布默认短释的词条、结构不完整的短语、同一区间的不同词条歧义及重叠落选候选不会展示。多义词使用已发布默认第一候选；优先较长词段，再按原文位置选择不重叠范围，没有固定三条提示上限。同一词条在一条字幕中只提示一次。短释不保证特定语境的词义正确，不能把不提示解释成词库没有该词。

片段分析文件可能包含真实字幕，只在本机查看、保留或删除，不复制到共享日志或提交仓库。它不记录视频地址、内容身份、模型输入输出或密钥；记录的是 API 处理结果，不能断言用户最终看到了提示。扩展日志仍不含原文。字幕连续追加及滚动时保留仍有效提示，裁剪窗口内已经退出的行不会因动画复位回闪；无重叠替换、拖动进度、关闭字幕和页面跳转会清除旧提示；迟到结果直接放弃，英文不会被阻塞。


下一步：[扩展 E2E](extension-e2e.md)验证工程链路；遇到故障先看[按阶段排障](../troubleshooting.md)。需要准备资料时进入[离线词库导入](lexicon-import.md)。
