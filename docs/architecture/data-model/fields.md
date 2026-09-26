# 1. 三表字段、赋值与索引 Reference

**位置：** [持久化模型](../data-model.md) → 字段 Reference。下表以说明性 `reliable` 词条为例；示例值不是当前本机库的查询结果。实际类型与中文数据库注释以[最新 SQL](../../../infra/postgres/schema.sql)为准。

## 1.1. `lexicon_dataset`：一次完整资料的元数据

| 字段 | SQL 类型 | 中文说明 | 示例值 | 赋值时间与方式 | 键／关联 |
| --- | --- | --- | --- | --- | --- |
| `dataset_id` | `SMALLINT` | 固定单例身份 | `1` | 完整发布事务最后写入 | PK，CHECK 等于 1 |
| `lexicon_version` | `BIGINT` | 完整资料版本，切换时使缓存和本机抑制偏好失效 | `1` | 发布事务锁定后从旧版本加一 | 正整数；结果版本来源 |
| `source_manifest` | `JSONB` | 来源标识、许可证、文件摘要和取得时间的清单 | `[ {"source_id":"ecdict-stardict","license_id":"MIT",…} ]` | 前置核对来源后随完整发布写入 | 当前单来源，结构允许清单 |
| `source_row_count` | `BIGINT` | 原始来源行数，含跳过的派生形 | `3402564` | 前置完整扫描计数，重读时复核 | 大于零 |
| `entry_count` | `BIGINT` | 已准备主词条数 | `2992863` | 批写计数与前置计数一致后写入 | 大于零 |
| `lookup_count` | `BIGINT` | 原形、别名和屈折形的查询行数 | `3600000` | 批写查询行时累计 | 不小于 `entry_count` |
| `preparation_policy` | `TEXT` | 本次基础词与释义准备规则标识 | `oxford-ranked-top2000-…` | 离线来源合同冻结后写入 | 非空 |
| `imported_at` | `TIMESTAMPTZ` | 完整发布记录时间 | `2026-09-26T09:00:00Z` | 数据库在发布事务中赋默认值 | 非空 |

此表不存 `STAGED`、`PUBLISHED` 或其他工作流状态。提交前旧完整资料仍可见，失败时新行与批写一并回滚。

## 1.2. `lexicon_prepared_entry`：一个规范主词条的准备结果

| 字段 | SQL 类型 | 中文说明 | 示例值 | 赋值时间与方式 | 键／关联 |
| --- | --- | --- | --- | --- | --- |
| `lexicon_entry_id` | `UUID` | 由语言和 lemma 确定的来源无关身份 | `50f1a5c1-f3f8-3d5d-99fc-9d0cd8314d75` | 前置规范化后确定性生成 | PK；供 lookup FK 引用 |
| `language_tag` | `TEXT` | 语言标签 | `en` | 来源规范化时固定 | 与 `lemma` 组成 UK |
| `lemma` | `TEXT` | 规范主词形 | `reliable` | 前置解析时 NFC、小写、空白折叠 | UK `(language_tag, lemma)` |
| `entry_kind` | `TEXT` | 单词或固定短语 | `word` | 按 lemma token 数判定 | CHECK `word/phrase` |
| `source_gloss` | `TEXT` | 被采用来源的原始中文释义，不汇集其他来源 | `可靠的` | 来源翻译字段清洗时保留 | 不建观看索引 |
| `source_gloss_ref` | `TEXT` | 该释义在来源文件的记录定位 | `stardict.csv#123:reliable` | 解析来源行时形成 | 配合文件摘要离线回查 |
| `source_bnc_rank` | `BIGINT` | 有效 BNC 正排名 | `3271` | 导入时解析；无效或缺失为 NULL | 不单独建索引 |
| `source_frq_rank` | `BIGINT` | 有效 FRQ 正排名 | `3504` | 同上 | 不单独建索引 |
| `source_complex_tags` | `TEXT[]` | 去重的复杂词表标签 | `{cet6,ky,toefl,ielts}` | 来源 tag 过滤并排序 | 不单独建索引 |
| `source_oxford_basic` | `BOOLEAN` | Oxford 原始标记，不等于最终阻断 | `false` | 来源字段为 `1` 时置真 | 不单独建索引 |
| `prepared_gloss` | `TEXT` | 经验证可展示的单一中文短释 | `可靠的` | 基础词、短语与释义安全规则完成后写入 | HINT 时非空 |
| `exclusion_reason` | `TEXT` | 无可靠短释时的固定排除原因 | `NULL` | 同一次准备决定；与短释互斥 | BLOCK 时非空 |
| `prepared_priority` | `INTEGER` | 词频与复杂标签形成的 0～1000 分数 | `930` | 导入时按固定公式计算 | 投影为 `final_priority` |
| `frequency_zipf` | `NUMERIC(3,2)` | 有效排名换算的 Zipf 值 | `4.49` | 取有效排名较小者再计算 | 投影到查询表 |
| `complex_list_count` | `SMALLINT` | 去重复杂标签数量 | `4` | 解析来源标签时计算 | 投影到查询表 |

