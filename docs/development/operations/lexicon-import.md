# 1. 离线词库导入：校验、发布与恢复

> 位置：[工程地图](../overview.md) → [Operations](../operations.md) → 离线词库导入。此流程为本机体验准备已发布资料，不是每次代码交付的必经步骤。

首批完整来源为本机 `stardict.csv`，不叠加 `ecdict.csv`。来源文件不进入仓库，也不使用用户字幕、观看历史或熟悉度。发布完成后回到[本机体验](local-experience.md)启动 API；观看只消费已发布版本。

## 1.1. 先看完整链路

| 阶段 | 做什么 | 输出与失败边界 |
| --- | --- | --- |
| I1 准备来源 | 核对路径、首行与摘要 | 来源不明确先停，不猜许可证或自动转换大文件 |
| I2 离线检查 | validate → basic-report / prewarm-report | 不连接数据库；报告帮助人工确认，不代表已发布 |
| I3 初始化与发布 | 确认开发库归属，初始化空 schema，再 publish | 写入 STAGED，完整扫描后才变为 PUBLISHED |
| I4 消费与恢复 | API 查询发布版本；中断按相同来源恢复 | 半成品不可见；来源改变则建立新批次 |

状态和事务关系见[持久化模型](../../architecture/data-model.md)。需要核对单行转换、字段、频率公式或基础词选择时，再读[记录处理 Reference](lexicon-import/record-processing.md)，不用在操作主干展开所有字段。

## 1.2. 校验文件与检查报告

准备本机 `stardict.csv`，确认首行和摘要；**直接把该文件传给命令**，不要把 StarDict 文件先转换成
`lexiflow-lexicon-v1.csv`，也不要叠加 `ecdict.csv`：

```bash
export STARDICT_CSV=/absolute/path/stardict.csv
shasum -a 256 "$STARDICT_CSV"
head -n 1 "$STARDICT_CSV"
```

文件路径和首行正确后，执行不连接数据库的完整校验：

```bash
LEXICON_ARGS=$(printf 'validate\037--input\037%s' "$STARDICT_CSV")
python3 -m scripts.environment.java_exec backend/gradlew -p backend \
  :platform:adapters:lexiconImport "-PlexiconImportArgs=$LEXICON_ARGS"
```

再检查基础词选择与预热候选；它们是只读报告，不会发布资料：

```bash
LEXICON_ARGS=$(printf 'basic-report\037--input\037%s' "$STARDICT_CSV")
python3 -m scripts.environment.java_exec backend/gradlew -p backend \
  :platform:adapters:lexiconImport "-PlexiconImportArgs=$LEXICON_ARGS"
LEXICON_ARGS=$(printf 'prewarm-report\037--input\037%s\037--limit\0372000' "$STARDICT_CSV")
python3 -m scripts.environment.java_exec backend/gradlew -p backend \
  :platform:adapters:lexiconImport "-PlexiconImportArgs=$LEXICON_ARGS"
```

`validate` 流式扫描原始 CSV，执行与发布一致的领域投影和跨行 canonical 表面校验，输出 `entries`、归并的派生词数和跳过原因；它不连接数据库，不保留原始行或完整领域词条；为检出跨行重复，保留随唯一 lemma/alias 数量增长的 canonical 表面索引。`basic-report` 输出基础词实际数量、名单及摘要、重复表达清洗数量，不连接数据库。`prewarm-report` 只保持 `--limit` 个候选，用于人工检查频率、复杂词表与 Oxford 排除。

## 1.3. 准备数据库并发布

确认上述输出后，才进入有副作用的数据库准备与发布：

以下初始化命令只接受空 schema，重复执行会拒绝，不会自动清库。结构变化时先核对本项目数据库及使用它的 API/worker，协调停用后显式清空并重建；不得操作其他项目库。完整导入发布后再启动应用。重建需同步失效对应缓存，并避免资料身份与本机偏好旧引用混淆；本命令块不自动完成重建或缓存处置。

```bash
podman compose -f infra/local/compose.yaml up -d
JDBC_URL='jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres&reWriteBatchedInserts=true'
INIT_ARGS=$(printf '%s\037%s' "$JDBC_URL" "$PWD/infra/postgres/schema.sql")
python3 -m scripts.environment.java_exec backend/gradlew -p backend \
  :platform:adapters:postgresInit "-PpostgresInitArgs=$INIT_ARGS"
```

初始化成功且确认数据库归属后再发布，命令会写入本项目开发库：

```bash
LEXICON_ARGS=$(printf 'publish\037--input\037%s\037--database-url\037%s\037--batch-source-id\037ecdict-stardict\037--batch-license-id\037MIT' "$STARDICT_CSV" "$JDBC_URL")
python3 -m scripts.environment.java_exec backend/gradlew -p backend \
  :platform:adapters:lexiconImport "-PlexiconImportArgs=$LEXICON_ARGS"
```

`reWriteBatchedInserts=true` 让 PostgreSQL JDBC 将批写合并传输，不改变 500 条事务与失败回滚边界。

## 1.4. 中断恢复与完成边界

发布以 500 条为一个已提交的 `STAGED` 分块，词条、义项、词形与来源证据按 JDBC batch 写入；任意已发布版本在整个扫描期间继续可查询。中断后以相同
输入摘要、`--batch-source-id` 和 `--batch-license-id` 重跑同一命令，导入从最后已提交的来源行继续，新的 canonical 表面会与该批次已提交记录复核。自然屈折形可返回多个 lemma，别名冲突仍拒绝；只有
完整扫描完成才会切换为 `PUBLISHED`。若来源文件或参数改变，则使用新的发布批次，不复用旧的 `STAGED` 批次。
