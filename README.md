# LexiFlow

看 YouTube 英文视频时，生词旁直接显示中文短释，不用切走查词。

> 效果示意：虚构字幕中的生词提示。

![LexiFlow 在英文字幕中显示中文短释](assets/readme/lexiflow-demo.gif)

## 本地启动

准备 Eclipse Temurin Java 25、Python 3、Node.js/npm、Chrome 和本项目的 PostgreSQL 数据库。首次导入词库还需要合法取得的 ECDICT StarDict `stardict.csv`。以下命令都在仓库根目录执行。

### 1. 编译

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend :api:bootJar
npm --prefix extension ci
npm --prefix extension run build
```

构建完成后，扩展位于 `extension/dist`。

### 2. 初始化配置与词库

创建本机配置文件 `.local/lexiflow/runtime.json`：

```bash
mkdir -p .local/lexiflow
```

将下面内容写入该文件，并把 `STARDICT_CSV` 改成你实际文件的**绝对路径**：

```json
{
  "JDBC_URL": "jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres",
  "STARDICT_CSV": "/absolute/path/stardict.csv"
}
```

```bash
chmod 600 .local/lexiflow/runtime.json
```

已经导入过词库，且数据库仍可用？**跳过下面命令，直接进入第 3 步。** 首次使用仓库提供的 PostgreSQL 时，先安装并启动 Podman，再执行：

```bash
podman compose -f infra/local/compose.yaml up -d postgres
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconRebuild
```

`lexiconRebuild` 只在首次空库或明确需要重建时运行；如果提示已有表，先确认目标和备份，再决定是否继续。详见[词库导入操作](docs/development/operations/lexicon-import.md#13-准备数据库并发布)。不要提交本机配置或词库文件。

### 3. 启动并使用

```bash
python3 -m scripts.environment.start_api
```

保持终端运行，确认日志出现 `runtime lexicon=postgres publishedVersion=<非零版本号>`。然后：

1. 打开 Chrome 的 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择 `extension/dist`。
2. 打开 YouTube 视频并开启英文字幕（CC）。命中的生词会在字幕中显示中文短释。

点击工具栏的 LexiFlow 图标可开关当前页面的提示；点击字幕中的中文可隐藏该词，在弹窗的“提示偏好 · 仅本机”中可恢复。日常使用只需启动数据库和 API；停止 API 时按 `Ctrl+C`。遇到问题看[本机体验与排障](docs/development/operations/local-experience.md#14-没有效果时看哪里)。
