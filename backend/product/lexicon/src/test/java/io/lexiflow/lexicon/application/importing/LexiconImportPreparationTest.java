package io.lexiflow.lexicon.application.importing;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;

/** 覆盖导入预检的流式计数、冲突和有界预热合同。 */
class LexiconImportPreparationTest {
  private static final String DIGEST = "a".repeat(64);
  private static final LexiconImportMetadata META =
      new LexiconImportMetadata(DIGEST, "source", "license", Instant.EPOCH);

  @Test
  void countsRawRowsSeparatelyFromDeliveredRowsAndBoundsSortedPreview() {
    var result =
        LexiconImportPreparation.inspect(
            META,
            consumer -> {
              consumer.accept(row("beta", 2));
              consumer.accept(row("alpha", 2));
              consumer.accept(row("gamma", 4));
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 5);
            },
            2);
    assertEquals(5, result.counts().sourceRowsTotal());
    assertEquals(3, result.counts().entries());
    assertEquals(3, result.statistics().preparedRows());
    assertEquals(5, result.statistics().inputRows());
    assertEquals(3, result.statistics().hintRows() + result.statistics().blockedRows());
    assertTrue(
        result.statistics().reasonCounts().values().stream().mapToLong(Long::longValue).sum() >= 3);
    assertEquals(
        List.of("gamma", "alpha"),
        result.prewarmEntries().stream().map(v -> v.entry().lemma()).toList());
    assertThrows(UnsupportedOperationException.class, () -> result.prewarmEntries().clear());
  }

  @Test
  void decisiveRuleAndMatchedTransformationCountOncePerEntry() {
    var ref = new SourceReference("ecdict-stardict", "license", "synthetic-row");
    var row =
        new LexiconImportRow(
            "medical-term",
            "[医]甲[床]瘤",
            "",
            List.of(),
            List.of(),
            new LexiconPriority(4, 0, 10),
            ref,
            ref,
            List.of(),
            true);
    var result =
        LexiconImportPreparation.inspect(
            META,
            consumer -> {
              consumer.accept(row);
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
            },
            1);
    var prepared = result.prewarmEntries().getFirst().prepared();
    assertTrue(prepared.matchedRules().contains(prepared.decisiveRule()));
    assertEquals(1L, result.statistics().reasonCounts().get(prepared.decisiveRule()));
  }

  @Test
  void excludesIneligibleAndZeroPriorityAndLimitZeroStillValidates() {
    var result =
        LexiconImportPreparation.inspect(
            META,
            consumer -> {
              consumer.accept(row("eligible", 2, true));
              consumer.accept(row("zero", 0, true));
              consumer.accept(row("ineligible", 7, false));
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 5);
            },
            5);
    assertEquals(
        List.of("eligible"), result.prewarmEntries().stream().map(v -> v.entry().lemma()).toList());
    var zeroLimit =
        LexiconImportPreparation.inspect(
            META,
            consumer -> {
              consumer.accept(row("only", 1, true));
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
            },
            0);
    assertEquals(1, zeroLimit.counts().entries());
    assertTrue(zeroLimit.prewarmEntries().isEmpty());
  }

  @Test
  void permitsEmptyPreviewButRejectsBadReceiptAndReadFailure() {
    var empty =
        LexiconImportPreparation.inspect(
            META, consumer -> new LexiconImportRowSource.ReadReceipt(DIGEST, 0), 0);
    assertEquals(0, empty.counts().entries());
    assertThrows(IllegalArgumentException.class, empty::requirePublishable);
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconImportPreparation.inspect(
                META,
                consumer -> {
                  consumer.accept(row("ghost", 1));
                  return new LexiconImportRowSource.ReadReceipt(DIGEST, 0);
                },
                0));
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconImportPreparation.inspect(
                META,
                consumer -> {
                  throw new java.io.IOException("synthetic read fault");
                },
                0));
  }

  @Test
  void rejectsDigestAndCanonicalCollisionAcrossStream() {
    assertThrows(
        IllegalStateException.class,
        () ->
            LexiconImportPreparation.inspect(
                META,
                consumer -> {
                  consumer.accept(row("alpha", 1));
                  return new LexiconImportRowSource.ReadReceipt("b".repeat(64), 1);
                },
                0));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            LexiconImportPreparation.inspect(
                META,
                consumer -> {
                  consumer.accept(row("alpha", 1));
                  consumer.accept(row("alpha", 1));
                  return new LexiconImportRowSource.ReadReceipt(DIGEST, 2);
                },
                0));
  }

  @Test
  void progressObserverFailureDoesNotInterruptPreparation() {
    var result =
        LexiconImportPreparation.inspect(
            META,
            consumer -> {
              consumer.accept(row("alpha", 2));
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
            },
            1,
            () -> {
              throw new IllegalStateException("synthetic observer failure");
            });
    assertEquals(1, result.statistics().preparedRows());
  }

  private static LexiconImportRow row(String lemma, int priority) {
    return row(lemma, priority, true);
  }

  private static LexiconImportRow row(String lemma, int priority, boolean eligible) {
    var ref = new SourceReference("source", "license", lemma);
    return new LexiconImportRow(
        lemma,
        "释义",
        "",
        List.of(),
        List.of(),
        new LexiconPriority(1, 0, priority),
        ref,
        ref,
        List.of(),
        eligible);
  }
}
