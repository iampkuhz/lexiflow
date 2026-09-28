# 1. 设计输入与冻结要求

## 1.1. 分类、提示资格与预热资格

分类不读取个人观看、掌握度或偏好状态。导入行现有的 `sourceOxfordBasic`、`sourceBncRank`、`sourceFrqRank`、`sourceComplexTags`、`priority`、`allBasicPhrase`、`curatedGloss` 与来源引用是可审计证据；不以字段缺失推造证据，不改 `LexiconPriority` 的既有计算或范围。

| 维度 | 冻结规则 | 证据与边界 |
|---|---|---|
| 词段类型 | `lemma` 是单词则按基础词名单独判断；别名和屈折形按已冻结的单词规则继承该词条资格。短语不继承组成单词的 `basicVocabulary`，另有 `allBasicPhrase` 证据。 | 原始来源、词元/词形及来源身份保留；不得把不同词条同形合并成一个词义。 |
| 基础词 | 维持现有 Oxford 基础名单与固定功能词规则；只对可核对的名单/标记生效。命中基础词的单词及其词形不提示。 | 基础表示公开资料分类，不表示用户熟练；其他来源无对应证据时不能套用 Oxford 标记。 |
| 完整短语 | `allBasicPhrase` 和 `lowInformationPhrase` 是短语启发式阻断，不把每个词都基础化。仅 `curatedGloss=true` 且明确绑定完整 `lemma` 的人工核对释义，可越过这两项启发式。普通 `sourceGloss`、清洗后安全短释、自动切分或来源已有默认义都不等于人工核对。 | 未核对的全基础短语或低信息短语继续不提示；`safeGloss` 只是结构/字符安全，不证明整短语语义正确。人工核对不得自动生成白名单。 |
| 释义与来源质量 | 保留确定性 cleaner 与来源首义规则。非人工核对项按来源既有顺序取首义并应用现有确定性清理，不因首义不合法而悄悄改取后义；`curatedGloss` 只能取经校验绑定该完整短语的短释。 | 来源缺失/不允许、查询长度越界、无合法默认义、来源身份冲突、同形候选冲突等硬校验不能被 curated 短释越过；越界形态保留具体拒绝原因。 |
| 频率与重要度 | 区分基础、重点、长尾、低频和未知频率。重点排序继续使用 `LexiconPriority` 现有公开频率/复杂词表证据，不加新阈值或新评分。低频和缺失来源排名本身不构成提示阻断。 | 缺排名仍标记未知，不伪造为零频率的事实；不从浏览或点击推断熟悉度。 |
| 提示与缓存 | 发布时分别决定 `HINT/BLOCK` 提示动作、优先级与正/负缓存预热资格；是否预热不改变按需查询资格。 | 任何提示/阻断候选都附发布版本；缓存预算是实现配置，不是资格阈值。 |

`curatedGloss` 的唯一受信生产者为 `StardictCsvReader` 的受审查 `CURATED_GLOSSES` 映射：按 `ecdict-stardict` 来源、英文和规范完整 lemma 精确匹配，并验证原始来源表达仍包含映射中要求的原文。来源表达变化立即拒绝导入；普通规范 CSV 恒为 false，不提供可自行填写的 curated 开关。二期不自动扩充该映射，不从清洗结果生成豁免。发布预处理策略须绑定映射内容摘要，保留来源引用与决定原因；新增人工映射须经来源核对和代码审查，而不是由运行时用户输入提升权限。现有映射已包含 `stream of data`，完整短语豁免使用该受信边界，不依赖一个无来源的布尔值。跨来源、同 lemma 不同来源表达及伪造 CSV 字段作为适配器拒绝用例；没有受信映射的短语不得获得豁免。

确定性判定顺序为：来源/词形身份、观看查询长度及合法词形 → 单词基础词硬阻断 → 默认义合法性（人工核对值须与完整短语绑定）→ 对短语应用 `allBasicPhrase` 与低信息形状启发式（仅完整短语人工核对可越过）→ 保留原始同形候选冲突证据 → 发布提示动作。任何更早的来源、长度、默认义、身份/同形冲突硬校验均不被人工核对豁免；具体数据质量原因与提示动作分别记录。既有评分只排序，不覆盖硬阻断。

