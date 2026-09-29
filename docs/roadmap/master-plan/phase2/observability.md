# 1. 提示主线观测合同

上级：[二期设计](design.md)。观测用于解释各步骤的决定与耗时，不另建业务链；实施与验收状态只见[状态页](status.md)。

## 1.1. 普通事件结构

后端普通日志使用单行 JSON，以固定字段输出，不以插值拼接原始请求或异常消息。事件 schema 为 `lexiflow.event.v1`。

| 字段 | 类型与要求 |
| --- | --- |
| schema | 固定字符串 `lexiflow.event.v1` |
| event | 下表中的固定事件名，不从用户输入生成 |
| stage | 固定处理节点：startup、import、query、selection、request、client、analysis |
| result | PASS、BLOCKED、FAIL；无提示是成功处理，不是失败 |
| reason | 固定原因码；无原因用 OK，不填写自由文本 |
| duration_ms | 非负整数，使用单调时钟计算当前事件耗时；不是时间戳 |
| counts | 当前事件明确允许的非负整数计数；缺少测量时不伪造 0，省略该键 |
| lexicon_version | 已绑定发布身份时填写非负整数；没有读取成功则省略，不把未知写为 0 |
| request_id | 后端接受一次请求时生成的随机关联 ID；请求类事件必填，其他事件省略 |
| timings_ms | 请求终态填写校验、候选、查询、决策、敏感记录和总 API 耗时；未执行节点省略 |

`request_id` 不作为指标标签，也不代表观看身份；不直接信任客户端传来的任意日志字段。logger 自身提供时间和级别。聚合指标只用固定 event/reason/stage 标签，版本与 request_id 留在日志，不进入高基数指标标签。

## 1.2. 事件、级别和打印时机

| event | 打印时机与级别 | 允许计数 | 原因码 |
| --- | --- | --- | --- |
| runtime.start.completed | 启动就绪判定结束恰好一次；正常 INFO，不可就绪 ERROR，预热降级 WARN | prewarm_positive、prewarm_negative | OK、DEMO_MODE、NO_PUBLISHED_DATA、DEPENDENCY_UNAVAILABLE、SCHEMA_MISMATCH、PREWARM_DEGRADED |
| lexicon.import.stage | 每个固定导入步骤开始和结束 INFO；故障按固定原因分级；长步骤只发有界心跳，不另设逐行日志 | input_rows、prepared_rows、hint_rows、blocked_rows | STARTED、OK、SOURCE_INVALID、SOURCE_CHANGED、CANCELLED、DEPENDENCY_UNAVAILABLE、INTERNAL_ERROR、PUBLISH_ROLLED_BACK |
| lexicon.import.completed | 每次发布尝试一次终态；成功 INFO，明确取消 WARN，失败 ERROR | input_rows、prepared_rows、lookup_rows、blocked_rows | OK、CANCELLED、SOURCE_INVALID、SOURCE_CHANGED、PUBLISH_ROLLED_BACK、DEPENDENCY_UNAVAILABLE、INTERNAL_ERROR |
| lexicon.cache.version_changed | 观察到资料身份变化并清理缓存后一次 INFO | invalidated_positive、invalidated_negative | VERSION_CHANGED |
| caption.request.completed | 每次 HTTP 请求恰好一次终态；正常含空提示 INFO，无效请求 WARN，内部失败 ERROR | new_ranges、query_keys、positive_hits、negative_hits、cache_misses、db_batches、version_reads、prewarm_reads、candidates、selected、ambiguous、overlap_dropped | OK、NO_HINT、NO_NEW_SEGMENTS、INVALID_REQUEST、NO_PUBLISHED_DATA、VERSION_CONFLICT、DEPENDENCY_UNAVAILABLE、INTERNAL_ERROR |
| runtime.dependency.changed | 依赖从可用转不可用或恢复时打印；故障 WARN，恢复 INFO；同状态不重复 | 无 | DEPENDENCY_UNAVAILABLE、RECOVERED |
| analysis.record.failed | 一次请求敏感记录写入失败时最多一次 WARN；提示业务结果保持独立 | attempted_segments | ANALYSIS_WRITE_FAILED |

db_batches 仅为缺失词形批量读取次数，版本读取与预热分别记录，不能把缓存命中请求描述为零次数据库访问。

