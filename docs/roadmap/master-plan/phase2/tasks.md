# 1. 第二阶段任务与启动合同

本页规定二期工作清单；用户授权与执行事实见状态页。任务身份、允许范围、版本和依赖以 [Catalog](../../../../planning/workstreams.yaml) 为准；任务均以 DRAFT 建立，阶段状态只见[状态页](status.md)。目标和边界见[阶段入口](../phase2.md)。

## 1.1. 正式启动条件

1. 核对用户启动授权、最新 Harness、实际代码和并发写入者；记录 phase1 缺失验收，不将二期启动标为一期已 close。
2. 从已获取远端 tip 并核对一致的最新 `main` 建立 `phase2`，不从一期工作分支创建。
3. 子任务从最新 `phase2` 建立 `phase2-N-主题`。一个可独立编译和验证的工作片完成后，精确提交本任务文件并及时合回 `phase2`；接口生产者和消费者共同切换，不积压到阶段末才集成。
4. 多 checkout 使用独立子任务分支并核对 ownership；同一 checkout 不在其他执行者运行中切换。集成与验证串行，冲突回到对应 owner 处理，不覆盖他人修改。
5. 本次授权包含子任务所需本地 stage/commit/merge；不包含 push、合回 `main`、rebase、reset、stash 或 force。构建/发布流程不等于镜像上传、商店提交或公开发布授权。

## 1.2. 执行波次与边界

- **启动核对**：ARCH-2001。之后重新读取最新实现，修订仍为草案的范围，保留稳定任务身份。
- **设计冻结**：ARCH-2002 → ARCH-2003 → OBS-2001 → ARCH-2004。先分类规则与处理主线，再日志合同，最后图；不先画图反推功能。
- **后端串行切换**：LEX-2001～2004 → DAT-2001 → OPS-2001～2002 → ENR-2001～2002 → OBS-2002 → API-2001～2002。依赖表表示语义前置；公共接口与所有消费者在同一集成窗口串行落地，中间不交付不编译的树、不以旧接口兼容绕过。
- **客户端与验收**：EXT-2001 → EXT-2002 → QLT-2001 → ARCH-2005。独立验证/审查按 Harness 真实身份执行，不由实施任务自签；审查只读冻结输入，不重跑交付命令。
- **发布与使用闭环**：按[发布设计](release.md)落实版本、资料包、Docker/Compose、制品构建、状态与用户流程，并纳入最终验收；已在发布页列出 12 项 Catalog Task；各任务先完成设计与直接验收入口核对，再派发实现。
- 观测是每个节点的验收要求，OBS Task 只拥有合同与适配器，不另建“观测业务阶段”。未来性能和模型工作不借机混入。
- Catalog 的设计 Task 可输出待冻结决定；每项对应实现派发前必须已有唯一合同、具体样例和收紧的 file claim。冻结时如超出 90 分钟或 8 个主要产品文件，按现有分解规则拆分；不得把大 glob 当成无限范围授权。

## 1.3. 逐项工作与验收

### 1.3.1. LF-TSK-ARCH-2001 · 核对用户启动通知、收尾证据与最新主干起点
- 主责：`LF-WS-ARCH`；预计 30 分钟；前置：用户启动通知与最新主干核对。
- 产出：记录用户启动授权、phase1 收尾依据、实际主干名与最新 tip、phase2 起点及干净隔离边界。
- 验收：依据明确的二期启动授权执行；记录一期验收缺口，不把开发授权或自动续轮当作一期 PASS。
- 验收：核对远端最新 main 后创建 phase2，子任务使用 phase2-N-主题；不从一期工作分支派生。
- 验收：核对分支归属和并发修改；按已授权范围精确提交并及时合回 phase2，不覆盖他人工作、不推送或合回 main。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

### 1.3.2. LF-TSK-ARCH-2002 · 冻结词段分类、预处理资格及排序规则表
- 主责：`LF-WS-ARCH`；预计 75 分钟；前置：`LF-TSK-ARCH-2001`。
- 产出：按五个独立维度给出确定规则、来源字段、边界样例和批准的行为变更清单。
- 验收：区分单词/短语、基础/重点/长尾、低频/未知频率、资料质量、提示与缓存；基础不等于用户熟练。
- 验收：明确基础名单依据、重点条件与排序、短语质量、默认首义、具体阻断原因及词形继承；低频或缺少排名不单独阻断。
- 验收：冻结阈值和优先级的依据及合成验收样例，不凭讨论臆造概率或个人画像；越出已批准范围先提交用户决策。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

