-- LexiFlow 唯一最新 SQLite 事实存储结构；结构变化须显式重建并完整重导。
CREATE TABLE lexicon_dataset (
    dataset_id INTEGER PRIMARY KEY CHECK (dataset_id = 1),
    lexicon_version INTEGER NOT NULL CHECK (lexicon_version > 0),
    source_manifest TEXT NOT NULL CHECK (
        json_valid(source_manifest) = 1
        AND json_type(source_manifest) = 'array'
        AND json_array_length(source_manifest) > 0
    ),
    source_row_count INTEGER NOT NULL CHECK (source_row_count > 0),
    entry_count INTEGER NOT NULL CHECK (entry_count > 0),
    lookup_count INTEGER NOT NULL CHECK (lookup_count >= entry_count),
    preparation_policy TEXT NOT NULL CHECK (length(trim(preparation_policy)) > 0),
    generation_version TEXT NOT NULL CHECK (length(generation_version) = 64 AND generation_version NOT GLOB '*[^0-9a-f]*'),
    generation_code_version TEXT NOT NULL CHECK (length(generation_code_version) = 64 AND generation_code_version NOT GLOB '*[^0-9a-f]*'),
    generation_source_digest TEXT NOT NULL CHECK (length(generation_source_digest) = 64 AND generation_source_digest NOT GLOB '*[^0-9a-f]*'),
    generation_config TEXT NOT NULL CHECK (json_valid(generation_config) = 1 AND json_type(generation_config) = 'object'),
    generation_inputs TEXT NOT NULL CHECK (json_valid(generation_inputs) = 1 AND json_type(generation_inputs) = 'object'),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (source_row_count >= entry_count)
) STRICT;

CREATE TABLE lexicon_entry (
    entry_id INTEGER PRIMARY KEY CHECK (entry_id > 0),
    lemma TEXT NOT NULL UNIQUE CHECK (length(trim(lemma)) > 0),
    gloss TEXT CHECK (gloss IS NULL OR length(trim(gloss)) > 0),
    canonical_form INTEGER NOT NULL CHECK (canonical_form IN (0,1)),
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 1000)
) STRICT;

CREATE TABLE lexicon_form (
    normalized_form TEXT NOT NULL CHECK (length(trim(normalized_form)) > 0),
    entry_id INTEGER NOT NULL REFERENCES lexicon_entry(entry_id) ON DELETE CASCADE,
    PRIMARY KEY (normalized_form, entry_id)
) STRICT, WITHOUT ROWID;

CREATE INDEX lexicon_form_entry_idx ON lexicon_form(entry_id);
CREATE INDEX lexicon_entry_prewarm_hint_idx ON lexicon_entry(priority DESC, entry_id)
    WHERE gloss IS NOT NULL AND priority > 0;
CREATE INDEX lexicon_entry_prewarm_block_idx ON lexicon_entry(priority DESC, entry_id)
    WHERE gloss IS NULL AND priority > 0;

CREATE VIEW lexicon_lookup AS
    SELECT lemma AS normalized_form, entry_id FROM lexicon_entry WHERE canonical_form = 1
    UNION ALL
    SELECT normalized_form, entry_id FROM lexicon_form;

CREATE TRIGGER lexicon_form_canonical_guard
BEFORE INSERT ON lexicon_form
WHEN EXISTS (
    SELECT 1 FROM lexicon_entry e
    WHERE e.entry_id = NEW.entry_id AND e.canonical_form = 1 AND e.lemma = NEW.normalized_form
)
BEGIN
    SELECT RAISE(ABORT, 'canonical form must not be stored in lexicon_form');
END;

CREATE TRIGGER lexicon_form_canonical_update_guard
BEFORE UPDATE OF normalized_form, entry_id ON lexicon_form
WHEN EXISTS (
    SELECT 1 FROM lexicon_entry e
    WHERE e.entry_id = NEW.entry_id AND e.canonical_form = 1 AND e.lemma = NEW.normalized_form
)
BEGIN
    SELECT RAISE(ABORT, 'canonical form must not be stored in lexicon_form');
END;

CREATE TRIGGER lexicon_entry_canonical_update_guard
BEFORE UPDATE OF lemma, canonical_form ON lexicon_entry
WHEN NEW.canonical_form = 1 AND EXISTS (
    SELECT 1 FROM lexicon_form f
    WHERE f.entry_id = OLD.entry_id AND f.normalized_form = NEW.lemma
)
BEGIN
    SELECT RAISE(ABORT, 'canonical form must not duplicate a physical form');
END;