## 1.2. 预处理、启动与同步主线

### 1.2.1. 离线资料准备

来源解析 → 来源和词形身份校验 → 基础/频率/复杂词表证据整理 → 确定性短语与默认义质量判断 → 生成提示动作及预热投影 → 校验发布身份、完整性并原子发布。

StarDict 与规范 CSV 各自解析，进入统一资料合同；保留来源，不伪造缺失频率或英文定义。同形不同词条保留全部候选。清洗规则只运行于离线准备；观看只读取已发布词形投影，不清洗词典、不重排来源候选、不重算名单。默认首项不合格不得静默以后项补位。资料发布和显式开发库重建分开；只更新最新 SQL，先隔离验证，服务启动绝不隐式导入、清库或重建。

### 1.2.2. 服务启动与运行模式

正式运行默认必须有已校验发布资料和可用数据库；缺发布版本或依赖不可用时返回不可就绪，拒绝提示查询但保留英文字幕。只有显式选择的 demo 模式可装配内置演示词库，启动与就绪状态明确标为 demo，不作为正式资料可用。配置错误、资料身份/策略不匹配均不可伪装就绪。

启动顺序固定为：读取显式运行模式 → 校验配置与资料包身份/策略版本 → 建立只读发布查询 → 读取发布版本 → 在版本绑定下清理正负缓存并按预算预热 → 发布就绪或不可就绪状态。版本变化同时清除正、负缓存，之后再装载新版本；不得混用版本候选。预热是尽力优化：单项/部分预热失败记为降级并保留可查询服务；只有数据库/发布资料查询依赖本身不可用才拒绝提示链，不把 warmup 的部分失败冒充业务请求失败。当前 4000 总容量、2000 正向预热、512 负向预热仅为既有配置起点，须在实现核对，不承诺性能指标或吞吐收益。

### 1.2.3. 当前字幕处理与词库端口

Enrichment 是字幕候选键的唯一生成者。对经现有 `LexiconSurfacePolicy.queryTokens` 规范化的字幕，按原顺序枚举每个起点连续 1 至 3 个 token，形成去重 `List<String>`；不得穷举更长短语。它调用公开 `LexiconCatalog.lookupForms(List<String> normalizedForms)`，接收不可变 `LexiconLookupResult`（候选、可选发布身份、本次查询计数）。接口校验非 null、非空白、已规范词形及至多三词的键；先防御性复制调用方列表，拒绝无效键，不暴露 `LexiconRepository`。空列表立即返回空候选、未知发布身份和零次访问计数，不触发存储访问。非空查询无发布资料时返回明确版本 0，与未知版本不同。候选列表不可变，保持完整同形/版本冲突证据。查询计数随本次结果返回，经 Enrichment 累加交给 API，不使用全局累加器、ThreadLocal 或读后清零。

规范化责任固定：Enrichment 调用既有 `LexiconSurfacePolicy.queryTokens` 后再枚举；Lexicon 只校验传入键已等于这些 token 用单空格连接的结果，不二次“修复”无效输入。合成断言至少包含 `Don't STOP` → `don't`、`stop`、`don't stop`，`co-operate` → `co`、`operate`、`co operate`，Unicode 字母按现有 Alphabetic 边界保留。查询 token 是查表键，不是定位坐标；位置匹配必须回到原始字幕，不能用规范化字符串下标替代 UTF-16 原文下标。

该端口切换是同一原子切片：`LexiconCatalog` 合同、`CachedLexiconQueryService` 与 `BuiltinLexiconCatalog` 两实现、`EnrichCaptionUseCase` 和直接单测一并迁移；调用者负责字幕转 key，catalog 仅对精确词形批量查询。写入/资料发布 Repository 与只读查询端口的拆分是独立切片，不借本次接口变更顺带重构。

字幕处理顺序如下：

