# 1. 二期功能架构与执行主线

上级：[二期设计](design.md)。图表达已确定的职责和交接，不表示实现已完成；执行证据见[状态页](status.md)。

## 1.1. 功能分层

图中箭头表示运行调用与资料交接，不是直接编译依赖；应用通过公开 port 访问平台实现。Lexicon 拥有资料及发布身份，Enrichment 拥有字幕候选和提示决策。macOS 和 Docker 运行同一组合根，不增加产品 worker。

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName "PingFang SC"
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
top to bottom direction
title 二期功能分层与主线
package "接入与组合根" #D6EAF8 {
  component "扩展采集与展示" as extension
  component "唯一 API 装配" as api
  component "导入命令入口" as import_cli
}
package "应用编排" #D5F5E3 {
  component "Enrichment 编排" as enrichment
  component "Lexicon 查询" as query
  component "Lexicon 发布" as publish
}
package "领域规则" #E8DAEF {
  component "定位与提示决策" as select
  component "分类与首义准备" as prepare
}
package "基础设施" #FCF3CF {
  component "PostgreSQL 适配" as repository
  database "已发布词库" as postgres
  component "授权本机记录" as analysis
}
extension --> api : S1 当前字幕请求
api --> enrichment : S2 处理新增区间
enrichment --> query : S3 准确词形合同
enrichment --> select : S4 消费发布候选
import_cli --> publish : S5\n显式导入
publish --> prepare : S6 确定性预处理
query --> repository : S7 只读端口
publish --> repository : S8 发布端口
repository --> postgres : S9 查询或原子发布
api --> analysis : S10 授权同步记录
legend bottom
箭头为调用与资料交接；应用经端口访问适配器。
观看只读已发布资料，不调用模型。
endlegend
@enduml
```

## 1.2. 当前字幕关键路径

英文在 M2 即显示，后端 M3–R5 只决定中文提示。M7 表示版本查询与缓存缺失时的批量读取，不表示每个键均访问数据库。图展开成功主线；非法请求、资料缺失或依赖故障走统一终态，禁止用未经校验的提示补位。M9 仅在本机显式授权时执行，写入失败不改变已确定提示；M11 拒绝迟到、跨身份和被本机抑制的结果。

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName "PingFang SC"
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
hide footbox
title 当前字幕关键路径
participant "字幕来源" as source
participant "扩展" as extension
participant "API" as api
participant "Enrichment" as enrichment
participant "Lexicon 查询" as lexicon
database "PostgreSQL" as postgres
participant "授权分析记录" as analysis
source -> extension : M1 可见字幕变化
extension -> extension : M2 立即显示完整英文
extension -> api : M3 冻结新增快照
api -> enrichment : M4 校验请求并处理
enrichment -> enrichment : M5 连续区间与查询键
enrichment -> lexicon : M6 准确词形集合
lexicon -> postgres : M7 发布身份与缺失键
postgres --> lexicon : R1 完整版本化候选
lexicon --> enrichment : R2 保留阻断与歧义证据
enrichment -> enrichment : M8 定位、冲突与非重叠
enrichment --> api : R3 处理覆盖与提示
api -> analysis : M9 仅授权时同步写入
analysis --> api : R4 写入结果独立于提示
api -> api : M10 一次请求终态事件
api --> extension : R5 响应与分段计时
extension -> extension : M11 校验身份与本机抑制
@enduml
```

## 1.3. 离线生产与后续观看

维护者在观看之外显式启动资料生产。M6 成功提交后新身份才可见；校验或写入失败回滚，原完整发布继续可用。观看仅观察已发布身份并重建缓存，不触发导入或模型。Docker 资料包来自这条经校验的发布链，不从用户字幕临时生成。

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName "PingFang SC"
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
hide footbox
title 资料生产与后续观看交接
actor "维护者" as operator
participant "来源适配" as importer
participant "Lexicon 发布" as publisher
participant "预处理规则" as preparation
database "PostgreSQL" as postgres
participant "观看查询" as query
operator -> importer : M1 显式导入已核对来源
importer -> publisher : M2 校验来源身份与摘要
publisher -> preparation : M3 分类、短语与默认首义
preparation --> publisher : R1 动作、原因与预热资格
publisher -> postgres : M4 事务写入完整资料
publisher -> publisher : M5 复核来源与完整性
publisher -> postgres : M6 提交发布或失败回滚
postgres --> publisher : R2 发布身份与结果
publisher --> operator : R3 导入终态和原因统计
query -> postgres : M7 后续请求读取发布身份
postgres --> query : R4 已发布版本
query -> query : M8 版本变化清缓存再装载
@enduml
```
