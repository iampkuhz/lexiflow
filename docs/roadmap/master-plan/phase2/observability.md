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
| lexicon.import.stage | 每个固定导入步骤开始和结束 INFO；长步骤沿既有有界心跳，不另设逐行日志 | input_rows、prepared_rows、hint_rows、blocked_rows | STARTED、OK、SOURCE_INVALID、SOURCE_CHANGED、CANCELLED、DEPENDENCY_UNAVAILABLE |
| lexicon.import.completed | 每次导入一次终态；成功 INFO，明确取消 WARN，失败 ERROR | input_rows、prepared_rows、lookup_rows、blocked_rows | OK、CANCELLED、SOURCE_INVALID、SOURCE_CHANGED、PUBLISH_ROLLED_BACK |
| lexicon.cache.version_changed | 观察到资料身份变化并清理缓存后一次 INFO | invalidated_positive、invalidated_negative | VERSION_CHANGED |
| caption.request.completed | 每次 HTTP 请求恰好一次终态；正常含空提示 INFO，无效请求 WARN，内部失败 ERROR | new_ranges、query_keys、positive_hits、negative_hits、cache_misses、db_batches、version_reads、prewarm_reads、candidates、selected、ambiguous、overlap_dropped | OK、NO_HINT、NO_NEW_SEGMENTS、INVALID_REQUEST、NO_PUBLISHED_DATA、VERSION_CONFLICT、DEPENDENCY_UNAVAILABLE、INTERNAL_ERROR |
| runtime.dependency.changed | 依赖从可用转不可用或恢复时打印；故障 WARN，恢复 INFO；同状态不重复 | 无 | DEPENDENCY_UNAVAILABLE、RECOVERED |
| analysis.record.failed | 一次请求敏感记录写入失败时最多一次 WARN；提示业务结果保持独立 | attempted_segments | ANALYSIS_WRITE_FAILED |

db_batches 仅为缺失词形批量读取次数，版本读取与预热分别记录，不能把缓存命中请求描述为零次数据库访问。

导入 stage 另有固定 `step` 与 `phase` 字段：step 为 source_check、prepare、persist、publish；phase 为 started、heartbeat、completed。只有 completed 可携带步骤最终数量；开始和心跳不冒充完成百分比。导入终态可追加有界 `reason_counts`，键只来自预处理规则枚举，不按词条分桶。

原因与结果关系固定：NO_HINT、NO_NEW_SEGMENTS、DEMO_MODE、PREWARM_DEGRADED 不让业务结果变为 FAIL；NO_PUBLISHED_DATA、DEPENDENCY_UNAVAILABLE 表示 BLOCKED；非法输入及违反版本/结构合同为 FAIL。schema 不合或内部异常不得冒充正常空提示。

请求 terminal 在控制器的统一终态出口输出，包括反序列化/校验失败的入口异常映射。业务组件只返回结构化计数与原因，不直接打印字幕或重复请求终态。导入进度的交互确认文本与结构化事件分开，等待人工输入不发执行心跳。

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
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"PASS","reason":"OK","duration_ms":7,"request_id":"synthetic-request-1","lexicon_version":7,"counts":{"new_ranges":1,"query_keys":3,"positive_hits":1,"negative_hits":1,"cache_misses":1,"db_batches":1,"candidates":1,"selected":1},"timings_ms":{"validation":0,"candidates":0,"query":3,"selection":1,"analysis":1,"api":7}}
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"PASS","reason":"NO_HINT","duration_ms":2,"request_id":"synthetic-request-2","lexicon_version":7,"counts":{"new_ranges":1,"selected":0},"timings_ms":{"api":2}}
{"schema":"lexiflow.event.v1","event":"caption.request.completed","stage":"request","result":"BLOCKED","reason":"DEPENDENCY_UNAVAILABLE","duration_ms":5,"request_id":"synthetic-request-3","counts":{"new_ranges":1},"timings_ms":{"query":4,"api":5}}
```

以上数字和身份仅用于合同示例，不是实测结果。直接测试必须证明：成功/空提示/非法请求/依赖故障各一次终态；版本变化一次失效事件；敏感记录故障仍返回既定提示；取消与迟到计数不混淆；未测量字段不伪造；注入换行、凭据样式及合成字幕均不能进入普通事件；日志故障不改变业务响应。
