<a id="1-词库持久化模型"></a>

# 1. 词库持久化模型与初始化合同

[架构总览](../../system.md) → [共享词库合同](../lexicon.md) → 持久化模型。本文说明发布表结构与原子初始化边界。

SQLite STRICT 由 Xerial JDBC `3.54.1.0` 访问，不复制 CSV：只存最终短释、统一 priority 和准确词形映射。INTEGER PRIMARY KEY 是 rowid alias，不另建主键 B-tree。正式数据库文件固定为 `/var/lib/lexiflow/lexicon.db`；原始文件按摘要受控保留，来源标识、许可和摘要每个数据集保存一次。规则命中、排除原因和来源原始字段不逐词入库，也不转存逐行 JSON 或审计表。

## 1.1. 三张物理表与一个逻辑视图

| 表 | 内容 | 关联与读取 |
| --- | --- | --- |
| `lexicon_dataset` | 全库一行版本、来源清单、计数、准备规则和生成身份 | 完整发布事务最后写入；版本变化使缓存失效 |
| `lexicon_entry` | 五列：稳定 ID、lemma、短释、canonical 窗口标志、priority | `entry_id` 主键，`lemma` 唯一；每个词条只存一份结果 |
| `lexicon_form` | 仅保存非 canonical 词形与词条 ID | PK `(normalized_form, entry_id)`，FK 指向 entry；索引支持反向查询 |
| `lexicon_lookup` | 逻辑视图，合并 canonical lemma 与非 canonical 词形 | 重建原有完整查询映射，供批量查询及预热消费 |

一个词条有多个形式，一个形式也可能对应多个词条。查询必须经 `lexicon_lookup` 取齐候选，包括不可展示词条，不能任取第一条。canonical lemma 由 entry 行的布尔标志重建，映射表不重复保存原形。准确词形不重复保存释义或评分。字段、索引及数据库注释以 [schema.sql](../../../infra/sqlite/schema.sql) 为准；以下说明不是本机数据查询结果。

元数据表保留 13 列；entry 使用五列 `entry_id`、`lemma`、`gloss`、`canonical_form`、`priority`；物理映射仅保存非 canonical 形式。除 `lexicon_entry.gloss` 外均不可为 NULL。写入方显式提供所有字段；`dataset_id=1` 由单例约束保证，不声明 SQL DEFAULT。下表区分数据库约束与导入、发布时保证的业务语义。

### 1.1.1. `lexicon_dataset`：全库一行元数据

| 字段 | SQL 类型 | 数据库约束与默认值 | 用途 |
| --- | --- | --- | --- |
| `dataset_id` | `INTEGER` | 主键；必须等于 `1` | 全库单例元数据身份 |
| `lexicon_version` | `INTEGER` | 大于 `0` | 完整重导时递增的已发布资料版本，驱动缓存失效 |
| `source_manifest` | `TEXT` | 必须为非空 JSON 数组 | 来源标识、许可、文件摘要和取得时间，只存一次 |
| `source_row_count` | `INTEGER` | 大于 `0`，且不小于 `entry_count` | 完整来源预扫描的输入行数 |
| `entry_count` | `INTEGER` | 大于 `0` | 窗口裁剪后实际持久化词条数，不等于全部预处理行数 |
| `lookup_count` | `INTEGER` | 不小于 `entry_count` | 完整逻辑词形映射数，含视图派生的 canonical 映射 |
| `preparation_policy` | `TEXT` | 至少包含一个非空白字符 | 冻结的准备规则标识 |
| `generation_version` | `TEXT` | 64 位小写 SHA-256 | 代码、来源和有效配置共同派生的数据生成版本 |
| `generation_code_version` | `TEXT` | 64 位小写 SHA-256 | 受控导入及预处理代码输入的内容身份 |
| `generation_source_digest` | `TEXT` | 64 位小写 SHA-256 | 已校验实际来源和导入输入身份 |
| `generation_config` | `TEXT` | 对象 | 本次有效的数据生成配置 |
| `generation_inputs` | `TEXT` | 对象 | 输入摘要和上游身份，供资料包恢复生成身份 |
| `content_sha256` | `TEXT` | 64 位小写 SHA-256 | 按确定顺序计算的发布词条与词形内容摘要 |

发布逻辑另外保证版本处于安全整数范围、计数与实际存储行数一致，且每个存储词条至少有一个可查询形式；这些不是上述 SQL CHECK 的完整保证。

### 1.1.2. `lexicon_entry`：每个词条五列

| 字段 | SQL 类型 | 数据库约束 | 用途 |
| --- | --- | --- | --- |
| `entry_id` | `INTEGER` | 主键；大于 `0` | 英文规范 lemma 派生的正 63 位稳定身份，碰撞使发布失败 |
| `lemma` | `TEXT` | 唯一；至少包含一个非空白字符 | 规范主词形，单词或完整短语 |
| `gloss` | `TEXT` | 可为 NULL；非 NULL 时至少包含一个非空白字符 | 唯一安全默认中文短释；NULL 为 BLOCK，非 NULL 为 HINT |
| `canonical_form` | `INTEGER` | `0` 或 `1`，不可为 NULL | lemma 是否落入词形查询窗口；构建期冻结，用于重建逻辑映射 |
| `priority` | `INTEGER` | `0`～`1000` | HINT 展示与预热共用；0 表示不预热，不表示 BLOCK |

