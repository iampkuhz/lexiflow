-- LexiFlow 最新 lexicon 结构初始化。
-- 直接声明六张表和对应索引；不拼接历史 ALTER/DROP，不保留 schema_migration 台账。

CREATE TABLE lexicon_entry (
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL CHECK (lexicon_version > 0),
    language_tag TEXT NOT NULL CHECK (language_tag = 'en'),
    entry_kind TEXT NOT NULL CHECK (entry_kind IN ('word', 'phrase')),
    lemma TEXT NOT NULL CHECK (lemma <> ''),
    normalized_key TEXT NOT NULL CHECK (normalized_key <> ''),
    provenance_source_id TEXT NOT NULL CHECK (provenance_source_id <> ''),
    provenance_license_id TEXT NOT NULL CHECK (provenance_license_id <> ''),
    provenance_digest CHAR(64) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    frequency_zipf NUMERIC(3, 2) NOT NULL DEFAULT 0 CHECK (frequency_zipf >= 0 AND frequency_zipf <= 8),
    complex_list_count SMALLINT NOT NULL DEFAULT 0 CHECK (complex_list_count >= 0),
    memory_priority INTEGER NOT NULL DEFAULT 0 CHECK (memory_priority BETWEEN 0 AND 1000),
    prewarm_eligible BOOLEAN NOT NULL DEFAULT TRUE,
    hint_eligibility TEXT NOT NULL DEFAULT 'UNPROCESSED'
        CHECK (hint_eligibility IN ('UNPROCESSED', 'BASIC_VOCABULARY', 'CANDIDATE')),
    hint_policy_reference TEXT NOT NULL DEFAULT 'unprocessed'
        CHECK (hint_policy_reference <> ''),
    PRIMARY KEY (lexicon_entry_id, lexicon_version),
    UNIQUE (language_tag, normalized_key, lexicon_version)
);

CREATE TABLE lexicon_sense (
    sense_id UUID PRIMARY KEY,
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL,
    chinese_gloss TEXT NOT NULL CHECK (chinese_gloss <> ''),
    definition_text TEXT,
    provenance_reference TEXT NOT NULL CHECK (provenance_reference <> ''),
    UNIQUE (lexicon_entry_id, lexicon_version, sense_id),
    FOREIGN KEY (lexicon_entry_id, lexicon_version)
        REFERENCES lexicon_entry (lexicon_entry_id, lexicon_version)
        ON DELETE RESTRICT
);

CREATE TABLE lexicon_alias (
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL,
    normalized_form TEXT NOT NULL CHECK (normalized_form <> ''),
    PRIMARY KEY (lexicon_entry_id, lexicon_version, normalized_form),
    UNIQUE (normalized_form, lexicon_version),
    FOREIGN KEY (lexicon_entry_id, lexicon_version)
        REFERENCES lexicon_entry (lexicon_entry_id, lexicon_version)
        ON DELETE RESTRICT
);

CREATE TABLE lexicon_inflection (
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL,
    normalized_form TEXT NOT NULL CHECK (normalized_form <> ''),
    PRIMARY KEY (lexicon_entry_id, lexicon_version, normalized_form),
    FOREIGN KEY (lexicon_entry_id, lexicon_version)
        REFERENCES lexicon_entry (lexicon_entry_id, lexicon_version)
        ON DELETE RESTRICT
);

CREATE TABLE lexicon_import_batch (
    import_batch_id UUID PRIMARY KEY,
    lexicon_version BIGINT NOT NULL UNIQUE CHECK (lexicon_version > 0),
    source_id TEXT NOT NULL CHECK (source_id <> ''),
    license_id TEXT NOT NULL CHECK (license_id <> ''),
    source_digest CHAR(64) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('STAGED', 'PUBLISHED', 'SUPERSEDED', 'REJECTED')),
    entry_count INTEGER NOT NULL CHECK (entry_count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMPTZ,
    source_rows_processed BIGINT NOT NULL DEFAULT 0 CHECK (source_rows_processed >= 0),
    source_rows_total BIGINT CHECK (source_rows_total IS NULL OR source_rows_total >= source_rows_processed),
    CHECK ((state IN ('PUBLISHED', 'SUPERSEDED') AND published_at IS NOT NULL) OR (state NOT IN ('PUBLISHED', 'SUPERSEDED')))
);

CREATE TABLE lexicon_source_evidence (
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL,
    source_id TEXT NOT NULL CHECK (source_id <> ''),
    license_id TEXT NOT NULL CHECK (license_id <> ''),
    source_record_ref TEXT NOT NULL CHECK (source_record_ref <> ''),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind IN ('DICTIONARY', 'FREQUENCY', 'COMPLEX_WORD_LIST')),
    evidence_value TEXT NOT NULL CHECK (evidence_value <> ''),
    PRIMARY KEY (lexicon_entry_id, lexicon_version, source_id, evidence_kind, source_record_ref),
    FOREIGN KEY (lexicon_entry_id, lexicon_version)
        REFERENCES lexicon_entry (lexicon_entry_id, lexicon_version)
        ON DELETE RESTRICT
);

CREATE INDEX lexicon_entry_lookup_idx
    ON lexicon_entry (language_tag, normalized_key, lexicon_version);
CREATE INDEX lexicon_entry_prewarm_idx
    ON lexicon_entry (lexicon_version, memory_priority DESC, normalized_key);
CREATE INDEX lexicon_source_evidence_entry_idx
    ON lexicon_source_evidence (lexicon_entry_id, lexicon_version);
CREATE UNIQUE INDEX lexicon_import_single_published_idx
    ON lexicon_import_batch ((state = 'PUBLISHED'))
    WHERE state = 'PUBLISHED';
CREATE INDEX lexicon_inflection_lookup_idx
    ON lexicon_inflection (normalized_form, lexicon_version);