导入 stage 另有固定 `step` 与 `phase` 字段：step 为 source_check、prepare、persist、publish；phase 为 started、heartbeat、completed。只有 completed 可携带步骤最终数量；开始和心跳不冒充完成百分比。导入终态可追加有界 `reason_counts`，键只来自预处理规则枚举，不按词条分桶。

原因与结果关系固定：NO_HINT、NO_NEW_SEGMENTS、DEMO_MODE、PREWARM_DEGRADED 不让业务结果变为 FAIL；NO_PUBLISHED_DATA、DEPENDENCY_UNAVAILABLE 表示 BLOCKED；非法输入及违反版本/结构合同为 FAIL。schema 不合或内部异常不得冒充正常空提示。

请求 terminal 在 HTTP 过滤器的统一终态出口输出，包括反序列化/校验失败的入口异常映射。业务组件只返回结构化计数与原因，不直接打印字幕或重复请求终态。导入进度的交互确认文本与结构化事件分开，等待人工输入不发执行心跳。

## 1.3. 浏览器观测与展示边界

浏览器继续保留内存聚合计数和现有分段时延，不逐词输出 INFO，不持久化观看日志。增加或明确固定原因：disabled、source_hidden、navigation、cancelled_before_send、cancelled_in_flight、late_response、protocol_mismatch、backend_unavailable、no_hint。

失败原因仅用于本机诊断；不通过额外网络请求上传。后端 request_id 可随原有响应关联单次诊断，但不保存视频身份。测不到服务端耗时必须标记缺失，不能填 0 或把浏览器往返耗时当数据库耗时。

## 1.4. 敏感记录与故障隔离

普通事件禁止字幕、中文释义、词段正文、观看 URL、视频/轨道身份、用户偏好、JDBC 字符串、凭据、私有文件路径及模型载荷；异常日志只输出固定错误分类，不透传可能含输入的 exception message。不得通过 DEBUG 绕过此边界。

敏感分析记录继续由用户显式授权的本机独立台账承担，保留 segment.key 去重、重启恢复与成功写入语义。Docker 发行包默认关闭，开发者现有授权不能继承给其他使用者。正文不得进入普通 logger 或诊断包。

二期保持当前同步写入语义，将其单独计时，不能称为“异步完成”。记录失败不丢弃已算出的提示，不假装写入成功；以后要异步化时另行设计持久交接、恢复与背压。

日志格式化/输出失败不得改变已经确定的业务结果；内部统计错误不能吞掉原有产品异常。固定字段与原因码用单元测试校验，敏感内容以合成特殊字符样例检查，不读取真实字幕。

## 1.5. 合成事件与直接验收

```json
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"PASS","reason":"OK","duration_ms":7,"request_id":"00000000-0000-4000-8000-000000000001","lexicon_version":7,"counts":{"new_ranges":1,"query_keys":3,"positive_hits":1,"negative_hits":1,"cache_misses":1,"db_batches":1,"candidates":1,"selected":1},"timings_ms":{"validation":0,"candidates":0,"query":3,"selection":1,"analysis":1,"api":7}}
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"PASS","reason":"NO_HINT","duration_ms":2,"request_id":"00000000-0000-4000-8000-000000000002","lexicon_version":7,"counts":{"new_ranges":1,"selected":0},"timings_ms":{"api":2}}
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"BLOCKED","reason":"DEPENDENCY_UNAVAILABLE","duration_ms":5,"request_id":"00000000-0000-4000-8000-000000000003","counts":{"new_ranges":1},"timings_ms":{"query":4,"api":5}}
```

以上数字和身份仅用于合同示例，不是实测结果。直接测试必须证明：成功/空提示/非法请求/依赖故障各一次终态；版本变化一次失效事件；敏感记录故障仍返回既定提示；取消与迟到计数不混淆；未测量字段不伪造；注入换行、凭据样式及合成字幕均不能进入普通事件；日志故障不改变业务响应。

## 1.6. 适配器与集成边界

