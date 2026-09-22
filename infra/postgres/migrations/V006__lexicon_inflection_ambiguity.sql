ALTER TABLE lexicon_inflection
    DROP CONSTRAINT lexicon_inflection_normalized_form_lexicon_version_key;

CREATE INDEX lexicon_inflection_lookup_idx
    ON lexicon_inflection (normalized_form, lexicon_version);
