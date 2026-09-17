# 来源适配合同

> Proposed。先读 [横切合同导读](../phase-1-cross-cutting-contracts.md)。

让来源差异停在 adapter，核心只接收稳定 identity 和规范片段。

## 精确语义与后续证据

下面保留原合同的任务映射、Must/Should/Later、conformance、验收和未决假设。它们约束后续实现，不能从文档存在推断产品通过。

<!-- retained-contract:start -->
**对应任务：`LF-TSK-ADP-0001`**

Source Adapter 负责从具体来源获取并规范化内容。Content 接收来源无关的 canonical representation；Lexicon、Vocabulary、Learning、Semantic 和 Enrichment 不得依赖 YouTube DOM、web selector、PDF library、audio decoder 或平台 SDK 类型。

### 7.1 Canonical port semantics

Port 在概念上交付以下语义；这些是 contract value，不是实现方法签名：

| 语义 | Must invariant |
|---|---|
| Source identity | 同一来源对象在 adapter 可观察生命周期内稳定、不可由展示标题代替；source kind 只是 metadata，不允许核心据此分支平台逻辑。 |
| Source revision | 内容变化时可比较；无法得到原生 revision 时由 adapter 产生保守 fingerprint，并明确可靠度。 |
| Segment identity | 在 source revision 内稳定；重复采集同一 segment 得到同一 identity，内容变化或边界重切有新 revision/identity。 |
| Text unit | 保留原始语言、规范化文本、可选 speaker/track 信息与提取 provenance；不得伪造缺失 transcript。 |
| Source location | 表达时间范围、页/区域、文档顺序或选区中的可用部分；核心只消费通用 location capability，不解析平台私有 locator。 |
| Context window | 当前 segment 必需；previous/next 可缺失、迟到、乱序或不连续，并显式说明 completeness。 |
| Playback/reading state | 可选位置、速度、暂停/seek 或选区状态；只作为上下文/行为输入，不改变内容 identity。 |
| Adapter capabilities | 明确是否 live、timed、seekable、ordered、selectable、transcript-complete；缺少 capability 是合法状态。 |
| Provenance | 标明 adapter/source kind、contract version、采集时间、语言/track 与 normalization version；不携带页面凭据。 |

**Must**

- adapter 对同一输入确定性地产生 canonical identity、revision 和 normalization；结果顺序、重试和重复 callback 不改变 identity。
- 当前 segment 可独立交付。previous/next、时间、speaker、完整 transcript 或语言置信缺失时，不阻塞 English first，也不以空字符串冒充已知。
- source-specific metadata 留在 adapter-owned opaque reference；核心 Domain 不能导入、解析或条件判断它。
- adapter 不决定用户是否需要 hint，不读取 Vocabulary Profile，不调用具体 Semantic Provider，也不产生 Learning familiarity。
- page/document/audio 内容按不可信数据规范化，限制大小与窗口；隐藏脚本、控制指令和富标记不能越过 port 成为执行语义。
- source revision 或 segment binding 不能证明时，结果使用保守的新 identity/revision，使旧 annotation miss，而不是冒险复用。
- 无字幕/转写时明确报告 content unavailable/capability absent；不得猜测文本。

**Should**

- 支持增量片段和 backfill，使 live 来源不必等待完整 transcript。
- 将重叠、断句变化和 seek 后乱序归一成可测试事件，允许 Content/Extension 做去重和 stale reject。
- accessibility text、官方 caption 或用户明确选区优先于不可解释的页面抓取；provenance 保留来源质量等级。

**Later**

- Phase 5 实现 YouTube Extension adapter；网页、PDF 与 audio/podcast adapter 只在 ADP 后续 research task 证明产品价值后激活。
- 具体抓取 API、DOM selector、PDF parser、ASR 方案和平台权限均由各 adapter 阶段决定，不进入 core contract。

### 7.2 Conformance examples

