package io.lexiflow.lexicon.application.importing;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.IOException;
import java.time.Instant;
import java.util.List;
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
        IllegalStateException.class,
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
        io.lexiflow.lexicon.application.importing.model.LexiconImportRequest request) {
      throw new UnsupportedOperationException();
    }

    @Override
    public long publishStreaming(
        LexiconImportMetadata metadata,
        long sourceRowsTotal,
        long expectedEntries,
        LexiconImportRowSource source) {
      called.set(true);
      try {
        source.read(ignored -> {});
      } catch (IOException exception) {
        throw new IllegalStateException("rollback", exception);
      }
      if (completed != null) completed.set(true);
      return 7;
    }
  }
}
