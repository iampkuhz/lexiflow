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
    CHECK ((state IN ('PUBLISHED', 'SUPERSEDED') AND published_at IS NOT NULL) OR (state NOT IN ('PUBLISHED', 'SUPERSEDED')))
);

ALTER TABLE lexicon_entry
    ADD COLUMN frequency_zipf NUMERIC(3, 2) NOT NULL DEFAULT 0 CHECK (frequency_zipf >= 0 AND frequency_zipf <= 8),
    ADD COLUMN complex_list_count SMALLINT NOT NULL DEFAULT 0 CHECK (complex_list_count >= 0),
    ADD COLUMN memory_priority INTEGER NOT NULL DEFAULT 0 CHECK (memory_priority BETWEEN 0 AND 1000);

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

CREATE INDEX lexicon_entry_prewarm_idx
    ON lexicon_entry (lexicon_version, memory_priority DESC, normalized_key);
CREATE INDEX lexicon_source_evidence_entry_idx
    ON lexicon_source_evidence (lexicon_entry_id, lexicon_version);
CREATE UNIQUE INDEX lexicon_import_single_published_idx
    ON lexicon_import_batch ((state = 'PUBLISHED'))
    WHERE state = 'PUBLISHED';
