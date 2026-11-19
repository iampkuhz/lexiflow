# 1. 有界术语工程候选

Lexicon 的有界目录在 `backend/product/adapters/src/main/resources/lexicon/reviewed-phrases.tsv`，自编短中文只用于完整精确词段，不改变组成单词的默认义。正常 Java 离线发布消费 `content_review=REVIEWED` 的条目，并只声明同一已发布子集的补充来源。PENDING 不发布；缺事实/许可/真实审核依据或内容漂移失败关闭。没有匿名上传、观看期追加或 CLI true 放行接口。

新增候选可以与已核子集同表存在，独立审核仅绑定其明确列出的主体；待审条目不能通过改状态复用其他条目的证据。`reviewBasis` 须与实际核查记录及发布行依据一致。

目录 SHA 是内容完整性锁，不是身份认证或批准签名。`reviewed-phrases-review.properties` 分别记录原审核报告摘要、公开摘要字节的 SHA-256、原受核输入摘要及七条受核主体的摘要与顺序。`reviewed-phrases-review.md` 是只含术语事实和许可边界的编辑摘要，不是独立审核原件；原件与本机运行数据不进入 Git。公开摘要不重签原件，也不构成人类确认、法律意见、独立开发检查或正式发行批准。

## 1.1. 独立事实与中文整理

| 词段 | 中文短释 | 独立事实依据 | 整理与审核依据 |
| --- | --- | --- | --- |
| neural tissue | 神经组织 | NCI 的 [neural](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/neural) 与 [tissue](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/tissue)：分别指神经系统相关及细胞构成的组织 | 生产者据独立事实整理；TASK-120 独立 AI 核查限定中文 |
| fatty tissue | 脂肪组织 | NCI 的 [Fatty breast tissue](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/fatty-breast-tissue) 与 [Connective Tissue](https://training.seer.cancer.gov/anatomy/cells_tissues_membranes/tissues/connective.html)：脂肪性组织属于生物组织 | 生产者据独立事实整理；TASK-120 独立 AI 核查限定中文 |
| net force | 合力 | NASA Glenn 的 [Newton’s Laws of Motion](https://www1.grc.nasa.gov/beginners-guide-to-aeronautics/newtons-laws-of-motion/)：外力相互抵消时 net force 为零，而不是物体“网” | 生产者据物理事实整理；TASK-120 独立 AI 核查限定中文 |

实际资料整理者在目录 `curator` 列，独立内容核查者在审核记录，二者不同。不要求用户逐词答复；也不冒称已经收到人类答复。显式复数词形沿用同一限定术语义，不推导其他 alias、派生词或新语境。修改内容主体后必须重新核查并精确绑定证据，不能只改摘要假称审核完成。以上不保证单独 tissue/net 或更广多义词消歧；原始错例及未覆盖风险在任务交接中保留。

同一路径还包含 [NCI nerve cell](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/nerve-cell)（神经细胞）和 [NCI spinal cord](https://www.cancer.gov/publications/dictionaries/cancer-terms/def/spinal-cord)（脊髓）；二者由实际资料核查 actor 据定义整理，沿用 NCI 文本复用与署名边界。

## 1.2. 来源许可与署名

`LicenseRef-NCI-Text-Reuse` 指 [NCI Reuse of Information](https://www.cancer.gov/policies/copyright-reuse) 中未另行标注的文本复用条件。本目录只使用事实及自编极短中文，不复制完整词典、图片、标志或字幕。事实来源：National Cancer Institute；中文表达根据 NCI 信息整理。The National Cancer Institute (NCI) does not endorse this translation and no endorsement by NCI should be inferred.

`LicenseRef-NASA-Factual-Use` 指 [NASA Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/) 的 factual use、署名及不得暗示背书边界。事实来源：NASA Glenn Research Center；中文由 LexiFlow 自行整理。NASA 不为本产品或中文表述背书。不使用 NASA 标志、图像、第三方版权内容或完整文章。

来源清单分别记录 NCI、NASA 子集：每个摘要为 UTF-8 原始表头加 LF、该许可子集的有序原始行（每行含末尾 LF）。整份目录另有精确字节内容锁，并纳入 generation code identity；原始 StarDict 摘要和 MIT 许可不被补充料冒用。资料包导出时，所有来源均须有匹配 ID、摘要、许可和 HTTPS 依据的显式复用批准记录；这不是自动法律许可判定，也不证明人类签字或授予正式发行资格。

## 1.3. 竞争与边界

Enrichment 在同区间歧义、安全和 Top N 过滤之后，先处理完整合格短语，再处理它完全包含的单词；仅此包含关系增加排序约束。无包含关系、短语之间与交叉匹配沿用原 priority、长度和稳定 tie-break。失败短语不提前删除内部单词。候选数据仍 rank=null、priority=0，不预热；不伪造词频来争胜。

观看只读取已经发布的词形与短释；仍限 1–3 词和既有同组同行增量边界。未发布、不完整、BLOCK 或同形歧义的短语不压制组成词；因此未覆盖语境仍可能出现旧默认义。词库截图、合成测试与真实观看验收是三个不同对象。


## 1.4. 独立来源支持的补充词段

`nerve fiber` / `nerve fibers` 的自编短释为“神经纤维”；`fatty substance` / `fatty substances` 为“脂肪性物质”。事实依据为 [NCI SEER Nerve Tissue](https://training.seer.cancer.gov/anatomy/nervous/tissue.html)，其神经元结构与 myelin 描述分别支持限定含义，沿用上述 NCI 文本复用与署名边界。

`fatty substances` 亦可在 [Oxford substance](https://www.oxfordlearnersdictionaries.com/definition/english/substance) 的独立用例中核对，表明它不是某视频专有拼接。这里只引用链接核查搭配，不复制 Oxford 词典正文、中文译文或将其许可冒充 NCI。两项均纳入同一七主体内容审核；fatty substance 仅是完整描述性搭配，不是医学专名或髓鞘同义词。Oxford 仅核到官方页面索引，直读 403，不声称完整读取或语料频率。两个词段均不泛推孤立 nerve/fatty，也不把 fatty substance 等同 myelin；具体审核与发布状态由目录及实际审核记录决定。
