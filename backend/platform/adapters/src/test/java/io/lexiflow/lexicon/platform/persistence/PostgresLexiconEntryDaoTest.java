package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class PostgresLexiconEntryDaoTest {
  @Test
  void usesTheIndexedCanonicalAndFormPathsRatherThanCorrelatedExists() {
    var sql = PostgresLexiconEntryDao.findByFormsSql(":form0", ":entryKey0");

    assertTrue(sql.contains("WITH matched_entries"));
    assertTrue(sql.contains("language_tag = 'en'"));
    assertTrue(sql.contains("normalized_key IN (:entryKey0)"));
    assertTrue(sql.contains("lexicon_alias"));
    assertTrue(sql.contains("lexicon_inflection"));
    assertFalse(sql.contains("EXISTS"));
  }

  @Test
  void findsCanonicalOwnersThroughTheNormalizedKeyLookupPath() {
    var sql = PostgresLexiconEntryDao.findCanonicalOwnersSql(":entryKey0", ":form0");

    assertTrue(sql.contains("language_tag = 'en'"));
    assertTrue(sql.contains("normalized_key IN (:entryKey0)"));
    assertTrue(sql.contains("a.normalized_form IN (:form0)"));
    assertFalse(sql.contains("lemma IN"));
  }

  @Test
  void derivesTheExistingNormalizedKeyForWordsAndPhrases() {
    assertEquals("en:word:reliable", PostgresLexiconEntryDao.normalizedEntryKey("reliable"));
    assertEquals("en:phrase:in spite", PostgresLexiconEntryDao.normalizedEntryKey("in spite"));
  }
}
