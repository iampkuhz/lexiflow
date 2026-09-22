package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportRow;
import io.lexiflow.lexicon.application.SourceReference;
import io.lexiflow.lexicon.domain.LexiconPriority;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.AbstractPlatformTransactionManager;
import org.springframework.transaction.support.DefaultTransactionStatus;
import org.springframework.transaction.support.TransactionTemplate;

/** 验证 Repository 只在一个事务中协调多个表级 DAO。 */
class DefaultLexiconRepositoryTest {
  @Test
  void publishesOneVersionByOrchestratingAllDaosInOrder() {
    var events = new ArrayList<String>();
    var transactionManager = new RecordingTransactionManager();
    var repository = repository(events, transactionManager, false);

    var version = repository.publish(request());

    assertEquals(7, version);
    assertEquals(
        List.of("lock", "next", "stage:1", "entries:1", "evidence:1", "supersede", "publish"),
        events);
    assertEquals(1, transactionManager.commits);
    assertEquals(0, transactionManager.rollbacks);
  }

  @Test
  void rejectsCanonicalAliasCollisionAgainstPreviouslyStagedChunk() {
    var events = new ArrayList<String>();
    var transactionManager = new RecordingTransactionManager();
    var repository = repository(events, transactionManager, false);
    var metadata = new LexiconImportMetadata("a".repeat(64), "fixture", "MIT", Instant.EPOCH);
    var batch = new io.lexiflow.lexicon.application.StagedLexiconImport(UUID.randomUUID(), 7, 0);

    repository.stage(batch, List.of(row("alpha", List.of("beta"), List.of())), 1, metadata);

    assertThrows(
        IllegalArgumentException.class,
        () -> repository.stage(batch, List.of(row("beta", List.of(), List.of())), 2, metadata));
    assertEquals(List.of("entries:1", "evidence:1", "processed:1"), events);
    assertEquals(1, transactionManager.commits);
    assertEquals(1, transactionManager.rollbacks);
  }

  @Test
  void rollsBackAStagedChunkBeforeItsRecoveryMarkerAdvances() {
    var events = new ArrayList<String>();
    var transactionManager = new RecordingTransactionManager();
    var repository = repository(events, transactionManager, true);
    var metadata = new LexiconImportMetadata("a".repeat(64), "fixture", "MIT", Instant.EPOCH);
    var batch = new io.lexiflow.lexicon.application.StagedLexiconImport(UUID.randomUUID(), 7, 0);

    assertThrows(
        IllegalStateException.class,
        () -> repository.stage(batch, List.of(request().rows().getFirst()), 1, metadata));

    assertEquals(List.of("entries:1"), events);
    assertEquals(0, transactionManager.commits);
    assertEquals(1, transactionManager.rollbacks);
  }

  @Test
  void rollsBackWhenEntryPersistenceFailsBeforePublication() {
    var events = new ArrayList<String>();
    var transactionManager = new RecordingTransactionManager();
    var repository = repository(events, transactionManager, true);

    assertThrows(IllegalStateException.class, () -> repository.publish(request()));

    assertEquals(List.of("lock", "next", "stage:1", "entries:1"), events);
    assertEquals(0, transactionManager.commits);
    assertEquals(1, transactionManager.rollbacks);
  }

  private static DefaultLexiconRepository repository(
      List<String> events, RecordingTransactionManager transactionManager, boolean failEntries) {
    return new DefaultLexiconRepository(
        new RecordingEntryDao(events, failEntries),
        new RecordingBatchDao(events),
        (entries, version) -> events.add("evidence:" + entries.size()),
        new LexiconEntryMapper(),
        new TransactionTemplate(transactionManager));
  }

  private static LexiconImportRequest request() {
    var source = new SourceReference("fixture", "MIT", "row-1");
    var row =
        new LexiconImportRow(
            "reliable",
            "可靠的",
            "worthy of trust",
            List.of(),
            List.of(),
            new LexiconPriority(4.2, 1, 900),
            source,
            source,
            List.of(source),
            true);
    return new LexiconImportRequest(
        List.of(row), new LexiconImportMetadata("a".repeat(64), "fixture", "MIT", Instant.EPOCH));
  }

  private static LexiconImportRow row(
      String lemma, List<String> aliases, List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", lemma);
    return new LexiconImportRow(
        lemma,
        "释义",
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true);
  }

  private static final class RecordingEntryDao implements LexiconEntryDao {
    private final List<String> events;
    private final boolean fail;
    private final java.util.Map<String, String> canonicalOwners = new java.util.HashMap<>();

    RecordingEntryDao(List<String> events, boolean fail) {
      this.events = events;
      this.fail = fail;
    }

    @Override
    public List<LexiconEntryDO> findByForms(long version, Collection<String> forms) {
      return List.of();
    }

    @Override
    public List<LexiconEntryDO> findPrewarmCandidates(long version, int limit) {
      return List.of();
    }

    @Override
    public java.util.Map<String, String> findCanonicalOwners(
        long version, Collection<String> surfaces) {
      return canonicalOwners.entrySet().stream()
          .filter(entry -> surfaces.contains(entry.getKey()))
          .collect(
              java.util.stream.Collectors.toUnmodifiableMap(
                  java.util.Map.Entry::getKey, java.util.Map.Entry::getValue));
    }

    @Override
    public void insertEntries(
        List<io.lexiflow.lexicon.application.LexiconImportPlan.PlannedEntry> entries,
        long version) {
      events.add("entries:" + entries.size());
      if (fail) {
        throw new IllegalStateException("entry write failed");
      }
      entries.forEach(
          planned -> {
            canonicalOwners.put(planned.entry().lemma(), planned.entry().lemma());
            planned
                .entry()
                .aliases()
                .forEach(
                    alias -> canonicalOwners.put(alias.normalizedForm(), planned.entry().lemma()));
          });
    }

    @Override
    public long countEntries(long version) {
      return 1;
    }
  }

  private static final class RecordingBatchDao implements LexiconImportBatchDao {
    private final List<String> events;

    RecordingBatchDao(List<String> events) {
      this.events = events;
    }

    @Override
    public void lockPublication() {
      events.add("lock");
    }

    @Override
    public long nextVersion() {
      events.add("next");
      return 7;
    }

    @Override
    public long publishedVersion() {
      return 0;
    }

    @Override
    public void insertStaged(
        UUID batchId, long version, LexiconImportMetadata metadata, int entryCount) {
      events.add("stage:" + entryCount);
    }

    @Override
    public LexiconImportBatchDO findStaged(LexiconImportMetadata metadata) {
      return null;
    }

    @Override
    public void updateProcessed(UUID batchId, long processedThrough) {
      events.add("processed:" + processedThrough);
    }

    @Override
    public void completeStaged(
        UUID batchId,
        long version,
        long sourceRowsTotal,
        long expectedProcessed,
        long entryCount) {}

    @Override
    public void supersedePublished() {
      events.add("supersede");
    }

    @Override
    public void publish(UUID batchId) {
      events.add("publish");
    }
  }

  private static final class RecordingTransactionManager
      extends AbstractPlatformTransactionManager {
    private static final long serialVersionUID = 1L;
    private int commits;
    private int rollbacks;

    @Override
    protected Object doGetTransaction() {
      return new Object();
    }

    @Override
    protected void doBegin(Object transaction, TransactionDefinition definition) {}

    @Override
    protected void doCommit(DefaultTransactionStatus status) {
      commits++;
    }

    @Override
    protected void doRollback(DefaultTransactionStatus status) {
      rollbacks++;
    }
  }
}
