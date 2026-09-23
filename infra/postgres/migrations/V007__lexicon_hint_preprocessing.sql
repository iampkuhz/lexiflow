-- 新列不推断旧资料的提示资格；完整预处理必须发布为新词库版本。
ALTER TABLE lexicon_entry
    ADD COLUMN hint_eligibility TEXT NOT NULL DEFAULT 'UNPROCESSED'
        CHECK (hint_eligibility IN ('UNPROCESSED', 'BASIC_VOCABULARY', 'CANDIDATE')),
    ADD COLUMN hint_policy_reference TEXT NOT NULL DEFAULT 'unprocessed'
        CHECK (hint_policy_reference <> '');