| Fixture | Adapter 输入能力 | Canonical 输出期望 | 缺失/变化处理 |
|---|---|---|---|
| YouTube recorded video | timed caption track、player position、video identity；caption callback 可能重复或重切。 | current timed segment、稳定 source/revision/segment、可选 previous/next、`live=false`、`timed=true`。 | 无 next 时显式 incomplete；seek/track/revision 变化使旧 result stale；DOM/track 私有对象不越界。 |
| YouTube live | 逐步出现的 timed captions、窗口持续滚动。 | 增量 current segment、单调可比较的 adapter revision/provenance、有限 context window、`live=true`。 | 文本修订产生可比较 revision；不等待直播结束；迟到增量不覆盖当前 segment。 |
| Web article/selection | document identity、阅读顺序、段落或用户选区，无可靠播放时间。 | ordered text segment、document/selection location、current + 可选相邻块、`timed=false`、`selectable=true`。 | DOM 重排但文本/identity 等价时保持 canonical；内容实质变化更新 revision；脚本/导航噪声不作为文本。 |
| PDF page/selection | document fingerprint、page/order、选区或提取文本，可能多栏/扫描。 | page/order location、规范化 text segment、提取 provenance、可选相邻段。 | 无文本层时报告 capability absent 或待外部转写；不制造阅读顺序；新 PDF revision 不复用旧 context。 |
| Audio/podcast transcript | episode identity、timed transcript/ASR segment、playback position。 | timed segment、transcript provenance/quality、有限前后文、`timed=true`、`seekable` 按来源声明。 | 无 transcript 时 content unavailable；ASR 修订更新 revision；低置信文本被标记而非当作确定字幕。 |

这些 fixture 只证明同一 port 能承载来源差异，不承诺 Phase 1 实现未来来源。

### 7.3 Cross-source conformance suite

**Must**

- 每种 fixture 通过同一组 canonical contract assertions：identity 稳定、revision 可比较、current 必需、邻居可选、location capability 可缺、语言/provenance 明确、大小有界。
- suite 覆盖重复、乱序、缺 previous/next、segment overlap、seek、source revision、未知语言、无 transcript 和超长输入。
- core fixture 只使用 canonical values；任何断言若需要 YouTube video object、DOM node、CSS selector、PDF page class 或 audio decoder 类型，即判为 adapter 泄漏。
- 同一 canonical Content Context 进入 Enrichment/Learning 时，不因来源名称改变 need-hint、Profile owner 或 event 语义。
- conformance fixture 使用合成文本和身份，不含真实用户字幕、观看历史或受版权限制的完整材料。

**Should**

- contract suite 由所有 source adapter 复用，adapter 可添加自己的 acquisition tests，但不能删除共享 assertions。
- 建立 source-neutral golden context window，证明 timed 与 untimed 来源都能触发相同的 fast/slow enrichment contract。

**Later**

- `LF-TSK-ADP-0002` 在 Phase 5 用 dependency/import gate 证明 core 无 YouTube 类型；后续 adapter research 各自提交 conformance receipt。

### 7.4 Acceptance evidence

`LF-TSK-ADP-0001` 的 `PASS` 证据至少包括：

1. 一份 source-neutral port review，覆盖 identity、revision、segment、location capability、context completeness、language 与 provenance，且无平台 SDK/DOM 类型。
2. YouTube recorded/live、web、PDF 与 audio 五类合成 fixture 的期望结果，证明 timed 与 untimed、完整与部分 context 均可表达。
3. determinism/property evidence，证明重复/乱序输入可去重，source revision 或实质内容变化不会误命中旧 annotation。
4. forbidden-dependency evidence，证明 Content 以外的 core Domain 不按 source kind 分支，也不依赖 Chrome、YouTube、网页、PDF 或 audio 实现类型。
5. no-text journey，证明字幕/transcript 不可用时系统明确降级且不猜测内容，English first/原来源体验继续。

### 7.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| ADP-A1 | `identity + revision + segment + optional location` 足以统一 timed 与 untimed 来源。 | Phase 5 YouTube 实现和后续 web/PDF spike。 |
| ADP-A2 | 三段式有界 context 是初期质量/隐私/成本的合理折中。 | Phase 4 Semantic evaluation。 |
| ADP-A3 | YouTube caption callback 可构造稳定 segment identity，即使平台重切断句。 | Phase 5 Extension spike；失败则使用保守 revision 而非放宽 stale 检查。 |
| ADP-A4 | Web/PDF/audio 只需 source-specific acquisition，核心 Learning/Enrichment 语义无需分叉。 | 每个未来 adapter 的 conformance review。 |
<!-- retained-contract:end -->
