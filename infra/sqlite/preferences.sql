PRAGMA foreign_keys = ON;
CREATE TABLE local_preference (
  preference_key TEXT PRIMARY KEY,
  preference_value TEXT NOT NULL
);
CREATE TABLE hidden_lexicon_entry (
  entry_id TEXT PRIMARY KEY,
  lemma TEXT NOT NULL,
  gloss TEXT NOT NULL
);
INSERT INTO local_preference(preference_key, preference_value)
VALUES ('commonWordCutoff', '1500');
CREATE TABLE recent_hint_daily_count (
  day TEXT NOT NULL CHECK(length(day)=10),
  entry_id INTEGER NOT NULL CHECK(entry_id > 0),
  count INTEGER NOT NULL CHECK(count > 0),
  PRIMARY KEY(day, entry_id)
);
