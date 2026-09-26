# 1. 离线词库导入：校验与原子发布

> 位置：[工程地图](../overview.md) → [Operations](../operations.md) → 离线词库导入。此流程为本机体验准备已发布资料，不是每次代码交付的必经步骤。

首批完整来源为本机 `stardict.csv`，不叠加 `ecdict.csv`。来源文件不进入仓库，也不使用用户字幕、观看历史或熟悉度。发布完成后回到[本机体验](local-experience.md)启动 API；观看只消费已发布版本。

## 1.1. 先看完整链路

| 阶段 | 做什么 | 输出与失败边界 |
| --- | --- | --- |
| I1 准备来源 | 核对路径、首行与摘要 | 来源不明确先停，不猜许可证或自动转换大文件 |
| I2 离线检查 | validate → basic-report / prewarm-report | 不连接数据库；报告帮助人工确认，不代表已发布 |
| I3 初始化与发布 | 确认开发库归属，初始化空 schema，再 publish | 前置完整扫描后，单个事务批写并切换完整资料 |
| I4 消费与重试 | API 查询发布版本；中断后重新执行发布 | 失败事务回滚，旧完整资料仍可见 |

状态和事务关系见[持久化模型](../../architecture/data-model.md)。需要核对单行转换、字段、频率公式或基础词选择时，再读[记录处理 Reference](lexicon-import/record-processing.md)，不用在操作主干展开所有字段。

## 1.2. 校验文件与检查报告

准备本机 `stardict.csv`，确认首行和摘要；**直接把该文件传给命令**，不要把 StarDict 文件先转换成
`lexiflow-lexicon-v1.csv`，也不要叠加 `ecdict.csv`：

```bash
export STARDICT_CSV='/absolute/path/stardict.csv'
shasum -a 256 "$STARDICT_CSV"
head -n 1 "$STARDICT_CSV"
```

文件路径和首行正确后，执行不连接数据库的完整校验：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconValidate
```

再检查基础词选择与预热候选；它们是只读报告，不会发布资料：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconBasicReport
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconPrewarmReport
```

`validate` 流式扫描原始 CSV，执行与发布一致的领域投影和跨行 canonical 表面校验，输出 `entries`、归并的派生词数和跳过原因；它不连接数据库，不保留原始行或完整领域词条；为检出跨行重复，保留随唯一 lemma/alias 数量增长的 canonical 表面索引。`basic-report` 输出基础词实际数量、名单及摘要、重复表达清洗数量，不连接数据库。`lexiconPrewarmReport` 使用默认 2000 个候选，用于人工检查频率、复杂词表与 Oxford 排除。

## 1.3. 准备数据库并发布

确认来源校验通过，并准备好可连接的本项目 PostgreSQL 开发库。运行前先按[根 README](../../../README.md#本地启动)完成本地编译、连接配置与来源路径设置。本节不创建或启动数据库服务。`JDBC_URL` 与 `STARDICT_CSV` 只配置一次，不再拼接参数。`lexiconPublish` 固定记录已确认来源 `ecdict-stardict` 与许可证 `MIT`，不要把其他来源冒充此来源。

已有表时先做[结构检查](#15-已有开发库结构不匹配时)，不要直接重跑初始化。

以下初始化命令只接受空 schema，重复执行会拒绝，不会自动清库。结构变化时先核对本项目数据库及使用它的 API/worker，协调停用后显式清空并重建；不得操作其他项目库。完整导入发布后再启动应用。重建需同步失效对应缓存，并避免资料身份与本机偏好旧引用混淆；本命令块不自动完成重建或缓存处置。

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend postgresInit
```

初始化成功且确认数据库归属后再发布，命令会写入本项目开发库：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconPublish
```

离线 JDBC 适配器已启用 `reWriteBatchedInserts=true`；每 500 条组成一批网络写入，但整个发布仍只有一个事务。不要另拼接数据库性能参数。

## 1.4. 中断重试与完成边界

`lexiconPublish` 先完整扫描来源并检查跨行 canonical 词形，再核对文件摘要；通过后重读来源，以 500 条为一个 JDBC batch 将准备词条和查询词形写入**同一个事务**。批写不是分块提交。提交前再次核对来源摘要与计数。中断或来源变化使事务回滚，原完整数据集继续可查；重新运行命令会从头预检和重导，不需要恢复数据库里的中间状态。自然屈折形可以指向多个 lemma；canonical 别名冲突拒绝发布。

## 1.5. 已有开发库结构不匹配时

如果查询出现 `relation "lexicon_hint_lookup" does not exist` 或投影字段缺失，先排查是否连接错库；重新编译、重启 API 或重跑 publish 不会补齐结构。`postgresInit` 只接受空 schema。

先核对启动命令的 JDBC URL。在连接到同一数据库的本地 SQL 客户端执行只读查询：

```sql
SELECT current_database(), current_user, current_schema();
SELECT table_name
FROM information_schema.tables
WHERE table_schema = current_schema()
  AND table_name IN ('lexicon_dataset', 'lexicon_prepared_entry', 'lexicon_hint_lookup')
ORDER BY table_name;
SELECT lexicon_version, entry_count, lookup_count FROM lexicon_dataset;
```

应有三张表；数据集行仅在完整发布后出现。表存在不代表字段、约束与索引均匹配。

没有完整数据集时完成 publish；结构不匹配时按以下边界处理：

1. 停止使用该库的本项目 API/worker，确认数据库及 schema 属于本项目，不操作共享库或其他项目。
2. 确认来源可完整重导；需要保留数据时先备份并核验，不能直接丢弃。
3. **只有人工确认开发数据可丢弃后**，才在数据库管理工具中清空并重建目标 schema；这会删除其对象及依赖，本页不提供自动清库命令。
4. 回到 **1.3 的 postgresInit → lexiconPublish**，完整重导后再启动 API。不要只补单列，也不要把包含不匹配表结构的备份直接恢复到新 schema。

重建会重新产生资料身份。重新启动本项目 API/worker 以清除进程内缓存；若另有缓存或本机词段抑制偏好，需按其 owner 处理旧身份引用，不自动删除用户数据。用不带旧偏好的浏览器测试 profile 验证新词库，完成后回到[本地体验](local-experience.md#12-初始化本地配置再启动确定性-api)。
