package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.ArrayList;
import java.util.EnumMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class StructuredEventTest {
  @Test
  void validatesClosedRequestAndKnownZero() {
    var e =
        new StructuredEvent(
            StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
            StructuredEvent.Reason.NO_HINT,
            0,
            Map.of(),
            0L,
            UUID.randomUUID(),
            Map.of(StructuredEvent.Timing.API, 0L),
            null,
            null,
            Map.of());
    assertEquals("PASS", e.result());
    assertEquals("caption.request.completed", e.eventName());
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new StructuredEvent(
                StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
                StructuredEvent.Reason.OK,
                0,
                Map.of(),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new StructuredEvent(
                StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
                StructuredEvent.Reason.OK,
                -1,
                Map.of(),
                null,
                UUID.randomUUID(),
                Map.of(StructuredEvent.Timing.API, 0L),
                null,
                null,
                Map.of()));
  }

  @Test
  void rejectsSurrogateSplitAndInvalidRanges() {
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new SegmentAnalysisRecord(
                "a".repeat(64),
                "😀",
                java.util.List.of(
                    new SegmentAnalysisRecord.TranslatedRange(
                        1, 2, "b".repeat(64), "词", 1, true))));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new SegmentAnalysisRecord(
                "a".repeat(64),
                "abc",
                java.util.List.of(
                    new SegmentAnalysisRecord.TranslatedRange(0, 2, "b".repeat(64), "词", 1, true),
                    new SegmentAnalysisRecord.TranslatedRange(
                        1, 3, "b".repeat(64), "词", 1, true))));
  }

  @Test
  void validatesEventReasonMatrixAndAnalysisResult() {
    for (var type : StructuredEvent.EventType.values()) {
      for (var reason : StructuredEvent.Reason.values()) {
        boolean accepted = false;
        try {
          event(type, reason);
          accepted = true;
        } catch (IllegalArgumentException expected) {
          // 非白名单组合必须被拒绝，防止事件原因越界串用。
        }
        assertEquals(allowed(type, reason), accepted, type + "/" + reason);
      }
    }
    var failed =
        event(
            StructuredEvent.EventType.ANALYSIS_RECORD_FAILED,
            StructuredEvent.Reason.ANALYSIS_WRITE_FAILED);
    assertEquals("FAIL", failed.result());
    assertEquals(StructuredEvent.Level.WARN, failed.level());
    assertEquals("analysis", failed.stage());
  }

  @Test
  void levelAndResultFollowFrozenContractForEveryAllowedReason() {
    for (var type : StructuredEvent.EventType.values()) {
      for (var reason : StructuredEvent.Reason.values()) {
        if (!allowed(type, reason)) continue;
        var e = event(type, reason);
        String result =
            switch (reason) {
              case NO_PUBLISHED_DATA, DEPENDENCY_UNAVAILABLE, CANCELLED -> "BLOCKED";
              case SCHEMA_MISMATCH,
                  SOURCE_INVALID,
                  SOURCE_CHANGED,
                  PUBLISH_ROLLED_BACK,
                  INVALID_REQUEST,
                  VERSION_CONFLICT,
                  INTERNAL_ERROR,
                  ANALYSIS_WRITE_FAILED ->
                  "FAIL";
              default -> "PASS";
            };
        var level =
            switch (type) {
              case RUNTIME_START_COMPLETED ->
                  reason == StructuredEvent.Reason.PREWARM_DEGRADED
                      ? StructuredEvent.Level.WARN
                      : result.equals("PASS")
                          ? StructuredEvent.Level.INFO
                          : StructuredEvent.Level.ERROR;
              case LEXICON_IMPORT_STAGE, LEXICON_IMPORT_COMPLETED ->
                  reason == StructuredEvent.Reason.CANCELLED
                      ? StructuredEvent.Level.WARN
                      : result.equals("FAIL") || result.equals("BLOCKED")
                          ? StructuredEvent.Level.ERROR
                          : StructuredEvent.Level.INFO;
              case CAPTION_REQUEST_COMPLETED ->
                  reason == StructuredEvent.Reason.INVALID_REQUEST
                      ? StructuredEvent.Level.WARN
                      : result.equals("PASS")
                          ? StructuredEvent.Level.INFO
                          : StructuredEvent.Level.ERROR;
              case RUNTIME_DEPENDENCY_CHANGED ->
                  reason == StructuredEvent.Reason.RECOVERED
                      ? StructuredEvent.Level.INFO
                      : StructuredEvent.Level.WARN;
              case ANALYSIS_RECORD_FAILED -> StructuredEvent.Level.WARN;
              case LEXICON_CACHE_VERSION_CHANGED -> StructuredEvent.Level.INFO;
            };
        assertEquals(result, e.result(), type + "/" + reason);
        assertEquals(level, e.level(), type + "/" + reason);
      }
    }
  }

  @Test
  void importPhasesRejectPrematureCountsAndInvalidReason() {
    for (var phase :
        new StructuredEvent.ImportPhase[] {
          StructuredEvent.ImportPhase.STARTED, StructuredEvent.ImportPhase.HEARTBEAT
        }) {
      var reason =
          phase == StructuredEvent.ImportPhase.STARTED
              ? StructuredEvent.Reason.STARTED
              : StructuredEvent.Reason.OK;
      assertThrows(
          IllegalArgumentException.class,
          () ->
              new StructuredEvent(
                  StructuredEvent.EventType.LEXICON_IMPORT_STAGE,
                  reason,
                  0,
                  Map.of(StructuredEvent.Count.INPUT_ROWS, 1L),
                  null,
                  null,
                  Map.of(),
                  StructuredEvent.ImportStep.PREPARE,
                  phase,
                  Map.of()));
    }
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new StructuredEvent(
                StructuredEvent.EventType.LEXICON_IMPORT_STAGE,
                StructuredEvent.Reason.STARTED,
                0,
                Map.of(),
                null,
                null,
                Map.of(),
                StructuredEvent.ImportStep.PREPARE,
                StructuredEvent.ImportPhase.COMPLETED,
                Map.of()));
    assertEquals(
        1L,
        new StructuredEvent(
                StructuredEvent.EventType.LEXICON_IMPORT_STAGE,
                StructuredEvent.Reason.OK,
                0,
                Map.of(StructuredEvent.Count.INPUT_ROWS, 1L),
                null,
                null,
                Map.of(),
                StructuredEvent.ImportStep.PREPARE,
                StructuredEvent.ImportPhase.COMPLETED,
                Map.of())
            .counts()
            .get(StructuredEvent.Count.INPUT_ROWS));
  }

  @Test
  void fieldOwnershipAndNonnegativeMeasurements() {
    var id = UUID.randomUUID();
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
                Map.of(),
                null,
                null,
                Map.of(StructuredEvent.Timing.API, 0L),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
                Map.of(),
                null,
                id,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(StructuredEvent.Count.SELECTED, 1L),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(),
                null,
                id,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(),
                null,
                null,
                Map.of(StructuredEvent.Timing.API, 0L),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(),
                null,
                null,
                Map.of(),
                StructuredEvent.ImportStep.PREPARE,
                StructuredEvent.ImportPhase.STARTED,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of(StructuredEvent.ReasonCount.CURATED, 1L)));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.ANALYSIS_RECORD_FAILED,
                Map.of(),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(StructuredEvent.Count.PREWARM_POSITIVE, -1L),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                Map.of(),
                -1L,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
                Map.of(),
                null,
                id,
                Map.of(StructuredEvent.Timing.API, -1L),
                null,
                null,
                Map.of()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            make(
                StructuredEvent.EventType.LEXICON_IMPORT_COMPLETED,
                Map.of(),
                null,
                null,
                Map.of(),
                null,
                null,
                Map.of(StructuredEvent.ReasonCount.CURATED, -1L)));
  }

  @Test
  void countWhitelistMatchesEachEventContract() {
    for (var type : StructuredEvent.EventType.values()) {
      Set<StructuredEvent.Count> allowedCounts =
          switch (type) {
            case RUNTIME_START_COMPLETED ->
                Set.of(
                    StructuredEvent.Count.PREWARM_POSITIVE, StructuredEvent.Count.PREWARM_NEGATIVE);
            case LEXICON_IMPORT_STAGE ->
                Set.of(
                    StructuredEvent.Count.INPUT_ROWS, StructuredEvent.Count.PREPARED_ROWS,
                    StructuredEvent.Count.HINT_ROWS, StructuredEvent.Count.BLOCKED_ROWS);
            case LEXICON_IMPORT_COMPLETED ->
                Set.of(
                    StructuredEvent.Count.INPUT_ROWS, StructuredEvent.Count.PREPARED_ROWS,
                    StructuredEvent.Count.LOOKUP_ROWS, StructuredEvent.Count.BLOCKED_ROWS);
            case LEXICON_CACHE_VERSION_CHANGED ->
                Set.of(
                    StructuredEvent.Count.INVALIDATED_POSITIVE,
                    StructuredEvent.Count.INVALIDATED_NEGATIVE);
            case CAPTION_REQUEST_COMPLETED ->
                Set.of(
                    StructuredEvent.Count.NEW_RANGES, StructuredEvent.Count.QUERY_KEYS,
                    StructuredEvent.Count.POSITIVE_HITS, StructuredEvent.Count.NEGATIVE_HITS,
                    StructuredEvent.Count.CACHE_MISSES, StructuredEvent.Count.DB_BATCHES,
                    StructuredEvent.Count.VERSION_READS, StructuredEvent.Count.PREWARM_READS,
                    StructuredEvent.Count.CANDIDATES, StructuredEvent.Count.SELECTED,
                    StructuredEvent.Count.AMBIGUOUS, StructuredEvent.Count.OVERLAP_DROPPED);
            case RUNTIME_DEPENDENCY_CHANGED -> Set.of();
            case ANALYSIS_RECORD_FAILED -> Set.of(StructuredEvent.Count.ATTEMPTED_SEGMENTS);
          };
      for (var count : StructuredEvent.Count.values()) {
        var requestId =
            type == StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED
                    || type == StructuredEvent.EventType.ANALYSIS_RECORD_FAILED
                ? UUID.randomUUID()
                : null;
        var timings =
            type == StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED
                ? Map.of(StructuredEvent.Timing.API, 0L)
                : Map.<StructuredEvent.Timing, Long>of();
        var step =
            type == StructuredEvent.EventType.LEXICON_IMPORT_STAGE
                ? StructuredEvent.ImportStep.PREPARE
                : null;
        var phase = step == null ? null : StructuredEvent.ImportPhase.COMPLETED;
        if (allowedCounts.contains(count)) {
          assertEquals(
              0L,
              make(type, Map.of(count, 0L), null, requestId, timings, step, phase, Map.of())
                  .counts()
                  .get(count));
        } else {
          assertThrows(
              IllegalArgumentException.class,
              () -> make(type, Map.of(count, 0L), null, requestId, timings, step, phase, Map.of()),
              type + "/" + count);
        }
      }
    }
  }

  @Test
  void copiesMapsAndSensitiveRecordRanges() {
    var counts = new EnumMap<StructuredEvent.Count, Long>(StructuredEvent.Count.class);
    counts.put(StructuredEvent.Count.PREWARM_POSITIVE, 0L);
    var e =
        make(
            StructuredEvent.EventType.RUNTIME_START_COMPLETED,
            counts,
            0L,
            null,
            Map.of(),
            null,
            null,
            Map.of());
    counts.put(StructuredEvent.Count.PREWARM_POSITIVE, 9L);
    assertEquals(0L, e.counts().get(StructuredEvent.Count.PREWARM_POSITIVE));
    assertThrows(UnsupportedOperationException.class, () -> e.counts().clear());
    var ranges = new ArrayList<SegmentAnalysisRecord.TranslatedRange>();
    ranges.add(new SegmentAnalysisRecord.TranslatedRange(0, 2, "b".repeat(64), "词", 1, true));
    var record = new SegmentAnalysisRecord("a".repeat(64), "abc", ranges);
    ranges.clear();
    assertEquals(1, record.translatedRanges().size());
    assertThrows(UnsupportedOperationException.class, () -> record.translatedRanges().clear());
    assertThrows(
        IllegalArgumentException.class,
        () -> new SegmentAnalysisRecord("bad", "x", java.util.List.of()));
    assertThrows(
        IllegalArgumentException.class,
        () -> new SegmentAnalysisRecord.TranslatedRange(0, 1, "bad", "词", 1, true));
    assertThrows(
        IllegalArgumentException.class,
        () -> new SegmentAnalysisRecord.TranslatedRange(0, 1, "b".repeat(64), "  ", 1, true));
    assertThrows(
        IllegalArgumentException.class,
        () -> new SegmentAnalysisRecord.TranslatedRange(0, 1, "b".repeat(64), "词", 0, true));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            new SegmentAnalysisRecord(
                "a".repeat(64),
                "x",
                java.util.List.of(
                    new SegmentAnalysisRecord.TranslatedRange(
                        0, 2, "b".repeat(64), "词", 1, true))));
  }

  private static StructuredEvent make(
      StructuredEvent.EventType type,
      Map<StructuredEvent.Count, Long> counts,
      Long version,
      UUID id,
      Map<StructuredEvent.Timing, Long> timings,
      StructuredEvent.ImportStep step,
      StructuredEvent.ImportPhase phase,
      Map<StructuredEvent.ReasonCount, Long> reasons) {
    var reason =
        switch (type) {
          case ANALYSIS_RECORD_FAILED -> StructuredEvent.Reason.ANALYSIS_WRITE_FAILED;
          case LEXICON_CACHE_VERSION_CHANGED -> StructuredEvent.Reason.VERSION_CHANGED;
          case RUNTIME_DEPENDENCY_CHANGED -> StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE;
          default -> StructuredEvent.Reason.OK;
        };
    return new StructuredEvent(type, reason, 0, counts, version, id, timings, step, phase, reasons);
  }

  private static StructuredEvent event(StructuredEvent.EventType t, StructuredEvent.Reason r) {
    var counts = Map.<StructuredEvent.Count, Long>of();
    var timings = Map.<StructuredEvent.Timing, Long>of();
    var request =
        t == StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED
                || t == StructuredEvent.EventType.ANALYSIS_RECORD_FAILED
            ? UUID.randomUUID()
            : null;
    if (t == StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED)
      timings = Map.of(StructuredEvent.Timing.API, 1L);
    var step =
        t == StructuredEvent.EventType.LEXICON_IMPORT_STAGE
            ? StructuredEvent.ImportStep.PREPARE
            : null;
    var phase =
        t == StructuredEvent.EventType.LEXICON_IMPORT_STAGE
            ? (r == StructuredEvent.Reason.STARTED
                ? StructuredEvent.ImportPhase.STARTED
                : StructuredEvent.ImportPhase.COMPLETED)
            : null;
    return new StructuredEvent(t, r, 0, counts, null, request, timings, step, phase, Map.of());
  }

  private static boolean allowed(StructuredEvent.EventType t, StructuredEvent.Reason r) {
    return switch (t) {
      case RUNTIME_START_COMPLETED ->
          java.util.Set.of(
                  StructuredEvent.Reason.OK,
                  StructuredEvent.Reason.DEMO_MODE,
                  StructuredEvent.Reason.NO_PUBLISHED_DATA,
                  StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE,
                  StructuredEvent.Reason.SCHEMA_MISMATCH,
                  StructuredEvent.Reason.PREWARM_DEGRADED)
              .contains(r);
      case LEXICON_IMPORT_STAGE ->
          java.util.Set.of(
                  StructuredEvent.Reason.STARTED,
                  StructuredEvent.Reason.OK,
                  StructuredEvent.Reason.SOURCE_INVALID,
                  StructuredEvent.Reason.SOURCE_CHANGED,
                  StructuredEvent.Reason.CANCELLED,
                  StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE,
                  StructuredEvent.Reason.INTERNAL_ERROR,
                  StructuredEvent.Reason.PUBLISH_ROLLED_BACK)
              .contains(r);
      case LEXICON_IMPORT_COMPLETED ->
          java.util.Set.of(
                  StructuredEvent.Reason.OK,
                  StructuredEvent.Reason.CANCELLED,
                  StructuredEvent.Reason.SOURCE_INVALID,
                  StructuredEvent.Reason.SOURCE_CHANGED,
                  StructuredEvent.Reason.PUBLISH_ROLLED_BACK,
                  StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE,
                  StructuredEvent.Reason.INTERNAL_ERROR)
              .contains(r);
      case LEXICON_CACHE_VERSION_CHANGED -> r == StructuredEvent.Reason.VERSION_CHANGED;
      case CAPTION_REQUEST_COMPLETED ->
          java.util.Set.of(
                  StructuredEvent.Reason.OK,
                  StructuredEvent.Reason.NO_HINT,
                  StructuredEvent.Reason.NO_NEW_SEGMENTS,
                  StructuredEvent.Reason.INVALID_REQUEST,
                  StructuredEvent.Reason.NO_PUBLISHED_DATA,
                  StructuredEvent.Reason.VERSION_CONFLICT,
                  StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE,
                  StructuredEvent.Reason.INTERNAL_ERROR)
              .contains(r);
      case RUNTIME_DEPENDENCY_CHANGED ->
          r == StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE
              || r == StructuredEvent.Reason.RECOVERED;
      case ANALYSIS_RECORD_FAILED -> r == StructuredEvent.Reason.ANALYSIS_WRITE_FAILED;
    };
  }
}