StructuredEvent 是普通日志的封闭技术值：事件、原因、计数键、耗时键、导入 step/phase 与规则计数键均使用固定枚举；只接受非负整数和后端生成的 UUID 请求关联号，不接收自由文本或异常对象。result、stage 和 level 由事件/原因确定，不允许调用者任意组合。CANCELLED 为 BLOCKED/WARN；启动不可就绪 ERROR、预热降级 WARN；请求 INVALID_REQUEST 为 FAIL/WARN、其他请求 FAIL/BLOCKED 为 ERROR；依赖不可用与敏感写入失败 WARN。schema 错误与版本冲突为 FAIL。请求终态必须有 request_id 与 api 总耗时；analysis.record.failed 关联同一请求 UUID；其他事件不携带 request_id，timings_ms 仅请求终态允许。已知 lexicon_version=0 与未知省略有区别。导入 stage 的 STARTED 对应 started，heartbeat 无最终数量，completed 才可携带最终计数；reason_counts 仅导入终态使用固定准备规则键，不接收词条。未取得的测量用缺项表示。

StructuredEventLogger 负责单行 JSON 编码与按固定级别输出；提供封闭事件 supplier 的失败隔离入口，事件构造、编码或输出失败仅返回未写出，不打印 exception message、路径或正文，不改变调用方业务结果。事件模型与适配器直接验收不代表所有调用位置已接线；请求终态与入口异常在 API-2002，启动/依赖状态在 API-2001，导入/缓存节点由 OBS-2003 逐项取得实际调用与次数证据，并作为 QLT-2001 的硬依赖。

敏感台账通过 adapters 内的 SegmentAnalysisStore 技术接口接收已准备的 SegmentAnalysisRecord（散列 segmentId、英文及分段 translated ranges）。它是本机文件格式合同，不新建业务 Domain 或 Gradle 模块，不让 adapters 依赖 Enrichment/API。API 的 SegmentAnalysisLog 仅将已验证 request/result 映射到该中立记录并调用 Store；文件、锁、去重、权限、JSON 编码和专用 console 全部属于 FileSegmentAnalysisStore，组合根选择路径并装配具体实现。默认路径定位留在组合根，不进入领域或文件 Store；现有开发授权不自动扩展至 Docker，发行模式必须装配 disabled Store，后续显式启用才写入。

Record 及 ranges 防御性复制，范围要求 UTF-16 半开、升序不重叠且在英文内，禁止切开代理对；身份与版本有效。Store 同步写入并 force 成功后才更新去重集合和专用 console；重复 key、重启恢复、旧尾补全不重写、失败不阻断提示保持。空列表和 disabled Store 不初始化文件；损坏 JSON、重复 ID、未完成尾行及符号链接拒绝，不删除、不截断或自动修复。JSON 编码使用现有 BOM 锁定的 Jackson core；不引入平台外发、异步队列或额外资料采集。文件故障测试只用合成临时文件。

## 1.7. 请求观测的具体交接

`HintSelectionResult` 同时返回提示与实际选择计数。ambiguous 统计匹配位置 `(start,end)` 上存在多个 entryId 的位置数，包含禁止提示的歧义证据；overlap_dropped 只统计可显示、非歧义且未因 entryId 重复而排除，最终仅因重叠落选的候选次数。计数不改变既有优先级、去重和区间选择规则。领域只返回值，不依赖 logger。

`MeasuredIncrementalCaptionResult.Diagnostics` 汇总 newRanges、ambiguous、overlapDropped、publishedVersion、versionConflict 与 candidatesNanos。newRanges 是实际处理的连续新增区间数；selected 是跨区间去重后返回的提示数。候选计时包含区间规划及查询词形生成，query 只含实际词库查询，selection 包含策略与坐标映射。所有区间的查询版本、全部候选版本及映射提示版本在过滤与去重前统一检查；混版清空提示并携带明确 conflict，不以空列表反推原因。没有唯一已知版本则省略版本字段；无新增区间不伪造查询/选择计时，无查询 key 不打印 query 节点耗时。

`CaptionRequestObservationFilter` 只负责 `/api/v1/caption-hints` 的请求生命周期：生成不受客户端影响的 UUID，立即写入 `X-Request-ID` 响应头，将独立 observation 放入 request attribute，并在 finally 通过失败隔离的事件 supplier 输出唯一 terminal。observation 只存 ID、固定原因与已测数字，不存字幕、业务结果或异常对象，不使用全局累加器、ThreadLocal 或读取后清零。控制器及异常处理器只补充 observation，不重复打印 terminal。

