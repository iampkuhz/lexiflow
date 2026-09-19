CREATE TABLE content_revision (
    content_id UUID NOT NULL,
    content_revision BIGINT NOT NULL CHECK (content_revision > 0),
    source_kind TEXT NOT NULL CHECK (source_kind <> ''),
    source_reference_digest CHAR(64) NOT NULL,
    normalization_version BIGINT NOT NULL CHECK (normalization_version > 0),
    content_digest CHAR(64) NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    non_deliverable_at TIMESTAMPTZ,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (content_id, content_revision),
    UNIQUE (source_kind, source_reference_digest, normalization_version, content_digest)
);

CREATE TABLE caption_segment (
    content_id UUID NOT NULL,
    content_revision BIGINT NOT NULL,
    segment_id CHAR(64) NOT NULL,
    track_id TEXT NOT NULL CHECK (track_id <> ''),
    sequence_no BIGINT NOT NULL CHECK (sequence_no >= 0),
    start_millis BIGINT NOT NULL CHECK (start_millis >= 0),
    end_millis BIGINT NOT NULL CHECK (end_millis > start_millis),
    normalized_text TEXT NOT NULL CHECK (length(normalized_text) BETWEEN 1 AND 500),
    PRIMARY KEY (content_id, content_revision, segment_id),
    UNIQUE (content_id, content_revision, track_id, sequence_no),
    FOREIGN KEY (content_id, content_revision)
        REFERENCES content_revision (content_id, content_revision)
        ON DELETE RESTRICT
);

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
    PRIMARY KEY (lexicon_entry_id, lexicon_version),
    UNIQUE (language_tag, normalized_key, lexicon_version)
);

CREATE TABLE lexicon_sense (
    sense_id UUID PRIMARY KEY,
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL,
    chinese_gloss TEXT NOT NULL CHECK (chinese_gloss <> ''),
    definition_text TEXT NOT NULL CHECK (definition_text <> ''),
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
    UNIQUE (normalized_form, lexicon_version),
    FOREIGN KEY (lexicon_entry_id, lexicon_version)
        REFERENCES lexicon_entry (lexicon_entry_id, lexicon_version)
        ON DELETE RESTRICT
);

CREATE TABLE annotation (
    annotation_id UUID PRIMARY KEY,
    annotation_version BIGINT NOT NULL CHECK (annotation_version > 0),
    content_id UUID NOT NULL,
    content_revision BIGINT NOT NULL,
    segment_id CHAR(64) NOT NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    lexicon_entry_id UUID NOT NULL,
    lexicon_version BIGINT NOT NULL CHECK (lexicon_version > 0),
    sense_id UUID NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('suppressed', 'pending', 'ready', 'abandoned')),
    retention_expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (
        content_id, content_revision, segment_id, start_offset, end_offset,
        lexicon_entry_id, lexicon_version, annotation_version
    ),
    FOREIGN KEY (content_id, content_revision, segment_id)
        REFERENCES caption_segment (content_id, content_revision, segment_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (lexicon_entry_id, lexicon_version, sense_id)
        REFERENCES lexicon_sense (lexicon_entry_id, lexicon_version, sense_id)
        ON DELETE RESTRICT
);

CREATE TABLE semantic_result (
    semantic_result_id UUID PRIMARY KEY,
    semantic_result_version BIGINT NOT NULL CHECK (semantic_result_version > 0),
    annotation_id UUID NOT NULL,
    annotation_version BIGINT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'abstained', 'failure')),
    chinese_expression TEXT,
    confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    failure_category TEXT,
    semantic_contract_version BIGINT NOT NULL CHECK (semantic_contract_version > 0),
    retention_expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (annotation_id, annotation_version, semantic_result_version),
    FOREIGN KEY (annotation_id) REFERENCES annotation (annotation_id) ON DELETE RESTRICT,
    CHECK (
        (outcome = 'success' AND chinese_expression IS NOT NULL AND confidence IS NOT NULL AND failure_category IS NULL)
        OR (outcome = 'abstained' AND chinese_expression IS NULL AND confidence IS NULL AND failure_category IS NULL)
        OR (outcome = 'failure' AND chinese_expression IS NULL AND confidence IS NULL AND failure_category IS NOT NULL)
    )
);

CREATE TABLE annotation_work (
    work_id UUID PRIMARY KEY,
    work_key CHAR(64) NOT NULL UNIQUE,
    annotation_id UUID NOT NULL,
    annotation_version BIGINT NOT NULL,
    semantic_contract_version BIGINT NOT NULL CHECK (semantic_contract_version > 0),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'LEASED', 'COMPLETED', 'ABANDONED', 'CANCELLED')),
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 0),
    fence_token BIGINT NOT NULL CHECK (fence_token >= 0),
    lease_expires_at TIMESTAMPTZ,
    deadline_at TIMESTAMPTZ NOT NULL,
    failure_category TEXT,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (annotation_id) REFERENCES annotation (annotation_id) ON DELETE RESTRICT,
    CHECK (
        (state = 'LEASED' AND lease_expires_at IS NOT NULL)
        OR (state <> 'LEASED')
    )
);