### 1.3.3. LF-TSK-ARCH-2003 · 冻结预处理、启动、同步主线与跨模块接口
- 主责：`LF-WS-ARCH`；预计 90 分钟；前置：`LF-TSK-ARCH-2002`。
- 产出：每节点的输入输出、owner、调用次序、失败出口和版本合同；列出全部消费者及串行切换顺序。
- 验收：分别明确预处理、启动与请求七步流程；静态质量判断前移，范围、版本及响应安全检查保留在运行期。
- 验收：冻结准确词形查询端口、发布投影、诊断和敏感记录接口；现有消费者完整盘点，不造空壳多义项接口。
- 验收：明确正式/演示模式、无发布版本、依赖故障、预热失败、版本冲突的唯一行为；不隐式导入或重建。
- 验收：查词默认义仍非上下文消歧；异步 Promise/线程若服务当前提示仍属于关键路径。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

### 1.3.4. LF-TSK-OBS-2001 · 冻结结构化日志模板、事件和打印时机
- 主责：`LF-WS-OBS`；预计 60 分钟；前置：`LF-TSK-ARCH-2003`。
- 产出：启动、请求、状态变化、导入事件表与成功/空提示/异常样例，区分敏感台账。
- 验收：明确事件名、字段类型单位、原因码、级别、触发次数、开关和保留；result 遵守结果语义，NO_HINT 仅为原因。
- 验收：普通事件只保留聚合数值、版本和随机关联 ID；不得含字幕、中文提示、观看身份或凭据，ID 不作指标标签。
- 验收：保留敏感分析记录的授权、去重和失败语义；不在本阶段引入异步丢失队列或新采集。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

### 1.3.5. LF-TSK-ARCH-2004 · 按冻结主线形成架构和执行时序图
- 主责：`LF-WS-ARCH`；预计 60 分钟；前置：`LF-TSK-ARCH-2003`、`LF-TSK-OBS-2001`。
- 产出：功能分层、观看时序、资料生产与发布时序的内嵌 PlantUML 图及节点到责任映射。
- 验收：图源来自冻结的分类和流程结论；说明当前提示关键路径、独立离线链及发布身份交接，不按线程创建业务服务。
- 验收：按 documentation-policy 和指定 skill 在 ignored 目录渲染并实际查看后原样入正文；缺工具或预览为 BLOCKED，不以静态围栏检查代替视觉验收。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

### 1.3.6. LF-TSK-LEX-2001 · 实现预处理分类与独立提示和缓存资格
- 主责：`LF-WS-LEX`；预计 90 分钟；前置：`LF-TSK-ARCH-2004`。
- 产出：基础词、频率及复杂词表的正交分类证据与独立提示/缓存资格；重点和长尾沿既有 `priority` 连续排序，不新增二元阈值，含直接测试。
- 实现边界：分类结果由导入计划与既有发布消费者实际消费；区分 StarDict 缺排名和规范 CSV 明确频率，不新增评分阈值或改写 priority 公式。范围收紧至七个主要产品文件；Catalog 合同为 v2/change 2.0.0。
- 验收：按冻结规则生成分类及原因，低频和缺失排名分别处理；基本词形继承不误伤完整短语。
- 验收：重点词的默认资料及词形资格可核对；提示资格不由预热资格决定，禁止行为熟练度推断。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test`。

### 1.3.7. LF-TSK-LEX-2002 · 实现短语质量与默认释义的发布前判定
- 主责：`LF-WS-LEX`；预计 75 分钟；前置：`LF-TSK-LEX-2001`。
- 产出：有原因的短语/默认释义过滤与边界测试，不再依靠频率掩盖来源质量。
- 实现边界：仅改提示准备策略和 StarDict 人工映射的策略身份；受信完整 lemma 可豁免短语启发式，硬校验不豁免，普通 CSV 无 curated 开关。Catalog 为 v2/change 2.0.0。
- 验收：按照冻结规则调整短语粗过滤，覆盖可靠完整短语、残缺词组和高频坏资料。
- 验收：完整原始释义保留；首候选无效不以后项补位，质量判断发布前完成。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test`。