1. 校验请求长度、快照结构、片段身份、范围和新增内容；
2. 计算连续新增区间，保留分组词边界及 UTF-16 坐标；旧片段不重复查询；
3. Enrichment 生成并去重 1–3 token 查询键，空键不访问 Lexicon；
4. 绑定发布身份，读取版本一致的正/负缓存并批量查询缺失词形；拒绝混合版本；
5. 定位精确词形候选，使用已发布动作和首义，核对响应有效性；保留阻断及同形歧义证据；
6. 执行冲突、排序、重复与重叠选择，映射回片段 key/局部偏移；
7. 返回处理覆盖和提示；客户端再校验生命周期、字幕身份、迟到结果及本机显式抑制偏好后展示。

静态资料质量在预处理；请求范围、版本及响应有效性仍在运行期校验。英文字幕不等待提示链。当前服务字幕的 Promise/线程仍属于同步业务关键路径，不新建队列、worker 或后台成功假象；异步化需另定持久交接与失败合同。

## 1.3. 代码职责与所有者

| 现有实现 | 冻结职责 | owner 与切换边界 |
|---|---|---|
| `HintPreparation`、`BasicVocabulary`、`LexiconPriority`、`LexiconImportRow` | 离线分类、质量/首义资格及排序证据；提示动作与预热资格分开 | LEX；不引入个人状态或新增评分阈值 |
| `LexiconSurfacePolicy`、`LexiconCatalog` | 共享规范化词元边界；精确词形批量公开合同 | LEX 定义契约，ENR 生成 key |
| `CachedLexiconQueryService`、`BuiltinLexiconCatalog` | 只查精确词形、管理版本绑定正负缓存；demo 实现明确隔离 | LEX；与端口、Enrichment 同切片 |
| `EnrichCaptionUseCase`、`DeterministicHintPolicy` | 增量区间和 key 生成、结果坐标映射；消费已发布动作、冲突与非重叠选择 | ENR；不把查询生成职责留给 Catalog |
| `LexiconRepository`、`DefaultLexiconRepository` | Repository 仍按现状提供当前查询/导入消费者；查询/发布端口拆分另行定义 | LEX、DAT；本次不捆绑拆分 |
| `LexiconImportMain`、`LexiconImportService` | CLI/资源适配、来源读取、预检与发布用例装配 | LEX、OPS；不把资料导入放进 API 启动 |
| `ApiApplication`、`CaptionHintController` | 唯一组合根装配模式、发布查询和状态；HTTP 仅映射合同/观测 | API；正式无资料不可就绪，demo 必须显式 |
| `SegmentAnalysisLog` | 授权分析记录与普通日志分离；同步端口调用 | OBS 实现，API 装配；失败不改变提示业务结果 |
| `content.ts`、`stream.ts`、`overlay.ts` | 页面生命周期、快照采集、请求协调、展示和本机显式偏好 | EXT；不传输或推断偏好 |

以上符号需在任务正式启动时复核，但不得借“复核”扩展到无消费者的通用工作流、模型、队列或第二服务进程。跨模块只通过公开合同，依赖仍由组合根指向 application 再指向 domain/ports。

## 1.4. 日志与观测合同

事件名、字段、级别、固定原因码、打印位置和合成样例以[观测合同](observability.md)为准；不在本页重复定义。普通日志与授权敏感台账分开，观测故障不改变提示业务结果。

功能分层、当前字幕和离线发布的交接见[架构与时序](architecture.md)，图由上述合同生成，不反向替代功能设计。

## 1.5. 验收与工作包冻结