`prepared_gloss` 与 `exclusion_reason` 必须恰有一个非空。数据库不对每个来源逐条保存原始释义；多来源归并规则属于后续离线准备能力，不让观看现场选来源。

## 1.3. `lexicon_hint_lookup`：观看唯一反查表

| 字段 | SQL 类型 | 中文说明 | 示例值 | 赋值时间与方式 | 键／关联 |
| --- | --- | --- | --- | --- | --- |
| `language_tag` | `TEXT` | 准确词形语言 | `en` | 从准备词条投影 | PK 第 1 列 |
| `normalized_form` | `TEXT` | 字幕直接反查形式 | `reliable` | 原形、别名、屈折形各生成一行 | PK 第 2 列；直接索引查找 |
| `lexicon_entry_id` | `UUID` | 该词形所指向的规范词条 | `50f1a5c1-f3f8-3d5d-99fc-9d0cd8314d75` | 从准备词条复制 | PK 第 3 列；FK 指向准备表 |
| `form_kind` | `TEXT` | 原形、别名或屈折形 | `lemma` | 构造词形时确定 | `lemma/alias/inflection` |
| `canonical_lemma` | `TEXT` | 所属规范主词形 | `reliable` | 从准备词条复制 | 不需观看再联表 |
| `entry_kind` | `TEXT` | 所属词条类别 | `word` | 从准备词条复制 | 决定匹配优先级 |
| `final_action` | `TEXT` | 冻结为提示或阻断 | `HINT` | 从准备短释与排除原因派生 | 预热索引首列 |
| `final_gloss` | `TEXT` | 可靠中文短释 | `可靠的` | 仅 HINT 复制准备短释；BLOCK 为 NULL | 与动作 CHECK 一致 |
| `final_priority` | `INTEGER` | 非个人化提示排序分数 | `930` | 从准备优先级投影 | Enrichment 使用 |
| `final_sense_id` | `UUID` | 当前资料的义项身份 | `5dab620d-a268-33eb-b85b-31fb70f5b527` | HINT 时按版本及 lemma 确定；BLOCK 为 NULL | Annotation 精确引用 |
| `final_frequency_zipf` | `NUMERIC(3,2)` | 提示排序所需频率 | `4.49` | 从准备词条投影 | 不回查准备表 |
| `final_complex_list_count` | `SMALLINT` | 提示排序所需复杂标签数量 | `4` | 从准备词条投影 | 不回查准备表 |
| `cache_priority` | `INTEGER` | 独立的正负缓存预热分数 | `930` | 基础 BLOCK 设高值，其余按预热资格与优先级生成 | 部分索引 `(language_tag, final_action, cache_priority DESC, normalized_form)` |

PK 允许同一个 `normalized_form` 关联多个不同词条；查询必须返回全部行。准确查词依靠 PK 左前缀 `(language_tag, normalized_form)`，不需要同时查准备表。正负预热只访问 `cache_priority > 0` 的部分索引，并在选中一个词形后取齐该词形的全部歧义行。
