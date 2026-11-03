# 1. 离线词库校验、初始化与重建

> 位置：[仓库生命周期](../README.md) → 离线词库导入。本流程准备本机已发布资料，不是每次代码交付的必经步骤。

首批完整来源为本机 `stardict.csv`，不叠加 `ecdict.csv`。来源文件不进入仓库，也不使用用户字幕、观看历史或熟悉度。API 只读消费已发布资料；来源校验、数据库初始化及重建均由离线命令完成。

## 1.1. 校验来源

准备合法取得的 StarDict CSV，确认文件摘要与首行：

```bash
export STARDICT_CSV='/absolute/path/stardict.csv'
shasum -a 256 "$STARDICT_CSV"
head -n 1 "$STARDICT_CSV"
python3 -m scripts.local.run_with_java_toolchain backend/gradlew -p backend lexiconValidate
python3 -m scripts.local.run_with_java_toolchain backend/gradlew -p backend lexiconBasicReport
python3 -m scripts.local.run_with_java_toolchain backend/gradlew -p backend lexiconPrewarmReport
```

上述校验和报告不连接数据库，也不代表资料已发布。不要将来源文件或报告中的私有数据提交仓库。

## 1.2. 初始化或完整重建

数据库是专属文件 `/var/lib/lexiflow/lexicon.db`；开发环境可使用仓库配置的独立持久数据目录。只有离线重建/初始化命令可以写入。数据库动作必须从完整来源重新导入，不支持旧结构原地升级或迁移；结构变化须先备份并确认目标，再完整重建。API 不建库、不建表、不清库。

使用项目提供的离线入口完成准备：

```bash
python3 -m scripts.local.run_with_java_toolchain backend/gradlew -p backend lexiconRebuild
```

命令先完成来源预检、冻结目标与输入、执行事务性重建和完整导入、校验发布资料，再写入成功元数据。失败回滚；目标、来源或配置在冻结后漂移时阻断。确认只能授权本次显示的精确目标，不得将确认文本写入自动化管道。重建会产生新的资料身份；不得将旧缓存或本机偏好强行套用于不同内容。

发行资料包初始化通过 API JAR 管理入口执行，目标路径与 JDBC URL 必须使用同一绝对数据库文件：

```bash
LEXIFLOW_RELEASE_JDBC_URL='jdbc:sqlite:/absolute/path/to/lexicon.db' \
  java -jar backend/product/api/build/libs/api-<version>.jar \
  --release-dataset initialize --package /absolute/path/dataset.zip \
  --expected-sha256 <sha256> --expected-file /absolute/path/to/lexicon.db
```

部署数据使用 `--deployment-data inspect|apply --request <file> --expected-file <absolute DB>`，环境变量为 `LEXIFLOW_DEPLOYMENT_JDBC_URL`；响应以 `databaseFile` 标识目标。发行 CLI 使用 `LEXIFLOW_RELEASE_JDBC_URL`。两种管理命令都要求摘要/请求与绝对目标文件精确匹配，不能指向真实旧运行卷或未确认的文件。

## 1.3. 启动 API 与失败诊断

启动前确认数据库文件存在、可读且包含有效发布资料。API 使用只读 JDBC `mode=ro`，缺少数据库即拒绝，不隐式创建空库。SQLite 启用外键、STRICT 表、`DELETE` journal、`synchronous=FULL` 与 5000ms busy timeout；锁等待有界，失败不无限重试。安装升级前先停止 API，再执行离线资料操作。

数据库文件损坏、schema 不匹配、摘要错误或无已发布资料时停止服务并保留现场；不要通过复制运行中的主文件、手工建表、清库或重试 API 启动修复。按[持久化模型](../system/lexicon/data-model.md)核对当前合同，并由离线流程从完整来源重新构建。

## 1.4. 发布语义

完整来源预扫描、canonical 冲突验证及内容计数均在写入前完成；导入在单个 SQLite 事务中进行，来源摘要、计数、稳定身份与发布记录在提交前再次核验。业务查询通过逻辑 `lexicon_lookup` 视图获得 canonical 与非 canonical 的完整形式集合，保留同形歧义和 BLOCK 候选。唯一最新物理 SQL 为 `infra/sqlite/schema.sql`。