- 每个实现 Task 包含直接测试；设计 Task 输出可独立核对的合同和合成样例，图在功能合同冻结后按文档 policy 渲染、实际查看再入正文。
- 覆盖基础词与词形、包含基础词的完整短语、可靠低频／无排名词段、噪声短语、非法默认义、同形冲突、跨片段范围、增量追加与重复、版本切换、冷缓存、取消／迟到、分析写入失败及发布回滚。
- 性能计数用于定位与避免明显退化，不把本阶段包装为已经取得专项提速收益；规则变更与行为保持的拆分分别出具证据。
- 准备阶段的检查只运行规划／文档／策略投影静态检查。正式实现后采用当时 Harness 的同输入验证链，验证与审查身份分离，真实观看验证与合成测试分开报告。
- 所有 Task 派发前按冻结合同收紧文件 claim；预计超出 90 分钟或 8 个主要产品文件时按当前 Catalog 规则拆分，不以大范围 glob 掩盖不可验收的工作包。

合成验收至少逐例固定输入证据、预期动作/原因与运行边界：

| 合成输入 | 预期结果 | 核对点 |
|---|---|---|
| 基础词 `the` 及其屈折/别名词形 | `BLOCK`，不提示 | 单词分类继承；无用户状态参与 |
| `take the lead`，有 `allBasicPhrase=true` 但 `curatedGloss=false` | `BLOCK` | 不从组成词推断整短语语义；普通来源释义不豁免 |
| 同一完整短语有与其 lemma 绑定且安全合法的 `curatedGloss=true` | 可越过 all-basic/低信息启发式；通过其余硬校验后才可 `HINT` | cleaner 及来源首义规则不被任意改写；缓存资格单独断言 |
| 同一短语 curated 但来源身份无效、超查询长度或默认义非法 | `BLOCK`，保留对应硬拒绝原因 | curated 不覆盖来源/长度/合法默认义检查 |
| 可靠低频词及无排名词 | 可按质量资格提示；无排名标为未知 | 低频/无排名不单独阻断，priority 数值不被重算 |
| 字幕 `A reliable phrase` | Enrichment 产生去重的一至三连续 token keys；Catalog 仅返回对应发布候选 | 精确规范词形、稳定候选/版本冲突证据、不可变输入/输出 |
| Catalog 收到空键列表或空字幕产生空列表 | 空候选且不触碰 Repository | 验证零存储访问 |
| 正负预热后发布版本改变；预热某些项失败 | 正负缓存一起清除；部分预热降级但不混版；数据库不可用则正式提示不可就绪 | 不把预热失败伪装为请求失败或 ready |
| 同一有效字幕分别走正式 API 与显式 demo | 正式无发布资料不可就绪；demo 明确标识且仅使用内置演示项 | 不把 demo 当正式词库，不影响英文字幕 |

## 1.6. 应用端口、结果及实现切片

### 1.6.1. 查询结果与计数

`LexiconLookupResult` 位于 Lexicon 的公开 domain.model，包含 `candidates`、`OptionalLong publishedVersion` 和嵌套不可变 `Counts`。Counts 包含 queryKeys、positiveHits、negativeHits、cacheMisses、dbBatches、versionReads、prewarmReads，均非负。positiveHits 是已有非空可提示候选的键数，negativeHits 是已缓存空结果或仅 BLOCK 候选的键数；混合 HINT/BLOCK 集合只算一个 positive key，但候选全部返回。前三级键计数针对去重输入，命中数加缺失数等于 queryKeys。dbBatches 只统计本次 findByForms 调用，版本查询和预热查询分别统计，不能将三者合称一条 SQL。

每次调用独立计数；启动预热不计入后续请求，请求触发的版本切换预热计入该请求。公开结果携带实际绑定版本；Enrichment 收集所有非空查询的发布身份，包括最终没有提示的查询，拒绝同请求混版，不等到选出提示才检查版本。失败通过固定领域错误映射 HTTP，不能伪造零次访问的成功结果。

原子切片包含 Catalog、查询/demo 两实现、查询结果值、CandidateForms、EnrichCaptionUseCase 及其两类 Measured result，共 8 个主要产品文件；改造相应直接测试与集成测试。组合根构造参数保持可编译；请求控制器仍可读取原有结果与计时，详细计数由后续 API 观测任务实际输出。删除旧字幕查询签名，不保留兼容入口。

### 1.6.2. 持久化角色与导入编排

