# 1. 词库持久化模型

LexiFlow 当前唯一持久化闭环是共享词库。PostgreSQL 保存已发布的词条版本和导入来源；进程内 L1、Redis 与浏览器状态均可重建，不能成为事实来源。

## 1.1. LexiconEntry 聚合

`LexiconEntry` 是跨内容复用的词汇知识根，使用 `lexicon_entry_id` 与不可变的 `lexicon_version` 标识。一个版本包含 lemma、词类、一个或多个 sense、alias、inflection、来源与优先级。Repository 返回完整聚合，禁止上层以表行或部分 sense 代替领域模型。

## 1.2. 导入批次

`lexicon_import_batch` 保存来源摘要、许可、版本、处理行数与发布状态。完整规范输入在一个事务内写入并发布；流式输入只写入不可见 `STAGED` 批次，完整扫描后才切换为 `PUBLISHED`。同一时刻最多一个版本为 `PUBLISHED`。

## 1.3. 持久化边界

领域和 application 只使用 Model 与 `LexiconRepository`。DO、DAO、SQL、`JdbcClient` 与 PostgreSQL 事务只存在于 `:platform:adapters` 的 persistence 包。未形成闭环的内容、标注、语义结果和异步工作不保留表或持久化接口。