`entry_id` 主键复用 rowid B-tree，`lemma` 唯一约束另建唯一索引；另有两个预热部分索引：

| 索引 | 索引列 | 条件 |
| --- | --- | --- |
| `lexicon_entry_prewarm_hint_idx` | `(priority DESC, entry_id)` | `gloss IS NOT NULL AND priority > 0` |
| `lexicon_entry_prewarm_block_idx` | `(priority DESC, entry_id)` | `gloss IS NULL AND priority > 0` |

### 1.1.3. `lexicon_form`：仅非 canonical 映射

`lexicon_form` 只持久化非 canonical 词形；`lexicon_lookup` 通过 `UNION ALL` 合并 `canonical_form=true` 的 lemma 与全部非 canonical 映射，恢复完整逻辑查询集合。窗口外 lemma 仍保留于 entry，但不进入 lookup。

| 字段 | SQL 类型 | 数据库约束 | 用途 |
| --- | --- | --- | --- |
| `normalized_form` | `TEXT` | 至少包含一个非空白字符；按空白拆分为 `1`～`3` 个词；仅一个词时字母数至少为 `3` | 非 canonical 规范准确词形：别名或屈折形 |
| `entry_id` | `INTEGER` | 外键引用 `lexicon_entry(entry_id)`，`ON DELETE CASCADE` | 对应词条的稳定身份；同形允许关联多个词条 |

主键与反向索引分别支持准确形式查询与 entry 查询；canonical lemma 不重复写入该表。SQL 约束保证非空文本、外键、唯一映射及不重复 canonical；规范化、查询窗口及 canonical 标志的精确一致性由共享领域策略和发布／包校验保证。词形窗口外的数据不落库，完整短语不因包含短词被删。同形 BLOCK 候选也返回以保留歧义判断。

## 1.2. 最终结果与短身份

`gloss` 为已校验默认中文短释；NULL 表示已完成处理但不可提示的 BLOCK，非空表示 HINT，不用 NULL 表示待处理。BLOCK 仍可能参与同形歧义和负向预热，不能全部删除。单独不足三个字母或超过查询窗口的形式不持久化；没有任何可查询形式的词条不持久化。完整短语独立判断，主词形不可查询但有合格别名的词条仍保留。

运行时仅有统一 `priority`，HINT 展示与预热共用 0..1000 分数，0 不预热但不表示 BLOCK。`complex_list_count` 仅可用于构建期打分，不持久化；`ranked_word` 不作为 runtime 字段。排序策略简化不承诺旧排序等价，稳定身份、有效词条、lookup、gloss 与 BLOCK 歧义仍须等价，且需记录全量新旧排序差异并验证新策略。`americanizes` → `americanize` HINT 与 `americanizes` BLOCK 同形歧义须覆盖。精确 Zipf、原始排名和复杂标签只在导入计算中使用。

`entry_id` 是规范 `entry:en:<lemma>` 的 SHA-256 前八字节大端值取正 63 位，零映射为 1。SQLite STRICT 使用 INTEGER，Java 用 long，HTTP/扩展用范围 `1`～`9223372036854775807` 的十进制字符串，禁止转为 JavaScript Number。INTEGER PRIMARY KEY 为 rowid alias，不另建主键 B-tree。主键碰撞拒绝整批发布并回滚，不随机换 ID、不合并词条。`senseId` 在候选组装时按发布版本和 lemma 确定性派生，BLOCK 无义项，不另外存一列；Annotation 仍保留 entry、sense、version 三种身份。

当前语言固定 `en`，不在每行重复存储。单词／短语类型由规范 lemma 派生，动作由 gloss 是否为空派生，不在观看时重新清洗或选义。

## 1.3. 前置校验与原子发布

先完整预扫描、归并来源、冻结基础名单并验证 canonical 冲突，再裁剪不可查询形式。通过后重读同一文件，在**单个 SQLite 事务**内固定分块写入词条和映射，提交前复核来源摘要、扫描计数与实际存储计数，最后写入来源、生成版本和内容摘要元数据。成功记录与数据共同提交，失败全部回滚；不持久化 STAGED/PUBLISHED 状态。本机安装阶段不是成功导入的事实来源。

查询将缓存未命中的形式合并为一次 SQL，通过主键关联 entry，不做逐词 N+1。正负预热独立有界，选中一个形式后取齐其全部词条。排序策略简化不承诺旧序列等价；关联开销需要隔离验证，不假定一定比宽表更快。

## 1.4. 边界与初始化

`LexiconRepository` 是 application 的持久化端口，Enrichment 只消费发布候选，不直接读表或原始来源。L1、Redis 和浏览器状态均可重建。

唯一最新 SQL 在 [schema.sql](../../../infra/sqlite/schema.sql)。数据库动作由离线管理入口完成，必须核对冻结目标与输入；API 仅以 `mode=ro` 打开 `/var/lib/lexiflow/lexicon.db`，缺库即拒绝，不隐式创建或清库。每个连接启用外键与 5000ms busy timeout；写连接使用 `synchronous=FULL`、`DELETE` journal，不使用 WAL。离线初始化／重建在事务中完成写入、校验与发布记录，失败回滚。新发布身份不得与不同内容复用，重建不能简单把版本归零后重用旧缓存。结构变化须完整重导；不维护旧结构兼容。
