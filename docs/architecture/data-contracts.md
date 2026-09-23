# 1. 词库持久化与初始化合同

## 1.1. Repository 与 DAO

应用服务只依赖 `LexiconRepository`。Repository 组合词条、导入批次和来源证据 DAO；DAO 只处理 PostgreSQL 表访问；DO 仅在 persistence 包内出现。Mapper 将 DO 还原为完整领域 Model，确保多个 sense、alias 和 inflection 不丢失。

## 1.2. 事务与版本

规范导入在同一 PostgreSQL 事务内锁定发布、分配版本、写入批次/词条/证据、替换旧发布版本并发布新版本。流式导入以连续分块更新 `source_rows_processed`，只有处理行数等于完整来源行数时才能发布。

## 1.3. 结构初始化

数据库只维护唯一最新 SQL（`infra/postgres/schema.sql`），直接声明当前需要的表和索引。结构变化时显式重建开发库并完整重新导入，不维护递增迁移链或台账。初始化测试验证空库初始化、非空拒绝、失败原子回滚后可按修正 SQL 重试。
