-- LexiFlow 最新完整词库：来源资料、准备结果、观看查询投影共三表。
-- 本文件只用于显式初始化空 schema；结构变化须在隔离环境重建并重导。

CREATE TABLE lexicon_dataset (
    dataset_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (dataset_id = 1),
    lexicon_version BIGINT NOT NULL CHECK (lexicon_version > 0),
    source_manifest JSONB NOT NULL CHECK (jsonb_typeof(source_manifest) = 'array'),
    source_row_count BIGINT NOT NULL CHECK (source_row_count > 0),
    entry_count BIGINT NOT NULL CHECK (entry_count > 0),
    lookup_count BIGINT NOT NULL CHECK (lookup_count >= entry_count),
    preparation_policy TEXT NOT NULL CHECK (preparation_policy <> ''),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lexicon_prepared_entry (
    lexicon_entry_id UUID PRIMARY KEY,
    language_tag TEXT NOT NULL CHECK (language_tag = 'en'),
    lemma TEXT NOT NULL CHECK (lemma <> ''),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN ('word', 'phrase')),
    source_gloss TEXT NOT NULL CHECK (source_gloss <> ''),
    source_gloss_ref TEXT NOT NULL CHECK (source_gloss_ref <> ''),
    source_bnc_rank BIGINT CHECK (source_bnc_rank IS NULL OR source_bnc_rank > 0),
    source_frq_rank BIGINT CHECK (source_frq_rank IS NULL OR source_frq_rank > 0),
    source_complex_tags TEXT[] NOT NULL DEFAULT '{}',
    source_oxford_basic BOOLEAN NOT NULL DEFAULT FALSE,
    prepared_gloss TEXT,
    exclusion_reason TEXT,
    prepared_priority INTEGER NOT NULL CHECK (prepared_priority BETWEEN 0 AND 1000),
    frequency_zipf NUMERIC(3, 2) NOT NULL CHECK (frequency_zipf BETWEEN 0 AND 8),
    complex_list_count SMALLINT NOT NULL CHECK (complex_list_count >= 0),
    CONSTRAINT lexicon_prepared_entry_language_lemma_uk UNIQUE (language_tag, lemma),
    CONSTRAINT lexicon_prepared_entry_decision_ck CHECK (
        (prepared_gloss IS NOT NULL AND exclusion_reason IS NULL)
        OR (prepared_gloss IS NULL AND exclusion_reason IS NOT NULL))
);

CREATE TABLE lexicon_hint_lookup (
    language_tag TEXT NOT NULL CHECK (language_tag = 'en'),
    normalized_form TEXT NOT NULL CHECK (normalized_form <> ''),
    lexicon_entry_id UUID NOT NULL REFERENCES lexicon_prepared_entry (lexicon_entry_id) ON DELETE CASCADE,
    form_kind TEXT NOT NULL CHECK (form_kind IN ('lemma', 'alias', 'inflection')),
    canonical_lemma TEXT NOT NULL CHECK (canonical_lemma <> ''),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN ('word', 'phrase')),
    final_action TEXT NOT NULL CHECK (final_action IN ('HINT', 'BLOCK')),
    final_gloss TEXT,
    final_priority INTEGER NOT NULL CHECK (final_priority BETWEEN 0 AND 1000),
    final_sense_id UUID,
    final_frequency_zipf NUMERIC(3, 2) NOT NULL CHECK (final_frequency_zipf BETWEEN 0 AND 8),
    final_complex_list_count SMALLINT NOT NULL CHECK (final_complex_list_count >= 0),
    cache_priority INTEGER NOT NULL CHECK (cache_priority BETWEEN 0 AND 1000),
    CONSTRAINT lexicon_hint_lookup_pk PRIMARY KEY (language_tag, normalized_form, lexicon_entry_id),
    CONSTRAINT lexicon_hint_lookup_decision_ck CHECK (
        (final_action = 'HINT' AND final_gloss IS NOT NULL AND final_sense_id IS NOT NULL)
        OR (final_action = 'BLOCK' AND final_gloss IS NULL AND final_sense_id IS NULL))
);

CREATE INDEX lexicon_hint_lookup_prewarm_idx
    ON lexicon_hint_lookup (language_tag, final_action, cache_priority DESC, normalized_form)
    WHERE cache_priority > 0;
CREATE INDEX lexicon_hint_lookup_entry_idx ON lexicon_hint_lookup (lexicon_entry_id);