`LexiconReadRepository` 仅包含 publishedVersion、findByForms、findPrewarmForms；`LexiconPublicationRepository` 仅包含 publish(metadata, sourceRowsTotal, expectedEntries, PreparedEntrySource)，消费已准备条目而非原始行，不对外暴露数据库连接。适配器内的 Repository 实现负责事务、映射和查询，两类业务消费者只依赖对应角色。

读写分离分两个可编译动作：先提取 publication role 并让既有 Repository 聚合它，同时将 LexiconImportService 收窄到 publication role；随后提取 read role，查询服务和 API 注入改为 read role。聚合 Repository 只保留给实际需要组合两角色的持久化工厂，不让业务依赖它。这是最终基础设施组合合同，不保留重复方法、旧实现或历史接口适配层。SQL 拆分不是读写分离的必要条件。

`LexiconReadRepository` 是读取合同唯一声明位置；聚合 `LexiconRepository` 不重新声明继承方法。`CachedLexiconQueryService`、包内 `VersionedLexiconCache` 与 API 的 `ObjectProvider` 均只接收 read role，查询测试替身不实现发布方法。`PostgresPersistenceConfiguration` 仍发布唯一聚合 bean，Spring 通过父接口分别提供 read/publication 角色；不创建第二份 Repository、连接池或事务状态。真实隔离数据库测试验证两角色解析到同一实例并完成发布后读取、混合候选返回与失败回滚；API 测试另以只读实现装配真实应用用例，证明不依赖写能力或偷偷回退 demo。服务就绪及 demo 默认策略仍由 API-2001 单独调整。

LexiconImportService 负责 metadata/来源计数校验、预检结果与重读来源的身份一致性和发布调用；来源解析/文件打开、用户重建确认和进度输出留在 CLI/适配器。导入与发布共享已准备结果，不在持久化映射时重复清洗。发布只在一个事务内使新资料可见，源变化或计数不符回滚，API 启动不执行导入。

流式来源 `LexiconImportRowSource.read` 在交付解析行后返回不可变 `ReadReceipt(sourceDigest, sourceRowsTotal)`；摘要由适配器对本次重读的来源生成，应用层不打开文件。导入用例包装该来源，在发布事务结束前核对 receipt 与预检 metadata 摘要、原始行数，并核对实际交付条数；不符立即抛错阻止提交。规范内存输入的 receipt 由已冻结请求生成。该返回值与 CLI、持久化及测试来源同批切换，不保留 void 或默认兼容入口。缓存状态、版本失效、正负预热和动态容量由包内 `VersionedLexiconCache` 管理，查询服务仅校验键、编排精确查询并组装本次结果/计数。

准备入口 `LexiconImportPreparation.inspect(metadata, rowSource, prewarmLimit)` 不依赖 Repository：按流执行单行准备和跨行 canonical 冲突校验，核对实际读取 receipt 与 metadata 摘要及原始/可导入计数，返回不可变的计数与有界预热报告。预热报告只保留资格为真且 memoryPriority>0 的前 limit 项，分数降序、同分 lemma 升序，不新增评分规则；limit=0 只校验。没有可导入条目可形成校验报告，但发布仍拒绝空资料。CSV 明确频率和 StarDict 缺排名的证据语义不变。

发布端口的嵌套 `PreparedEntrySource` 是 `read(long publishedVersion, Consumer<LexiconImportPlan.PlannedEntry>) throws IOException`：事务取得实际新版本后回调应用生产者。LexiconImportService 对每条原始行调用一次 prepareNext、维护跨分块 canonical 集合、共享该条 PreparedHint 给词义/追溯/查询投影，并在回调返回前校验 receipt 和全部计数。持久化实现仅映射准备结果、批写和事务/版本切换，不执行 prepare/fromRow 或重新清洗。内存输入 publish(request) 由应用服务转成冻结 row source 并走同一链；原始行版本的持久化 publish/publishStreaming 不保留兼容入口。