### 1.3.8. LF-TSK-LEX-2003 · 原子切换准确词形合同与全部查询消费者
- 主责：`LF-WS-LEX`；预计 90 分钟；前置：`LF-TSK-LEX-2002`。
- 产出：候选枚举归 Enrichment，LexiconCatalog 只接受规范词形集合；两实现与全部消费者和测试在同一可编译切片切换。
- 实现范围：八个主要产品文件及对应消费者测试，Catalog 为 v3/change 3.0.0；不借此拆分读写 Repository。
- 验收：公开 lookupForms(List<String>) 返回完整候选、可选发布身份与请求局部查询计数；Enrichment 生成规范键，保留同形冲突与 BLOCK 证据，不使用全局计数。
- 验收：删除旧字幕查询入口，不维护双套兼容；Cached/Builtin、用例、装配与所有测试调用者一起切换并验证全后端编译。
- 验收：空输入不读存储；大小写/标点规范化集中且无字符范围漂移；读写 Repository 分离不捆绑此次公开合同。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test :enrichment:test :api:test :adapters:test`。

### 1.3.9. LF-TSK-LEX-2004 · 拆分版本缓存管理与导入应用编排
- 主责：`LF-WS-LEX`；预计 90 分钟；前置：`LF-TSK-LEX-2003`。
- 产出：缓存负责版本/预热/容量；导入用例负责预检结果、来源一致性和发布调用，不依赖文件/SQL。
- 实现范围：八个主要产品文件及来源消费者测试；来源返回重读摘要/原始行数，由用例在事务内核对身份及实际条数。Catalog 为 v2/change 2.0.0；文件读取与摘要计算留在适配器。
- 验收：移除查询服务内字幕候选生成；负缓存、预热与动态缓存保持有界且绑定发布身份。
- 验收：导入应用编排经端口消费来源，不调用适配器 main；故障不形成部分发布。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test`。

### 1.3.10. LF-TSK-DAT-2001 · 落实发布投影字段与数据库约束
- 主责：`LF-WS-DAT`；预计 60 分钟；前置：`LF-TSK-LEX-2003`。
- 产出：同步调整最新 SQL、发布写入映射及隔离库回归；保存来源、频率证据、准备轨迹和词形最终原因，不另建历史迁移链。
- 验收：每个增加字段都有明确生产者与消费者；能沿来源追溯提示及阻断决定，不存个人状态。
- 验收：不连接或重建真实运行库；最新 SQL 与适配器集成测试在串行交接中共同核验。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :adapters:postgresIntegrationTest`。

- 实现边界：Catalog v2/change 2.0.0；数据库字段和写入者同批交付，SQL 追溯消费见设计 1.6.4。读角色拆分仍归 OPS-2001。

### 1.3.11. LF-TSK-OPS-2001 · 适配 PostgreSQL 读写边界与原子发布
- 主责：`LF-WS-OPS`；预计 90 分钟；前置：`LF-TSK-DAT-2001`、`LF-TSK-LEX-2004`。
- 产出：只读接口和发布接口按调用方隔离，组合持久化保持完整映射及原子事务。
- 验收：查询返回完整候选和统一发布身份；映射不遗漏分类或冲突证据。
- 验收：原子提交与失败回滚、旧版本可查及开发结构重建边界保持；无跨域表访问。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test :api:test :adapters:postgresIntegrationTest`。

- 实现边界：Catalog v2/change 2.0.0；查询服务、版本缓存与 API 一起切换到 read role，聚合接口只保留在基础设施组合；直接测试证明只读替身和真实 PostgreSQL bean 的 Spring 装配。数据库字段已由 DAT-2001 同步交付，不重复改 SQL。

### 1.3.12. LF-TSK-OPS-2002 · 将导入 CLI 收敛为入口和来源适配
- 主责：`LF-WS-OPS`；预计 90 分钟；前置：`LF-TSK-OPS-2001`。
- 产出：两种来源经统一应用准备与发布用例；持久化消费已准备条目，CLI 保留来源、参数、资源和输出。
- 验收：不同来源进入统一准备流程且缺失证据不伪造；CLI 仅参数、资源、格式和输出。
- 验收：普通发布不触发重建；重建仍需精确确认，等待输入不刷进度；不新增后台调度。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :lexicon:test :adapters:test :adapters:postgresIntegrationTest`。

- 实现边界：Catalog v2/change 2.0.0；5 个主要产品文件完成准备用例、发布端口、应用服务、持久化和 CLI 的原子切换，删除 raw-row 发布端口，不以转发包装保留重复业务逻辑。

### 1.3.13. LF-TSK-ENR-2001 · 分离原文位置匹配与发布候选资格检查
- 主责：`LF-WS-ENR`；预计 90 分钟；前置：`LF-TSK-LEX-2004`。
- 产出：从现有提示策略提取实际被调用的位置匹配和发布候选完整性校验，保留精确范围和冲突证据。
- 验收：位置匹配独立处理词边界、重复出现、标点和 UTF-16 范围；不重复实现已归 Enrichment 应用层的查询键枚举。
- 验收：只消费已发布默认义和动作；资格校验不重做离线清洗，不用预过滤掩盖同形多条目冲突。
- 验收：提取组件被现有策略实际消费，不留空框架；未改规则部分用同一合成样例验证行为保持。
- 实现边界：Catalog v3/change 3.0.0；领域内包可见组件按设计 1.6.3 拆分，移除观看时静态短语重筛，保留短释安全和全部歧义证据。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :enrichment:test`。

