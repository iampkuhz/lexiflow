# 1. 共享词库合同

`Lexicon` 保存可跨内容复用的英文词汇事实；它不保存某次字幕采用哪个义项、提示是否展示、
用户行为或供应商载荷。`Enrichment` 只能通过公开查询合同读取已发布的版本。逻辑聚合身份和
持久化层级和迁移规则见[词库持久化与迁移合同](data-contracts.md)。

## 1.1. 词条、lemma、短语与义项

一个 `LexiconEntry` 是一个语言内可复用的单词或固定短语根，具有不可猜测的
`lexicon_entry_id` 和单调递增的 `lexicon_version`。每个版本保存：

| 字段 | 规则 |
| --- | --- |
| `language_tag` | BCP 47 语言标签；首版只接收 `en`。 |
| `entry_kind` | `word` 或 `phrase`；`phrase` 必须由两个或以上 token 组成。 |
| `lemma` | 词条的规范形，按 `language_tag` 归一。 |
| `normalized_key` | `language_tag`、`entry_kind` 与规范 lemma 的稳定组合；同一词库版本只能属于一个 entry。 |
| `Sense` | 从属的一个或多个义项；每个具有稳定 `sense_id`、简短中文表达、定义、词库版本与来源引用。 |

不同义项可以属于同一个词条，但 alias 或 inflection 不能独立成为义项；语境选择始终由
Enrichment 的 Annotation 决策记录。一个 Annotation 必须引用精确的
`lexicon_entry_id`、`lexicon_version` 与 `sense_id`，不能以“最新词条”替代历史事实。

## 1.2. alias、词形与歧义

`Alias` 表示同一 entry 的可检索拼写或固定别名；`Inflection` 表示可还原到同一 lemma 的
屈折形。两者都保存原文、`normalized_form`、语言和所属 entry/version。

1. 同一语言和词库版本的 lemma/alias canonical 表面只能指向一个 entry；自然屈折形允许对应多个 lemma，候选查询保留全部引用，不由导入器猜测义项。
2. 短语与单词可共享 token，但候选查询按最长匹配排序；重叠候选必须保留到 Enrichment
   决策，Lexicon 不猜测当前字幕义项。
3. 大小写、Unicode 规范形和连续空白的差异仅在归一化时消除；标点不能静默拼接相邻 token。
4. 不能归一、重复 lemma 或 canonical 表面冲突时，导入批次必须拒绝发布，不得覆盖已发布版本；自然屈折形歧义不作为冲突。

## 1.3. 来源、许可与发布

每个 entry/version 具有不可变 `Provenance`：来源标识、许可标识、取得日期、可复核内容
摘要和导入批次。来源原文、许可证全文和导入工具日志属于受控构件或运行证据，不复制进
Annotation、缓存键或观测字段。

词库更新以新 `lexicon_version` 发布；发布后 entry、sense、alias、inflection 和 provenance
不能原地改写。更正、撤回或替换创建新版本并记录被替换版本引用。旧版本仍可被历史
Annotation 查询，直至其生命周期规则允许清除；新版本不得追写旧 Annotation。

## 1.4. 频率、复杂词表与预热优先级

每个已发布 entry/version 记录可审计的 `frequency_zipf`、`complex_list_count` 和由固定公式计算的
`memory_priority`。它们只来自导入材料中的语料频率和复杂学习词表成员资格，不读取或推断任何用户
观看、点击、熟悉度或字幕数据。优先级在中等 Zipf 频段最高，并随复杂词表覆盖增加；过于基础和过于
罕见的词不进入优先预热集合。

API 仅将同一已发布版本中排名靠前的有界集合预热到本机 L1；版本切换即清空该副本。L1 未命中从
PostgreSQL 查询，再以相同 `lexicon_version` 缓存。缓存和 Redis 都可重建，不能成为词库事实来源。
导入格式、公式、来源审阅和操作命令见
[离线词库导入与预热](../development/toolchain-reproduction/lexicon-import.md)。

## 1.5. 公开查询边界

公开查询输入是受长度限制的、已规范化英文 token/短语；返回值按版本和最长匹配稳定排序。
查询合同不暴露表名、导入批次内部字段、供应商数据、来源 DOM 或任一用户标识。查询未命中
只表示没有共享词汇候选；它不能创建 pending 工作，也不能要求客户端等待。
