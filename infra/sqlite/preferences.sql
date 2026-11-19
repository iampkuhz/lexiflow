PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS local_preference (
  preference_key TEXT PRIMARY KEY,
  preference_value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hidden_lexicon_entry (
  entry_id TEXT PRIMARY KEY,
  lemma TEXT NOT NULL,
  gloss TEXT NOT NULL
);
INSERT OR IGNORE INTO local_preference(preference_key, preference_value)
VALUES ('commonWordCutoff', '1500');
CREATE TABLE IF NOT EXISTS recent_hint_daily_count (
  day TEXT NOT NULL CHECK(length(day)=10),
  entry_id INTEGER NOT NULL CHECK(entry_id > 0),
  count INTEGER NOT NULL CHECK(count > 0),
  PRIMARY KEY(day, entry_id)
);