### 1.3.14. LF-TSK-ENR-2002 · 实现增量编排、默认义资格与提示选择边界
- 主责：`LF-WS-ENR`；预计 90 分钟；前置：`LF-TSK-ENR-2001`。
- 产出：新增区间、资格、冲突选择、请求合并、坐标映射独立，移出诊断文本拼接。
- 验收：消费发布时的静态资格，不动态重算基础名单或清洗来源；运行期范围和发布身份校验不移除。
- 验收：跨区间重复、跨片段偏移和混合版本安全处理；同形歧义不能被提前过滤掩盖。
- 验收：排序/重叠处理符合批准规则；默认义不宣称语境正确。
- 实现边界：Catalog v2/change 2.0.0；规划、映射、合并和冲突选择按设计 1.6.3 提取为被实际调用的包内组件；全部版本证据先于去重，诊断字段直接删除，授权日志合同不变。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :enrichment:test :api:test`。

### 1.3.15. LF-TSK-OBS-2002 · 实现普通结构化观测与敏感记录适配器
- 主责：`LF-WS-OBS`；预计 90 分钟；前置：`LF-TSK-OBS-2001`、`LF-TSK-ENR-2002`。
- 产出：按日志合同实现适配器及脱敏/格式/失败隔离测试，不触发额外资料采集。
- 验收：所有事件字段类型、原因及单位匹配合同；对无提示/失败不混淆，正文与凭据不进入普通日志。
- 验收：敏感记录文件实现与普通日志分离，沿用同步调用及重试/重启去重/失败语义，不构造假异步成功。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :adapters:test`。

