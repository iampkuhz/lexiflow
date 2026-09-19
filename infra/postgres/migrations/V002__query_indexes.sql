CREATE INDEX caption_segment_timeline_idx
    ON caption_segment (content_id, content_revision, track_id, sequence_no);
CREATE INDEX annotation_revision_range_idx
    ON annotation (content_id, content_revision, segment_id, start_offset, end_offset);
CREATE INDEX annotation_work_recovery_idx
    ON annotation_work (lease_expires_at, deadline_at)
    WHERE state IN ('PENDING', 'LEASED');
CREATE INDEX lexicon_entry_lookup_idx
    ON lexicon_entry (language_tag, normalized_key, lexicon_version);
CREATE INDEX content_revision_retention_idx
    ON content_revision (retention_expires_at)
    WHERE non_deliverable_at IS NOT NULL;
CREATE INDEX annotation_retention_idx
    ON annotation (retention_expires_at);
CREATE INDEX semantic_result_retention_idx
    ON semantic_result (retention_expires_at);
CREATE INDEX annotation_work_retention_idx
    ON annotation_work (retention_expires_at);
