package io.lexiflow.lexicon.domain.catalog;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class BuiltinLexiconCatalogTest {
  @Test
  void matchesOnlyExactFormsAndUsesVersionOneWithoutStorageReads() {
    var catalog = new BuiltinLexiconCatalog();
    var exact = catalog.lookupForms(List.of("liable", "reliable", "reliable"));
    assertEquals(
        List.of("reliable"), exact.candidates().stream().map(c -> c.normalizedForm()).toList());
    assertEquals(1, exact.publishedVersion().orElseThrow());
    assertEquals(2, exact.counts().queryKeys());
    assertEquals(1, exact.counts().positiveHits());
    assertEquals(1, exact.counts().negativeHits());
  }
}