控制器先独立捕获请求转领域对象的输入错误，再执行用例；用例内部异常不得映射为输入错误。统一异常处理器把 JSON/域输入错误映射为 400 INVALID_REQUEST，依赖或未发布映射为 503，内部异常映射为 500 INTERNAL_ERROR；响应正文与日志均不透传 exception message/cause。LexiconNotReadyException 固定保存抛出时原因，不事后读取可变全局 state。SCHEMA_MISMATCH 在请求事件中归为 INTERNAL_ERROR；混版明确返回 503 VERSION_CONFLICT，不执行敏感记录。

成功覆盖的敏感分析记录仍同步执行并单独计时，无 processed keys 则不调用；文件记录失败只产生最多一次、同 UUID 的 analysis.record.failed，保留已确定的成功响应。其故障捕获仅包围记录调用，不吞掉用例错误。成功响应字段及 Server-Timing 的 query/rules/api 名称保持不变；terminal 的 api 测量覆盖 HTTP 处理出口，不宣称包含网络时延。未执行或未取得的节点测量省略；事件构造、编码及 sink 故障均不改变业务响应。

直接验收使用合成真实 HTTP 请求，覆盖成功、空提示、无新增、非法 JSON、无效领域输入、无发布、依赖故障、混版、内部错误、敏感记录故障及日志 sink 故障；检查每请求恰好一次 terminal、关联头一致、客户端伪造 ID 无效和并发计数隔离。领域测试单独证明歧义/重叠计数及混版原因，普通日志不得包含合成字幕、释义、路径或异常载荷。

## 1.8. 导入与缓存的实际节点交接

### 1.8.1. 单次导入生命周期

仅显式 publish 建立一次 `LexiconImportObservation`；validate、basic-report、prewarm-report 是只读检查，不伪造发布完成。CLI 在参数解析后、来源摘要计算前开始 source_check；文件识别、摘要及 StarDict 基础词选择完成后结束该步。prepare 包围完整流式预检与 requirePublishable；不从有界预热预览推导全量数量。CLI 将同一 observation 交给应用发布服务，失败出口幂等收口一次，不另造全局状态。

`LexiconImportObserver` 是应用层的封闭观测端口，事件只含固定枚举、数字及有界规则计数。`LexiconEventObserver` 位于 adapters，将其映射到 StructuredEvent；应用与领域不依赖 logger。每次准备统计 input_rows（凭据原始行数）、prepared_rows（准备成功的词条数）、hint_rows/blocked_rows（准备词条的提示/阻断结果），reason_counts 按完整扫描中每词条的 decisiveRule 与 matchedRules 合并去重计数。规则只映射白名单，不携带 lemma、释义或任意键；失败时不把预检数量冒充已提交数量。

persist 从进入发布资源/写入步骤开始；发布端口增加事务进度回调，在批量写入、flush 和条数校验完成后结束 persist 并开始 publish。persist completed 仅表示事务内写入步骤完成，不表示对外可见。lookup_rows 来自持久化实际生成的投影行数；未执行或未取得的计数省略。publish 覆盖资料元数据写入及事务提交，只有发布端口正常返回已提交版本后才输出 publish completed 和唯一 OK 终态。失败和预提交事件不输出未提交版本。

来源凭据/摘要/行数不一致使用 `LexiconSourceChangedException`，不靠异常字符串分类。来源解析/读取失败为 SOURCE_INVALID；现有取消异常为 CANCELLED；资源不可用为 DEPENDENCY_UNAVAILABLE；其他内部异常为 INTERNAL_ERROR。只有事务 afterCompletion 明确通知 ROLLED_BACK 后，普通发布失败才可称 PUBLISH_ROLLED_BACK；来源变化和取消仍优先保留各自原因。未知提交结果不能伪称已回滚。失败关闭尚在运行的步骤并输出一次终态，后续 close/catch 不重复终态，不吞原业务异常。

观察器回调异常、事件构造与 sink 故障均隔离，不改变发布/回滚结果或原始业务异常。心跳使用单调时钟，只有实际执行扫描、批次或既有进度回调时按最多每三分钟一次触发；不另建调度器，不逐行输出，不把等待确认当执行。重建确认之前尚未进入 publish，不制造导入开始/取消事件；实际发布来源抛出取消信号则记录 CANCELLED。所有验证用合成临时文件和隔离 PostgreSQL，禁止访问真实运行台账或资料。