### 1.3.16. LF-TSK-API-2001 · 落实启动模式和已发布资料装载
- 主责：`LF-WS-API`；预计 60 分钟；前置：`LF-TSK-OPS-2002`、`LF-TSK-ENR-2002`。
- 产出：正式/演示模式、发布身份、缓存初始化和就绪/降级行为匹配冻结合同。
- 验收：正式配置缺失不静默退回五词演示库；空发布、数据库故障、预热失败均有确定行为。
- 验收：只装载有界发布资料，不执行导入/重建/模型；英文展示不依赖启动成功。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :api:test`。

### 1.3.17. LF-TSK-API-2002 · 连接请求主线、HTTP 映射和终态日志
- 主责：`LF-WS-API`；预计 90 分钟；前置：`LF-TSK-API-2001`、`LF-TSK-OBS-2002`。
- 产出：控制器调用应用用例和观测端口，移除直接文件实现，保留外部协议和计时消费合同。
- 验收：正常/空提示/无新增/非法请求/故障均有一致响应与终态汇总，不把敏感台账作为普通日志。
- 验收：敏感记录失败不改变提示结果，不隐瞒失败；原 SegmentAnalysisLog 的消费者和测试完成迁移。
- 验收：不因模块拆分随意修改 HTTP 字段；Server-Timing 如增项必须同步消费者并测试。
- 直接检查：`python3 -m scripts.environment.java_exec backend/gradlew -p backend :api:test`。

### 1.3.18. LF-TSK-EXT-2001 · 收拢页面生命周期与采集快照协调
- 主责：`LF-WS-EXT`；预计 90 分钟；前置：`LF-TSK-API-2002`。
- 产出：页面生命周期和采集快照从入口解耦，请求协调器保持唯一请求状态所有者。
- 验收：导航/跳转/字幕关闭/增强开关准确失效；新增字幕不清除仍有效提示，旧响应不跨字幕展示。
- 验收：采集、请求、展示不重复维护同一状态；本机偏好不上传、不新增采集。
- 直接检查：`npm --prefix extension test`。

### 1.3.19. LF-TSK-EXT-2002 · 对齐客户端观测和显示边界
- 主责：`LF-WS-EXT`；预计 60 分钟；前置：`LF-TSK-EXT-2001`。
- 产出：客户端采集/传输/显示耗时与失败原因匹配新节点，英文优先及本机抑制保持。
- 验收：超时、取消、迟到及无提示分开计数；有界聚合、不输出正文、不上传个人偏好。
- 验收：HUD/Server-Timing 消费者与后端一致；真实页面体验不能仅由夹具测试宣称。
- 直接检查：`npm --prefix extension test`。

### 1.3.20. LF-TSK-QLT-2001 · 串行核验完整主线及跨模块验收矩阵
- 主责：`LF-WS-QLT`；预计 90 分钟；前置：`LF-TSK-API-2002`、`LF-TSK-EXT-2002`、`LF-TSK-OPS-2002`。
- 产出：补齐合成集成场景与规则变化证据，冻结交付输入供独立 TASK_VALIDATION 使用。
- 验收：覆盖基础词/词形、重点/长尾/未知频率、可靠和噪声短语、坏默认义、同形冲突、增量范围及版本切换。
- 验收：覆盖冷缓存、正式/演示启动、取消迟到、敏感记录故障和发布回滚；使用隔离服务，不连接用户运行库。
- 验收：规则改变有批准依据与预期差异；同输入 Change/Repository Verify 未齐备不得 PASS。
- 直接检查：`python3 scripts/check_changes.py && python3 scripts/check_repository.py`。

### 1.3.21. LF-TSK-ARCH-2005 · 同步最终功能文档并核对阶段交付证据
- 主责：`LF-WS-ARCH`；预计 60 分钟；前置：`LF-TSK-QLT-2001`、`LF-TSK-ARCH-2004`。
- 产出：最终文档与冻结实现一致，P2 各项证据、限制、用户验收及未来待办归属清楚；最终收尾同时依赖 QLT-2002 的发布闭环验收。
- 验收：清理分散的第二/第三阶段归属陈述；图文只描述已确定主线，模型/隐私护栏不放宽。
- 验收：区分静态检查、合成集成、真实页面与正式 receipt；不自签独立 validation/review，不将规划完成当产品完成。
- 验收：未选入本阶段的性能、多义项、上下文和知识补充事项仍在后续池；不得宣布其已实现。
- 直接检查：`python3 -m scripts.repository.planning_check && python3 -m scripts.repository.docs_check && python3 -m scripts.repository.policy_projection --check`。

## 1.4. 准备与交付证据的区别

准备阶段仅运行文档、Catalog 与 policy projection 静态检查，说明任务可读取、依赖无环和共享规则未漂移；不执行产品实现或伪造完整验收。正式交付时，直接测试和同输入 Change/Repository Verify 在 TASK_VALIDATION 边界内核验；INDEPENDENT_REVIEW 只读冻结 diff/evidence，CATALOG_DECISION 只核对 receipt 与依赖 hash DAG。未运行、跳过或缺环境不得称 PASS。

后续事项唯一汇总在[后续待办池](../future.md)，每次再由用户选入下一个阶段。

## 1.5. 计划整合与共享入口

### 1.5.1. LF-TSK-QLT-2003 · 整合二期任务、授权边界与发布交付目标

- 主责：`LF-WS-QLT`；预计 90 分钟。
- 按用户授权确定 macOS 调试与 Docker 发布、phase2 及子任务分支集成，不冒充一期收尾完成或远端发布授权。
- 所有二期范围具有稳定任务身份、责任、具体输出、直接检查与精确依赖；最终任务覆盖全部阶段交付，不复用一期 receipt。
- 今天已完成的清洗功能不重复排期；公开接口与全部消费者按可构建工作片调整；后续模型/性能专项不混入。
- 设计、静态检查、产品实现和正式验收分开记录；OpenSpec ignored 入口不承载唯一共享合同。

### 1.5.2. LF-TSK-QLT-2004 · 将发布验收与使用入口接入共享工程合同

- 主责：`LF-WS-QLT`；预计 75 分钟。
- 在 module-checks 注册真实发布检查及其输入/依赖/环境，发布代码变动进入 Change/Repository Verify，不只运行文档检查。
- 缺 Docker 或必要资料与目标架构记 BLOCKED，不跳过业务验收；单元测试证明 check 选择和环境传播。
- README 阅读合同调整为取得发行产物、初始化与启动使用，macOS 编译调试保留按需入口，不重复维护规则。

### 1.5.3. LF-TSK-QLT-2005 · 修复依赖送验恢复

- 主责：`LF-WS-QLT`；预计 60 分钟；前置：`LF-TSK-QLT-2003`。
- 问题：失败送验必须保留，但既有 Gate 在读取验收链前按送验数量判歧义，导致失败重送无法交给下游。
- 改造：仅采用唯一完整 PASS 链，不按最新时间或任意顺序选择；多个 PASS、损坏/重复记录、身份/hash/嵌套依赖异常仍阻断。
- 验收：隔离 fixture 覆盖失败后成功、未完成、零/多条 PASS、篡改和嵌套依赖；不删真实历史，不重跑交付命令。
- 此项是用户批准的工程阻塞修复，列入启动核对前置，不改变二期产品范围。
