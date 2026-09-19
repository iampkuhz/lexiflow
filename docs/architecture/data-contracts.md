# 1. 词库持久化与迁移合同

## 1.1. Repository 与 DAO

应用服务只依赖 `LexiconRepository`。Repository 组合词条、导入批次和来源证据 DAO；DAO 只处理 PostgreSQL 表访问；DO 仅在 persistence 包内出现。Mapper 将 DO 还原为完整领域 Model，确保多个 sense、alias 和 inflection 不丢失。

## 1.2. 事务与版本

规范导入在同一 PostgreSQL 事务内锁定发布、分配版本、写入批次/词条/证据、替换旧发布版本并发布新版本。流式导入以连续分块更新 `source_rows_processed`，只有处理行数等于完整来源行数时才能发布。

## 1.3. 迁移

数据库变化只通过顺序、哈希锁定的 SQL 迁移发布。已发布迁移不可改写；未闭环表由新的前向迁移删除。迁移测试验证新库升级后只保留词库表和对应查询索引。