### 1.8.2. 缓存版本清理

`LexiconCacheObserver` 接收本次不可变版本清理结果，由 API 的 LexiconRuntime 注入 `LexiconEventObserver`。首次从未绑定状态装载版本不打印 version_changed；已经绑定的版本发生变化（包括转为 0）时，在 pinned/dynamic 清空并重新绑定之后、预热之前输出恰好一次。版本读取失败或同版本查询不输出，预热降级不重复失效事件。

失效数量在清理前取实际缓存 key：包含 HINT 的候选组为正，空候选或纯 BLOCK 为负，混合 HINT/BLOCK 只计一个正向 key；合并 pinned/dynamic 的实际内容，不用候选行数、容量或命中累计数代替。duration_ms 只计版本切换清理，不含新版本预热；不输出词形或缓存正文。观察器故障不阻止清理、预热或查询；不使用 read-clear、ThreadLocal 或全局累计计数。

直接测试覆盖真实失效数量、同版本不重复、初次装载、零版本、预热失败和 observer 抛错；API 测试检查真实 runtime 接线。隔离 PostgreSQL/CLI 验证两种来源成功发布、来源变化及真实回滚，核对事件顺序、唯一终态、提交前无成功发布事件和失败后旧资料仍在；只有 formatter 测试不构成节点验收。


## 1.9. 客户端节点与计数的具体交接

- 内存 `Diagnostics` 只接受固定 stage/outcome 和有限非负时长，保留每节点最多 256 个样本；缺失值为 null，固定字段之外不接受字幕、ID、URL 或偏好。现有 observed/requested/ready/no-pending/shown 统计继续描述各自节点，不将它们相加当作请求总数。新原因使用本节唯一命名；删除未被消费的旧取消/迟到别名，不维护双套键。
- 生命周期在实际状态变化时记录 disabled、source_hidden、navigation，重复关闭/隐藏或 DOM mutation 不重复记录状态变化；navigation 以新 pageKey 为界，navigate-finish 不再计一次。原因只通过回调交给组合根，不让生命周期依赖日志或接管请求状态。
- stream 在丢弃未发定时任务时记录 cancelled_before_send；取消唯一在途请求时记录 cancelled_in_flight；旧世代 Promise 返回时记录 late_response。取消动作与后来返回是两个事实，不把它们重复计为 timeout 或 network。hash 待决因失效放弃时单独计 cancelled-acquisition；已结束的 Promise 不冒充在途取消，计数不增加资料采集。
- 实际被接受的失败响应按 timeout、aborted、network、invalid-request、rejected 分类；HTTP 503 单独映射 backend_unavailable，不读取或转印错误正文；成功 HTTP 的 JSON/结构绑定校验失败在诊断中归 protocol_mismatch，不等同于实际软件版本不匹配。后者的版本协商属于发布任务。成功响应 hints 为空时恰好记录一次 no_hint，即使视图仍保留已有提示，也不计为超时或本机抑制。
- background 仅解析 Server-Timing 的 query/rules/api，合法范围 0–60000ms，未知字段和 desc 不进入诊断。缺失、无效、重复的维度均省略；同一维度重复时不任选一个值。stream 对每个接受的成功响应，只要任一必需维度缺失就记录一次 missing-server-timing；合法的其他维度仍记录，未测量绝不补零，transport 与 query 保持不同节点。
- render 记录实际绘制耗时；endToEnd 从当前字幕被观察到该序号首次 ready/no-pending/fallback 计一次，旧行封版二次发布和偏好/布局重绘不再计数。shown 与 suppressed 按该字幕序号去重；只有确实存在提示且全部被本机偏好抑制，才记录 suppressed，无提示不冒充抑制。HUD 的固定聚合字段保持可消费，不附带正文或 request_id。
- background 普通输出只含固定 stage/outcome/耗时；清除超时和在途引用不依赖 console 成功，console 异常不得改变已确定响应。诊断回调本身故障也不得改变调度、取消、英文或提示结果，不自动重试请求。
- 直接回归覆盖上述精确次数、超时与主动取消/迟到分离、503与坏响应、部分/缺失/重复 Server-Timing、保留提示下的无新提示、本机抑制、重复终态渲染、诊断故障隔离。使用真实模块对象与合成 content bundle；不据此宣布真实 YouTube 或最终性能预算通过。
