package io.lexiflow.lexicon.application.importing;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.IOException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;

/** 验证导入用例在事务回调返回前完成来源凭据校验。 */
class LexiconImportServiceTest {
  private static final String DIGEST = "a".repeat(64);
  private static final LexiconImportMetadata METADATA =
      new LexiconImportMetadata(DIGEST, "source", "license", Instant.EPOCH);

  @Test
  void rejectsInvalidCountsBeforeCallingPublicationPort() {
    var called = new AtomicBoolean();
    var service = new LexiconImportService(new FakePublicationRepository(called, null));
    assertThrows(
        IllegalArgumentException.class,
        () -> service.publishStreaming(METADATA, 0, 1, consumer -> null));
    assertEquals(false, called.get());
  }

  @Test
  void readReceiptRejectsNullBlankDigestAndNegativeRawRows() {
    assertThrows(
        IllegalArgumentException.class, () -> new LexiconImportRowSource.ReadReceipt(null, 0));
    assertThrows(
        IllegalArgumentException.class, () -> new LexiconImportRowSource.ReadReceipt("  ", 0));
    assertThrows(
        IllegalArgumentException.class, () -> new LexiconImportRowSource.ReadReceipt(DIGEST, -1));
  }

  @Test
  void verifiesReceiptAndDeliveredRowsBeforePublicationPortReturns() {
    var completed = new AtomicBoolean();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), completed));
    var row = row();
    var actual =
        service.publishStreaming(
            METADATA,
            2,
            1,
            consumer -> {
              consumer.accept(row);
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 2);
            });
    assertEquals(7, actual);
    assertEquals(true, completed.get());
  }

  @Test
  void preparesWithActualVersionOnceAndReturnsOnlyPreparedEntries() {
    var seen =
        new java.util.ArrayList<
            io.lexiflow.lexicon.application.importing.LexiconImportPlan.PlannedEntry>();
    var service =
        new LexiconImportService(
            (metadata, raw, entries, source, progress) -> {
              var lookups = new long[1];
              try {
                source.read(
                    9,
                    planned -> {
                      seen.add(planned);
                      lookups[0] +=
                          1L
                              + planned.entry().aliases().size()
                              + planned.entry().inflections().size();
                    });
              } catch (IOException exception) {
                progress.rolledBack();
                throw new IllegalStateException(exception);
              }
              progress.persisted(seen.size(), lookups[0]);
              return 9;
            });
    var result =
        service.publishStreaming(
            METADATA,
            1,
            1,
            consumer -> {
              consumer.accept(row());
              return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
            });
    assertEquals(9, result);
    assertEquals(1, seen.size());
    assertEquals(
        LexiconImportPlan.fromRow(row(), 9, DIGEST, Instant.EPOCH)
            .entry()
            .senses()
            .getFirst()
            .senseId(),
        seen.getFirst().entry().senses().getFirst().senseId());
    assertTrue(seen.getFirst().prepared() != null);
  }

  @Test
  void rejectsCanonicalConflictAcrossDeliveredRows() {
    var service =
        new LexiconImportService(
            new FakePublicationRepository(new AtomicBoolean(), new AtomicBoolean()));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            service.publishStreaming(
                METADATA,
                2,
                2,
                consumer -> {
                  consumer.accept(row());
                  consumer.accept(row());
                  return new LexiconImportRowSource.ReadReceipt(DIGEST, 2);
                }));
  }

  @Test
  void sourceReadFailureEscapesBeforePublicationPortCanReturn() {
    var completed = new AtomicBoolean();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), completed));
    assertThrows(
        IllegalStateException.class,
        () ->
            service.publishStreaming(
                METADATA,
                2,
                1,
                consumer -> {
                  throw new IOException("synthetic source failure");
                }));
    assertEquals(false, completed.get());
  }

  @Test
  void digestMismatchStopsPublicationBeforeRepositoryReturns() {
    var completed = new AtomicBoolean();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), completed));
    assertThrows(
        LexiconSourceChangedException.class,
        () ->
            service.publishStreaming(
                METADATA,
                2,
                1,
                consumer -> {
                  consumer.accept(row());
                  return new LexiconImportRowSource.ReadReceipt("b".repeat(64), 2);
                }));
    assertEquals(false, completed.get());
  }

  @Test
  void untypedFailureWithoutRollbackEvidenceIsInternalAndOriginalFailureEscapes() {
    var failure = new IllegalStateException("synthetic repository failure");
    var events = runRepositoryFailure(failure, false);
    assertEquals(LexiconImportObserver.Reason.INTERNAL_ERROR, events.getLast().reason());
    assertTrue(events.getLast().terminal());
    assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
  }

  @Test
  void repositoryArgumentErrorIsNotMisclassifiedAsSourceValidation() {
    var failure = new IllegalArgumentException("synthetic persistence programming error");
    assertEquals(
        LexiconImportObserver.Reason.INTERNAL_ERROR,
        runRepositoryFailure(failure, false).getLast().reason());
    assertEquals(
        LexiconImportObserver.Reason.PUBLISH_ROLLED_BACK,
        runRepositoryFailure(failure, true).getLast().reason());
    var events = new ArrayList<LexiconImportObserver.Event>();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), null));
    assertThrows(
        IllegalArgumentException.class,
        () ->
            service.publishStreaming(
                METADATA,
                1,
                1,
                consumer -> {
                  throw new IllegalArgumentException("synthetic source parse error");
                },
                new LexiconImportObservation(events::add)));
    assertEquals(LexiconImportObserver.Reason.SOURCE_INVALID, events.getLast().reason());
  }

  @Test
  void rowConsumerFailuresAreNotSourceFailuresWithOrWithoutConfirmedRollback() {
    for (boolean rollback : List.of(false, true)) {
      for (RuntimeException failure :
          List.of(
              new IllegalArgumentException("synthetic consumer error"),
              new java.io.UncheckedIOException(new IOException("synthetic consumer IO")))) {
        var events = new ArrayList<LexiconImportObserver.Event>();
        var service =
            new LexiconImportService(
                (metadata, raw, entries, source, progress) -> {
                  try {
                    source.read(
                        7,
                        planned -> {
                          throw failure;
                        });
                  } catch (IOException exception) {
                    throw new IllegalStateException(exception);
                  } catch (RuntimeException exception) {
                    if (rollback) progress.rolledBack();
                    throw exception;
                  }
                  throw new AssertionError("consumer must fail");
                });
        assertSame(
            failure,
            assertThrows(
                RuntimeException.class,
                () ->
                    service.publishStreaming(
                        METADATA,
                        1,
                        1,
                        consumer -> {
                          consumer.accept(row());
                          return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
                        },
                        new LexiconImportObservation(events::add))));
        assertEquals(
            rollback
                ? LexiconImportObserver.Reason.PUBLISH_ROLLED_BACK
                : LexiconImportObserver.Reason.INTERNAL_ERROR,
            events.getLast().reason());
        assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
      }
    }
  }

  @Test
  void cancellationTakesPriorityOverRollbackAndSourceReceiptChangeKeepsItsType() {
    var cancellation = new CancellationException("synthetic cancellation");
    var cancelled = runRepositoryFailure(cancellation, true);
    assertEquals(LexiconImportObserver.Reason.CANCELLED, cancelled.getLast().reason());

    var events = new ArrayList<LexiconImportObserver.Event>();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), null));
    var thrown =
        assertThrows(
            LexiconSourceChangedException.class,
            () ->
                service.publishStreaming(
                    METADATA,
                    1,
                    1,
                    consumer -> new LexiconImportRowSource.ReadReceipt("b".repeat(64), 1),
                    new LexiconImportObservation(events::add)));
    assertEquals(LexiconSourceChangedException.class, thrown.getClass());
    assertEquals(LexiconImportObserver.Reason.SOURCE_CHANGED, events.getLast().reason());
  }

  private static List<LexiconImportObserver.Event> runRepositoryFailure(
      RuntimeException failure, boolean reportRollback) {
    var events = new ArrayList<LexiconImportObserver.Event>();
    var service =
        new LexiconImportService(
            (metadata, raw, entries, source, progress) -> {
              if (reportRollback) progress.rolledBack();
              throw failure;
            });
    assertSame(
        failure,
        assertThrows(
            RuntimeException.class,
            () ->
                service.publishStreaming(
                    METADATA,
                    1,
                    1,
                    consumer -> {
                      consumer.accept(row());
                      return new LexiconImportRowSource.ReadReceipt(DIGEST, 1);
                    },
                    new LexiconImportObservation(events::add))));
    return events;
  }

  @Test
  void rejectsNullRowNullReceiptRawCountMismatchAndEntryCountMismatchBeforeReturn() {
    assertRejected(
        consumer -> {
          consumer.accept(null);
          return new LexiconImportRowSource.ReadReceipt(DIGEST, 2);
        });
    assertRejected(
        consumer -> {
          consumer.accept(row());
          return null;
        });
    assertRejected(
        consumer -> {
          consumer.accept(row());
          return new LexiconImportRowSource.ReadReceipt(DIGEST, 3);
        });
    assertRejected(consumer -> new LexiconImportRowSource.ReadReceipt(DIGEST, 2));
    assertRejected(
        consumer -> {
          consumer.accept(row());
          consumer.accept(row());
          return new LexiconImportRowSource.ReadReceipt(DIGEST, 2);
        });
  }

  private static void assertRejected(LexiconImportRowSource source) {
    var completed = new AtomicBoolean();
    var service =
        new LexiconImportService(new FakePublicationRepository(new AtomicBoolean(), completed));
    assertThrows(RuntimeException.class, () -> service.publishStreaming(METADATA, 2, 1, source));
    assertEquals(false, completed.get());
  }

  private static LexiconImportRow row() {
    var ref = new SourceReference("source", "license", "row-1");
    return new LexiconImportRow(
        "uncommon-term",
        "罕见词",
        "definition",
        List.of(),
        List.of(),
        new LexiconPriority(1.0, 0, 1),
        ref,
        ref,
        List.of(),
        false);
  }

  private static final class FakePublicationRepository implements LexiconPublicationRepository {
    private final AtomicBoolean called;
    private final AtomicBoolean completed;

    FakePublicationRepository(AtomicBoolean called, AtomicBoolean completed) {
      this.called = called;
      this.completed = completed;
    }

    @Override
    public long publish(
        LexiconImportMetadata metadata,
        long sourceRowsTotal,
        long expectedEntries,
        LexiconPublicationRepository.PreparedEntrySource source,
        LexiconPublicationRepository.PublicationProgress progress) {
      called.set(true);
      progress.persistStarted();
      var rows = new long[2];
      try {
        source.read(
            3,
            planned -> {
              rows[0]++;
              rows[1] +=
                  1L + planned.entry().aliases().size() + planned.entry().inflections().size();
            });
      } catch (IOException exception) {
        progress.rolledBack();
        throw new IllegalStateException("rollback", exception);
      }
      progress.persisted(rows[0], rows[1]);
      if (completed != null) completed.set(true);
      return 7;
    }
  }
}
