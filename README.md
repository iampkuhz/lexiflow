# LexiFlow

LexiFlow 在 YouTube 英文字幕的词语后插入来自已发布词库的简短中文提示，例如
`They completed the assignments(作业).`。英文先显示；当前机器请求只执行确定性的词库查询、范围匹配和提示规则，**不会调用大模型，也不需要配置模型服务**。

## 本地启动

以下命令在仓库根目录执行。需要 **Eclipse Temurin Java 25、Python 3、Node.js/npm 和 Chrome**，以及本机 `lsof`、`ps`；Gradle 使用仓库自带 Wrapper。正常视频体验还需要 Podman、已发布的 PostgreSQL 词库；不连接数据库时只能使用有限的内置演示词库。

### 1. 确认 Java

```bash
python3 -m scripts.environment.java_exec java -version
```

如果提示找不到 Java 25，安装 Eclipse Temurin 25 后用 `export LEXIFLOW_JAVA_HOME=/absolute/path/to/jdk-25` 指定；不要使用系统里的其他 Java 版本替代。

### 2. 启动确定性 API

当前 API 不读取、不需要、也不会调用模型服务。完整词库启动方式：

```bash
python3 -m scripts.environment.start_api \
  --database-url 'jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres'
```

启动前先确认数据库运行且词库已发布。词库来源由数据库配置决定，不由 profile 名称决定。启动器不会自动读取 `.env`，也不会继承被终止的旧 API 环境变量。

默认端口为 `18080`。如果已被占用，启动器会列出 PID、进程名称等身份信息，并询问：

```text
是否终止上述进程（SIGTERM）并启动 API？[y/N]
```

只有输入 `y` 或 `yes` 才会终止列出的进程；回车、`n`、取消输入或非交互环境均不会杀进程。确认后最多等待 10 秒，端口释放后再启动；不会自动 `kill -9`。

可用 `--port 18081` 指定其他端口，但扩展目前固定访问 `18080`，正常使用请保留默认值。

保持 API 终端运行，在另一个终端检查：

```bash
curl --fail http://127.0.0.1:18080/actuator/health
```

健康响应只说明 API 已启动，不代表词库已发布。没有候选时返回 `NO_PENDING`，只保留英文；不会用词典内容以外的模型结果补充提示。

启动日志应看到：

```text
runtime lexicon=postgres publishedVersion=<非零版本号>
```

`lexicon=builtin-demo` 表示没有接上数据库，只使用 `figure out`、`reliable`、`context`、`caption`、`deliver` 五个演示词条；`publishedVersion=0` 表示数据库没有已发布词库。

仅测试 API 能否启动时，才使用不带数据库参数的 `python3 -m scripts.environment.start_api`；这不是完整词库体验的启动方式。

### 3. 构建并加载扩展

```bash
cd extension
npm ci
npm run build
```

1. 在 Chrome 地址栏打开 `chrome://extensions`，开启“开发者模式”。
2. 点击“加载已解压的扩展程序”，选择本仓库的 `extension/dist` 文件夹，而不是 `extension`。
3. 打开 YouTube 视频，选择英文字幕并开启 CC。英文先显示；命中词库后看到同一行中的 `word(中文)`。
4. 更新代码后重新运行 `npm run build`，在扩展管理页刷新扩展，再刷新 YouTube 页面。

停止时，在 API 终端按 `Ctrl+C`；扩展可在管理页禁用。扩展不保存字幕，也不会补发旧字幕。

## 没有效果时看哪里

- **API 终端**：先查看 `runtime lexicon`。`lexicon candidates=0 → rules selected=0 → NO_PENDING` 表示请求已到后端但没有词库候选；这条路径不会调用模型。
- **YouTube 页面开发者工具 → Console**：筛选 `[LexiFlow]`，查看 `caption` 阶段的 `state`、`sequence`、`elapsedMs`。
- **扩展管理页 → LexiFlow → Service worker**：查看 `api` 阶段结果和请求耗时。

日志不打印字幕原文、视频地址、模型输入输出或密钥。字幕变化、拖动进度、关闭字幕和页面跳转都会清除旧提示；迟到结果直接放弃，英文不会被阻塞。

## 更多说明

- [产品目标](docs/product/product-brief.md)
- [架构入口](docs/architecture/overview.md)
- [产品路线图：基础体验 → 性能与效率 → 事后改进](docs/roadmap/master-plan.md)
- [路线图状态与下一步](docs/roadmap/master-plan/status.md)
- [扩展体验、自动化测试与边界](docs/development/toolchain-reproduction/chrome-extension-e2e.md)
- [开发校验手册](docs/development/validation.md)
- [Harness](harness/README.md)
