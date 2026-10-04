<a id="1-词库持久化模型"></a>

# 1. 词库持久化模型与初始化合同

[架构总览](overview.md) → [共享词库合同](lexicon-contract.md) → 持久化模型。本文说明发布表结构与原子初始化边界。

PostgreSQL 不复制 CSV：只存最终短释、实际排序信息和准确词形映射。原始文件按摘要受控保留；来源标识、许可和摘要每个数据集保存一次。规则命中、排除原因和来源原始字段不逐词入库，也不转存逐行 JSON 或审计表。

## 1.1. 三张表的职责与关系

| 表 | 内容 | 关联与读取 |
| --- | --- | --- |
| `lexicon_dataset` | 全库一行版本、来源清单、计数、准备规则和时间 | 完整发布事务最后写入；版本变化使缓存失效 |
| `lexicon_entry` | 七列：短 ID、lemma、短释、排名单词标志、提示分数、复杂词表计数、缓存分数 | `entry_id` 主键，`lemma` 唯一；每个词条只存一份结果 |
| `lexicon_form` | 两列：准确词形和词条 ID | PK `(normalized_form, entry_id)`，FK 指向 entry；一次批量关联查询 |

一个词条有多个形式，一个形式也可能对应多个词条。查询必须取齐候选，包括不可展示词条，不能任取第一条。准确词形不重复保存释义或评分。字段、索引及数据库注释以 [schema.sql](../../infra/postgres/schema.sql) 为准；以下说明不是本机数据查询结果。

三表共 17 列，除 `lexicon_entry.gloss` 外均不可为 NULL。只有 `dataset_id` 与 `imported_at` 有数据库默认值，其余字段须由写入方提供。下表区分数据库约束与导入、发布时保证的业务语义。

### 1.1.1. `lexicon_dataset`：全库一行元数据

| 字段 | SQL 类型 | 数据库约束与默认值 | 用途 |
| --- | --- | --- | --- |
| `dataset_id` | `SMALLINT` | 主键；必须等于 `1`；默认 `1` | 全库单例元数据身份 |
| `lexicon_version` | `BIGINT` | 大于 `0` | 完整重导时递增的已发布资料版本，驱动缓存失效 |
| `source_manifest` | `JSONB` | 必须为非空 JSON 数组 | 来源标识、许可、文件摘要和取得时间，只存一次 |
| `source_row_count` | `BIGINT` | 大于 `0`，且不小于 `entry_count` | 完整来源预扫描的输入行数 |
| `entry_count` | `BIGINT` | 大于 `0` | 窗口裁剪后实际持久化词条数，不等于全部预处理行数 |
| `lookup_count` | `BIGINT` | 不小于 `entry_count` | 窗口裁剪后实际持久化词形映射数 |
| `preparation_policy` | `TEXT` | 至少包含一个非空白字符 | 冻结的准备规则标识 |
| `imported_at` | `TIMESTAMPTZ` | 默认 `CURRENT_TIMESTAMP` | 完整写入事务提交前记录的导入时间，不是提交时间 |

发布逻辑另外保证版本处于安全整数范围、计数与实际存储行数一致，且每个存储词条至少有一个可查询形式；这些不是上述 SQL CHECK 的完整保证。

### 1.1.2. `lexicon_entry`：每个词条七列

| 字段 | SQL 类型 | 数据库约束 | 用途 |
| --- | --- | --- | --- |
| `entry_id` | `BIGINT` | 主键；大于 `0` | 英文规范 lemma 派生的正 63 位稳定身份，碰撞使发布失败 |
| `lemma` | `TEXT` | 唯一；至少包含一个非空白字符 | 规范主词形，单词或完整短语 |
| `gloss` | `TEXT` | 可为 NULL；非 NULL 时至少包含一个非空白字符 | 唯一安全默认中文短释；NULL 为 BLOCK，非 NULL 为 HINT |
| `ranked_word` | `BOOLEAN` | 不可为 NULL | 主词条为单词且来源 Zipf 大于零的冻结结果，不是精确频率值或来源证据状态 |
| `hint_priority` | `SMALLINT` | `0`～`1000` | 非个人化提示排序分数 |
| `complex_list_count` | `SMALLINT` | 不小于 `0` | 复杂词表证据数量，同分时的独立排序依据 |
| `cache_priority` | `SMALLINT` | `0`～`1000` | 独立缓存预热分数，零表示不预热 |

