ALTER TABLE lexicon_sense
    ALTER COLUMN definition_text DROP NOT NULL;
ALTER TABLE lexicon_sense
    DROP CONSTRAINT lexicon_sense_definition_text_check;

ALTER TABLE lexicon_entry
    ADD COLUMN prewarm_eligible BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE lexicon_import_batch
    ADD COLUMN source_rows_processed BIGINT NOT NULL DEFAULT 0 CHECK (source_rows_processed >= 0),
    ADD COLUMN source_rows_total BIGINT CHECK (source_rows_total IS NULL OR source_rows_total >= source_rows_processed);
