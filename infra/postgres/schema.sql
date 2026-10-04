-- LexiFlow 最新完整词库；结构重建显式执行，不由 API 启动升级或清库。

CREATE TABLE lexicon_dataset (
    dataset_id SMALLINT PRIMARY KEY DEFAULT 1 CONSTRAINT lexicon_dataset_singleton_ck CHECK (dataset_id = 1),
    lexicon_version BIGINT NOT NULL CONSTRAINT lexicon_dataset_version_ck CHECK (lexicon_version > 0),
    source_manifest JSONB NOT NULL CONSTRAINT lexicon_dataset_manifest_ck CHECK (
        jsonb_typeof(source_manifest) = 'array' AND jsonb_array_length(source_manifest) > 0),
    source_row_count BIGINT NOT NULL CONSTRAINT lexicon_dataset_source_count_ck CHECK (source_row_count > 0),
    entry_count BIGINT NOT NULL CONSTRAINT lexicon_dataset_entry_count_ck CHECK (entry_count > 0),
    lookup_count BIGINT NOT NULL CONSTRAINT lexicon_dataset_lookup_count_ck CHECK (lookup_count >= entry_count),
    preparation_policy TEXT NOT NULL CONSTRAINT lexicon_dataset_policy_ck CHECK (preparation_policy ~ '[^[:space:]]'),
    CONSTRAINT lexicon_dataset_source_rows_ck CHECK (source_row_count >= entry_count),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lexicon_entry (
    entry_id BIGINT PRIMARY KEY CONSTRAINT lexicon_entry_positive_id_ck CHECK (entry_id > 0),
    lemma TEXT NOT NULL UNIQUE CONSTRAINT lexicon_entry_lemma_ck CHECK (lemma ~ '[^[:space:]]'),
    gloss TEXT CONSTRAINT lexicon_entry_gloss_ck CHECK (gloss IS NULL OR gloss ~ '[^[:space:]]'),
    ranked_word BOOLEAN NOT NULL,
    hint_priority SMALLINT NOT NULL CONSTRAINT lexicon_entry_hint_priority_ck CHECK (hint_priority BETWEEN 0 AND 1000),
    complex_list_count SMALLINT NOT NULL CONSTRAINT lexicon_entry_complex_list_count_ck CHECK (complex_list_count >= 0),
    cache_priority SMALLINT NOT NULL CONSTRAINT lexicon_entry_cache_priority_ck CHECK (cache_priority BETWEEN 0 AND 1000)
);

CREATE TABLE lexicon_form (
    normalized_form TEXT NOT NULL CONSTRAINT lexicon_form_window_ck CHECK (
        normalized_form ~ '[^[:space:]]'
        AND array_length(regexp_split_to_array(btrim(normalized_form), '[[:space:]]+'), 1) BETWEEN 1 AND 3
        AND (array_length(regexp_split_to_array(btrim(normalized_form), '[[:space:]]+'), 1) > 1
             OR length(regexp_replace(normalized_form, '[^[:alpha:]]', '', 'g')) >= 3)),
    entry_id BIGINT NOT NULL CONSTRAINT lexicon_form_entry_fk REFERENCES lexicon_entry(entry_id) ON DELETE CASCADE,
    CONSTRAINT lexicon_form_pk PRIMARY KEY (normalized_form, entry_id)
);

CREATE INDEX lexicon_form_entry_idx ON lexicon_form(entry_id);
CREATE INDEX lexicon_entry_prewarm_hint_idx ON lexicon_entry(cache_priority DESC, entry_id)
    WHERE gloss IS NOT NULL AND cache_priority > 0;
CREATE INDEX lexicon_entry_prewarm_block_idx ON lexicon_entry(cache_priority DESC, entry_id)
    WHERE gloss IS NULL AND cache_priority > 0;

COMMENT ON TABLE lexicon_dataset IS '当前完整词库的单行来源与发布元数据；无导入生命周期状态';
COMMENT ON TABLE lexicon_entry IS '每个可查询规范词条仅保存一次的观看消费数据';
COMMENT ON TABLE lexicon_form IS '准确可查询词形到词条的多对多映射；同形歧义与 BLOCK 均保留';
COMMENT ON COLUMN lexicon_dataset.dataset_id IS '固定为1的单例数据集主键';
COMMENT ON COLUMN lexicon_dataset.lexicon_version IS '完整重导时递增的已发布资料版本';
COMMENT ON COLUMN lexicon_dataset.source_manifest IS '来源标识、许可、摘要与取得时间组成的清单';
COMMENT ON COLUMN lexicon_dataset.source_row_count IS '完整来源预扫描的输入行数';
COMMENT ON COLUMN lexicon_dataset.entry_count IS '窗口裁剪后最终持久化词条行数';
COMMENT ON COLUMN lexicon_dataset.lookup_count IS '窗口裁剪后最终持久化词形映射行数';
COMMENT ON COLUMN lexicon_dataset.preparation_policy IS '本次数据准备采用的冻结规则标识';
COMMENT ON COLUMN lexicon_dataset.imported_at IS '完整写入事务提交前记录的导入时间';
COMMENT ON COLUMN lexicon_entry.entry_id IS '由英文规范 lemma 派生的正 63 位稳定身份；碰撞拒绝发布';
COMMENT ON COLUMN lexicon_entry.lemma IS '规范化主词形或完整短语';
COMMENT ON COLUMN lexicon_entry.gloss IS '唯一已校验中文短释；NULL 表示发布期 BLOCK';
COMMENT ON COLUMN lexicon_entry.ranked_word IS '是否为有排名单词；不是精确频率值或来源证据状态';
COMMENT ON COLUMN lexicon_entry.hint_priority IS '提示选择使用的非个人化排序分数';
COMMENT ON COLUMN lexicon_entry.complex_list_count IS '复杂学习词表证据数量';
COMMENT ON COLUMN lexicon_entry.cache_priority IS '独立于提示排序的缓存预热分数';
COMMENT ON COLUMN lexicon_form.normalized_form IS '窗口内字幕可准确反查的规范词形、别名或屈折形';
COMMENT ON COLUMN lexicon_form.entry_id IS '对应词条的稳定正 long 身份';