主键和 `lemma` 唯一约束分别建立唯一索引；另有两个预热部分索引：

| 索引 | 索引列 | 条件 |
| --- | --- | --- |
| `lexicon_entry_prewarm_hint_idx` | `(cache_priority DESC, entry_id)` | `gloss IS NOT NULL AND cache_priority > 0` |
| `lexicon_entry_prewarm_block_idx` | `(cache_priority DESC, entry_id)` | `gloss IS NULL AND cache_priority > 0` |

### 1.1.3. `lexicon_form`：每个查询映射两列

| 字段 | SQL 类型 | 数据库约束 | 用途 |
| --- | --- | --- | --- |
| `normalized_form` | `TEXT` | 至少包含一个非空白字符；按空白拆分为 `1`～`3` 个词；仅一个词时字母数至少为 `3` | 规范准确词形：原形、别名或屈折形 |
| `entry_id` | `BIGINT` | 外键引用 `lexicon_entry(entry_id)`，`ON DELETE CASCADE` | 对应词条的稳定身份；同形允许关联多个词条 |

主键 `lexicon_form_pk (normalized_form, entry_id)` 支持批量准确形式查找；反向索引 `lexicon_form_entry_idx (entry_id)` 支持预热关联。窗口 CHECK 使用 `btrim` 后按 `[[:space:]]+` 拆词，单词字母数通过删除 `[^[:alpha:]]` 后计数；它不替代导入时的规范化。词形窗口外的数据不落库，完整短语不因包含短词被删。同形 BLOCK 候选也返回以保留歧义判断。

## 1.2. 最终结果与短身份

`gloss` 为已校验默认中文短释；NULL 表示已完成处理但不可提示的 BLOCK，非空表示 HINT，不用 NULL 表示待处理。BLOCK 仍可能参与同形歧义和负向预热，不能全部删除。单独不足三个字母或超过查询窗口的形式不持久化；没有任何可查询形式的词条不持久化。完整短语独立判断，主词形不可查询但有合格别名的词条仍保留。

`ranked_word` 冻结“主词条是单词且 Zipf 大于零”，不等于频率证据 KNOWN。`hint_priority` 和 `complex_list_count` 保留现有排序，`cache_priority` 独立决定预热。精确 Zipf、原始排名和复杂标签只在导入计算中使用；需要诊断时从受控来源文件重放准备规则。不能从提示分数或缓存分数为零推断是否可展示。

`entry_id` 是规范 `entry:en:<lemma>` 的 SHA-256 前八字节大端值取正 63 位，零映射为 1。数据库存正 BIGINT（8 字节），Java 用 long，HTTP/扩展用范围 `1`～`9223372036854775807` 的十进制字符串，禁止转为 JavaScript Number。主键碰撞拒绝整批发布并回滚，不随机换 ID、不合并词条。`senseId` 在候选组装时按发布版本和 lemma 确定性派生，BLOCK 无义项，不另外存一列；Annotation 仍保留 entry、sense、version 三种身份。

当前语言固定 `en`，不在每行重复存储。单词／短语类型由规范 lemma 派生，动作由 gloss 是否为空派生，不在观看时重新清洗或选义。

## 1.3. 前置校验与原子发布

先完整预扫描、归并来源、冻结基础名单并验证 canonical 冲突，再裁剪不可查询形式。通过后重读同一文件，在**单个 PostgreSQL 事务**内固定分块写入词条和映射，提交前复核来源摘要、扫描计数与实际存储计数，最后写入元数据。失败全部回滚；不持久化 STAGED/PUBLISHED 状态。

查询将缓存未命中的形式合并为一次 SQL，通过主键关联 entry，不做逐词 N+1。正负预热独立有界，选中一个形式后取齐其全部词条。不以减少字段为由改变歧义、排序或发布资格；关联开销需要隔离验证，不假定一定比宽表更快。

## 1.4. 边界与初始化

`LexiconRepository` 是 application 的持久化端口，Enrichment 只消费发布候选，不直接读表或原始来源。L1、Redis 和浏览器状态均可重建。

唯一最新 SQL 在 [schema.sql](../../infra/postgres/schema.sql)。结构不匹配时停止，显式确认本项目目标后重建并完整重导；API 启动与普通 upgrade 不隐式清库。新发布身份不得与不同内容复用，重建不能简单把版本归零后重用旧缓存。旧结构数据包拒绝装载，不保留双结构兼容。