COMMENT ON TABLE lexicon_dataset IS '当前完整词库的单行来源与发布元数据；无导入生命周期状态';
COMMENT ON TABLE lexicon_prepared_entry IS '来源归并后按语言与主词形唯一的离线准备结果';
COMMENT ON TABLE lexicon_hint_lookup IS '观看阶段按准确词形批量查询的最终提示或阻断投影';
COMMENT ON COLUMN lexicon_dataset.dataset_id IS '固定为1的单例数据集主键';
COMMENT ON COLUMN lexicon_dataset.lexicon_version IS '完整重导时递增的已发布资料版本';
COMMENT ON COLUMN lexicon_dataset.source_manifest IS '来源标识、许可、摘要与取得时间组成的清单';
COMMENT ON COLUMN lexicon_dataset.source_row_count IS '原始输入扫描的行数';
COMMENT ON COLUMN lexicon_dataset.entry_count IS '已准备主词条总数';
COMMENT ON COLUMN lexicon_dataset.lookup_count IS '包含原形与派生词形的观看投影总行数';
COMMENT ON COLUMN lexicon_dataset.preparation_policy IS '本次数据准备采用的冻结规则标识';
COMMENT ON COLUMN lexicon_dataset.imported_at IS '完整写入事务提交前记录的导入时间';
COMMENT ON COLUMN lexicon_prepared_entry.lexicon_entry_id IS '由语言及规范主词形生成的来源无关稳定身份';
COMMENT ON COLUMN lexicon_prepared_entry.language_tag IS '词条语言标签，当前固定英语';
COMMENT ON COLUMN lexicon_prepared_entry.lemma IS '规范化主词形或完整短语';
COMMENT ON COLUMN lexicon_prepared_entry.entry_kind IS '单词或短语类型';
COMMENT ON COLUMN lexicon_prepared_entry.source_gloss IS '被采用来源的原始中文释义，不是所有来源释义集合';
COMMENT ON COLUMN lexicon_prepared_entry.source_gloss_ref IS '被采用释义在来源文件中的定位';
COMMENT ON COLUMN lexicon_prepared_entry.source_bnc_rank IS '来源 BNC 正排名，缺失为空';
COMMENT ON COLUMN lexicon_prepared_entry.source_frq_rank IS '来源 FRQ 正排名，缺失为空';
COMMENT ON COLUMN lexicon_prepared_entry.source_complex_tags IS '来源复杂词表标签集合';
COMMENT ON COLUMN lexicon_prepared_entry.source_oxford_basic IS '来源 Oxford 基础词原始标记，不等于最终阻断';
COMMENT ON COLUMN lexicon_prepared_entry.prepared_gloss IS '导入时判定安全的单一中文短释；阻断时为空';
COMMENT ON COLUMN lexicon_prepared_entry.exclusion_reason IS '不能提示时的固定排除原因；可提示时为空';
COMMENT ON COLUMN lexicon_prepared_entry.prepared_priority IS '词频及复杂标签计算的最终非个人化排序分数';
COMMENT ON COLUMN lexicon_prepared_entry.frequency_zipf IS '来源排名换算的 Zipf 值';
COMMENT ON COLUMN lexicon_prepared_entry.complex_list_count IS '复杂学习词表证据数量';
COMMENT ON COLUMN lexicon_hint_lookup.language_tag IS '准确词形所属语言标签';
COMMENT ON COLUMN lexicon_hint_lookup.normalized_form IS '字幕直接反查的规范原形、别名或屈折形';
COMMENT ON COLUMN lexicon_hint_lookup.lexicon_entry_id IS '指向准备词条的来源无关稳定身份';
COMMENT ON COLUMN lexicon_hint_lookup.form_kind IS '原形、别名或屈折形来源类型';
COMMENT ON COLUMN lexicon_hint_lookup.canonical_lemma IS '展示候选所属的规范主词形';
COMMENT ON COLUMN lexicon_hint_lookup.entry_kind IS '所属主词条为单词还是短语';
COMMENT ON COLUMN lexicon_hint_lookup.final_action IS '观看阶段冻结的提示或阻断决定';
COMMENT ON COLUMN lexicon_hint_lookup.final_gloss IS '仅提示行携带的安全中文短释';
COMMENT ON COLUMN lexicon_hint_lookup.final_priority IS '提示选择时的非个人化优先级';
COMMENT ON COLUMN lexicon_hint_lookup.final_sense_id IS '仅提示行携带的稳定释义身份';
COMMENT ON COLUMN lexicon_hint_lookup.final_frequency_zipf IS '提示排序所需的来源频率投影';
COMMENT ON COLUMN lexicon_hint_lookup.final_complex_list_count IS '提示排序所需的复杂词表计数投影';
COMMENT ON COLUMN lexicon_hint_lookup.cache_priority IS '独立于是否显示的内存预热排序分数';
