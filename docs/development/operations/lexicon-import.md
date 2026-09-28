# 1. 离线词库导入：校验与原子发布

> 位置：[工程地图](../overview.md) → [Operations](../operations.md) → 离线词库导入。此流程为本机体验准备已发布资料，不是每次代码交付的必经步骤。

首批完整来源为本机 `stardict.csv`，不叠加 `ecdict.csv`。来源文件不进入仓库，也不使用用户字幕、观看历史或熟悉度。发布完成后回到[本机体验](local-experience.md)启动 API；观看只消费已发布版本。

## 1.1. 先看完整链路

| 阶段 | 做什么 | 输出与失败边界 |
| --- | --- | --- |
| I1 准备来源 | 核对路径、首行与摘要 | 来源不明确先停，不猜许可证或自动转换大文件 |
| I2 离线检查 | validate → basic-report / prewarm-report | 不连接数据库；报告帮助人工确认，不代表已发布 |
| I3 初始化与发布 | 确认开发库归属，必要时重建本项目表，再 publish | 来源先完整校验；重建和发布分别事务化，发布失败须重新执行 |
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

确认来源校验通过，并准备好可连接的本项目 PostgreSQL 开发库。运行前先按[根 README](../../../README.md#本地启动)完成连接配置与来源路径设置。本节不创建或启动数据库服务。`JDBC_URL` 与 `STARDICT_CSV` 在本机 `.local/lexiflow/runtime.json` 保存一次，不再拼接参数或在新终端重复填写。显式环境变量可以覆盖；只读校验、报告、发布及重建各有独立 Gradle 任务，Python 仅选择 JDK 并保留终端输入。`lexiconPublish` 固定记录已确认来源 `ecdict-stardict` 与许可证 `MIT`，不要把其他来源冒充此来源。

已有表时先做[结构检查](#15-已有开发库结构不匹配时)。结构变化或完整重建时先核对本项目数据库及使用它的 API，协调停用并核验备份；不得操作其他项目库。完整导入发布后再启动应用。重建需同步失效对应缓存，并避免资料身份与本机偏好旧引用混淆；命令不会自动清理缓存、偏好或本机文件。

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend lexiconRebuild
```

`lexiconRebuild` 是**一个 Gradle 任务**，内部按“来源预检 → 目标检查与人工确认 → 结构重建 → 全量导入与发布”四个阶段执行。日志以 `[lexiconRebuild 阶段 n/4]` 标出阶段开始、完成与内部步骤；来源统计和发布结果属于相应阶段的内部输出，不是独立 Gradle 任务。仅实际执行时，每 3 分钟输出当前内部步骤与该阶段已用时间；这只是存活状态，不是完成百分比或成功保证。等待输入时停止心跳，只显示一次精确文本、Enter 和取消方法；请直接在原终端输入，未输入不是执行进度。默认 plain console 不显示 Gradle 动态百分比。

已有本项目词库表时，命令显示数据库、schema 和已有关系；只有执行人输入 `REBUILD <database>.<schema>` 的精确文本并按 Enter 才会继续。空目标直接创建结构并导入。输入空行或其他文本会取消，stdin 关闭（EOF）会明确报错退出；按 Ctrl+C 可取消。以上情况不会删除任何表。重建只删除三张本项目词库表，不使用 `CASCADE`；未知 `lexicon_*` 关系或外部依赖会阻断并回滚结构事务。不要把确认文本写入自动化管道。`postgresInit` 仍仅接受空 schema，`lexiconPublish` 仍可单独对已有匹配结构发布，不执行结构重建。

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

1. 停止使用该库的本项目 API，确认数据库及 schema 属于本项目，不操作共享库或其他项目。
2. 确认来源可完整重导；需要保留数据时先备份并核验，不能直接丢弃。
3. **只有人工确认开发数据可丢弃后**，才执行 **1.3 的 `lexiconRebuild`**，核对提示中的数据库和 schema，再输入精确确认文本。命令仅重建本项目三张表并完整重导，不删除来源文件、其他项目对象或整个 schema。
4. 若有未知旧关系、其他对象依赖或数据库权限不足，命令会拒绝并保留原结构；停止并在数据库管理工具中人工核对归属，不使用 `CASCADE` 绕过。成功后再启动 API。不要只补单列，也不要把包含不匹配表结构的备份直接恢复到新 schema。

重建会重新产生资料身份。重新启动本项目 API 以清除进程内缓存；若另有缓存或本机词段抑制偏好，需按其 owner 处理旧身份引用，不自动删除用户数据。用不带旧偏好的浏览器测试 profile 验证新词库，完成后回到[本地体验](local-experience.md#12-初始化本地配置再启动确定性-api)。