CLI 把 StarDict/规范 CSV 解析包装成同一 row source，每次实际读取后提供摘要/原始计数；应用预检通过后才打开发布资源，发布重读故障回滚。CLI 不决定 canonical 冲突、清洗、来源一致性或预热排序。两遍分别是预检和事务重读，各扫描只准备一次，不将全量 StarDict 的准备对象保存在内存里。来源的基础名单选择、格式统计、文件读取/摘要计算和打印留在适配器；basic-report 仍是显式来源报告。无库 validate/prewarm 的批次标识仅为 preview、许可为 unasserted-preview-only，不把诊断身份当发布来源。正式发布始终要求用户给出批次来源及许可。重建入口与精确确认保持，不把发布变成隐式重建。

### 1.6.3. 当前字幕、安全与展示

保留 CaptionIncrementalRequest 的现有输入上限与身份校验，不在本次拆分中放宽：先值类型/长度/数量，再片段 key 唯一性及双快照一致性，再计算连续新增区间。旧上下文最多同组同视觉行两个片段、48 个 UTF-16 单元，提示终点必须落在新增区间，不跨组。

Enrichment 内用实际被调用的组件分离 CandidateForms（查表键）、CandidateMatcher（原文定位）、PublishedCandidateEligibility（发布资料完整性）、冲突/排序/非重叠规则，以及增量区间/坐标映射。来源正文不会进入普通统计对象；原先 Measured result 的诊断文本拼装迁至授权分析适配器，普通结果只含业务结果与聚合计数。API 只负责映射、授权记录调用和终态事件，不重做选词。

CandidateMatcher 是领域内的原文定位组件，仅接收原始 caption、UTF-16 范围和已规范化精确词形；返回该范围内全部位置，不去重、不排序选择、不生成查询键、不判断可展示资格。大小写匹配及词边界沿用现有规则，边界读取完整原文，组合标记/下划线/字母数字不当成分隔，补充平面字符不得切半。重复出现、标点、大小写与区间截断均有直接测试。

PublishedCandidateEligibility 只消费已发布动作及最终短释：HINT 且短释符合现有安全显示合同才允许展示，BLOCK 不展示；24 个 code point 上限、汉字和非法字符防线保留，不裁剪、拆义、清洗或改写。观看不再调用 lowInformationPhrase 或重算基础/词频名单；来源短语资格归离线准备，因此经发布的安全 HINT（包括明确来源特许短语）不被再次筛掉。位置匹配必须保留全部候选，不安全短释与 BLOCK 仍参与同形不同条目的歧义判断。整批 null/混版拒绝、排序、重叠、同词条去重及 requiredEndAfter 规则由 DeterministicHintPolicy 保持；两个组件在该策略内实际调用，包内可见，不扩张跨模块 API。

增量应用处理由包内 IncrementalCaptionPlan 规划连续 append 区间及紧邻旧上下文，IncrementalHintMapper 将组内 UTF-16 半开范围映回片段 key，IncrementalResultAssembler 合并请求级覆盖、候选计数、查询计数、发布版本和词条去重。EnrichCaptionUseCase 只串联规划、查表、策略、映射合并与单调计时，不持有跨请求状态；空查询键不访问 Catalog。规划保持原有同组同行最多两个旧片段、48 个 UTF-16 单元预算，不截断旧片段，不跨组/视觉行查询或补词。完整组原文仅用于位置边界，不扩大查询范围。

合并器在资格选择/去重结果之外收集每次 lookup 的已知发布版本与全部候选版本；未知发布身份且无候选不引入版本，已知版本 0 与正版本不同。BLOCK、不安全短释、未匹配位置或被去重的候选也不能掩盖混版；多个版本时整请求撤销提示但保留全部处理 key、候选数、查询次数及耗时。同词条跨区间只展示首次选中的一处。HintSelection 拥有领域内的同范围不同词条歧义识别、现有稳定优先级排序、同词条去重和非重叠选择；不新增评分、数量上限或上下文选义。

MeasuredIncrementalCaptionResult 仅保留 result、queryNanos、rulesNanos、candidateCount 和 queryCounts；删除无生产消费者的 processedEnglish/processedWithHints 与诊断拼接，不保留兼容构造器或换名正文副本。现有授权 SegmentAnalysisLog 已直接消费 request/result，继续使用其逐段文本和 translated/untranslated ranges 合同；本切片无需再造整段 finalText 或修改 HTTP/日志输出。原有跨段 anchor、迟到补全仅记新后缀及失败不阻断测试继续运行。

扩展由 content 组合页面生命周期、采集源、快照协调、stream 请求状态机、overlay 显示和 preferences 本机显式抑制。stream 独占上次成功快照与唯一在途请求；生命周期只发失效信号，不再复制请求状态。失败不自动计时重试，下一次有效字幕变化才重试；旧行中文冻结、导航/关闭后迟到结果不可复活，英文始终不等待后端。


### 1.6.4. 发布证据持久化与数据库不变量

三表保持不变；准备事实与准确词形动作分开。`DefaultLexiconRepository` 从同一个 `PlannedEntry` 写入以下证据，不再次清洗或重算分类。审计通过 `lexicon_entry_id` 联结词形和准备结果，再读取单例 dataset 的批次摘要、许可、准备政策与版本；观看热路径仍只读查询投影，不加载来源正文。

| 保存位置 | 生产者 | 消费与目的 |
|---|---|---|
| prepared 的 `source_dictionary_id` | row.dictionary.sourceId | 与已有 source_gloss_ref 配对定位被采用释义，不把批次政策标识当词典身份 |
| prepared 的 `source_frequency_id`、`source_frequency_ref` | row.frequency 的来源与记录引用 | 定位频率证据，既支持 CSV 显式 Zipf，也支持 StarDict 排名来源 |
| prepared 的 `frequency_evidence` | prepared.classification.frequencyEvidence | SQL 区分 KNOWN/UNKNOWN；数值零不能代替缺失证据 |
| prepared 的 `decisive_rule`、`matched_rules` | PreparedHint 的决定规则与有序清洗轨迹 | SQL 追溯首项的变换与终态；阻断规则必须与 exclusion_reason 一致 |
| lookup 的 `final_decision_reason` | 通常继承 decisive_rule；超窗口词形固定 outside_query_window | SQL 解释每个词形的最终动作，不覆盖词条级成功或阻断证据 |

不复制可从现有事实准确得出的资格布尔值：prepared_gloss/exclusion_reason 表示提示资格，cache_priority 表示词形预热结果，来源基础标记与复杂词表证据保留；不能从 cache_priority=0 推断提示被阻断。

数据库拒绝空来源数组、source_row_count 小于 entry_count、空白标识/释义/原因、未知 frequency_evidence 枚举和缺失/空白规则。prepared 的阻断原因须等于决定规则；HINT 的词形原因不得为 outside_query_window。matched_rules 可为空数组但不能含 null 或空白规则；不限制为陈旧的固定规则枚举。SQL 不重新实现清洗算法，不用新增表、触发器或迁移链替代原子发布。

离线只读追溯查询（不由 API 启动执行；正文不进入普通日志）：

```sql
SELECT d.lexicon_version, d.preparation_policy, d.source_manifest,
       h.normalized_form, h.final_action, h.final_decision_reason,
       p.source_dictionary_id, p.source_gloss_ref,
       p.source_frequency_id, p.source_frequency_ref, p.frequency_evidence,
       p.decisive_rule, p.matched_rules, p.exclusion_reason, h.cache_priority
FROM lexicon_hint_lookup h
JOIN lexicon_prepared_entry p USING (lexicon_entry_id)
CROSS JOIN lexicon_dataset d
WHERE d.dataset_id = 1 AND h.language_tag = 'en' AND h.normalized_form = 'reliable';
```

直接回归使用真实隔离 PostgreSQL：发布合成正常/阻断/缺排名/显式零频率和超窗口别名，核对上述追溯结果；对坏决定和元数据逐条写入验证约束拒绝，事务失败后旧版本及其查询结果保持。源码结构变化需要显式开发库重建，但本任务不授权连接或重建真实运行资料。
