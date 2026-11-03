package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.sql.SQLException;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;

/** 真实 SQLite 集成测试：稳定身份、同形歧义、BLOCK、预热、事务回滚及锁边界。 */
@Tag("sqlite")
class SqliteLexiconRepositoryIntegrationTest {
  @TempDir Path temporary;

  @Test
  void publishesExactCanonicalAndPhysicalFormsWithAmbiguityBlockAndStableIds() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    long firstVersion =
        SqliteTestSupport.publish(
            file,
            SqliteTestSupport.row("reliable", "可靠的", List.of("dependable"), List.of("reliably")),
            SqliteTestSupport.row("reliably", "可靠地", List.of(), List.of()),
            SqliteTestSupport.blockedRow("reliability", "释义", List.of(), List.of("reliable")));
    try (var persistence = SqlitePersistence.open(SqliteTestSupport.url(file))) {
      var repository = persistence.repository();
      var reliable = repository.findByForms(firstVersion, List.of("reliable"));
      assertEquals(2, reliable.size());
      assertEquals(
          1, reliable.stream().filter(v -> v.finalAction() == LexiconHintAction.HINT).count());
      assertEquals(
          1, reliable.stream().filter(v -> v.finalAction() == LexiconHintAction.BLOCK).count());
      assertEquals(2, repository.findByForms(firstVersion, List.of("reliably")).size());
      assertEquals(0, repository.findByForms(firstVersion, List.of("reli")).size());
      assertEquals(1, repository.findByForms(firstVersion, List.of("dependable")).size());
      try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
        assertEquals(
            3,
            SqliteTestSupport.scalar(
                connection, "SELECT COUNT(*) FROM lexicon_entry WHERE canonical_form=1"));
        assertEquals(3, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_form"));
        assertEquals(
            6, SqliteTestSupport.scalar(connection, "SELECT COUNT(*) FROM lexicon_lookup"));
      }
      assertEquals(firstVersion, repository.publishedVersion());
      assertEquals(
          1,
          repository.findPrewarmForms(firstVersion, LexiconHintAction.HINT, 1).stream()
              .map(v -> v.normalizedForm())
              .distinct()
              .count());
      assertEquals(
          1,
          repository.findPrewarmForms(firstVersion, LexiconHintAction.BLOCK, 1).stream()
              .map(v -> v.normalizedForm())
              .distinct()
              .count());
      var firstIds = reliable.stream().map(v -> v.entryId()).sorted().toList();
      long secondVersion =
          SqliteTestSupport.publish(
              file,
              SqliteTestSupport.row("reliable", "可靠的", List.of("dependable"), List.of("reliably")),
              SqliteTestSupport.row("reliably", "可靠地", List.of(), List.of()),
              SqliteTestSupport.blockedRow("reliability", "释义", List.of(), List.of("reliable")));
      assertTrue(secondVersion > firstVersion);
      assertEquals(
          firstIds,
          repository.findByForms(secondVersion, List.of("reliable")).stream()
              .map(v -> v.entryId())
              .sorted()
              .toList());
      assertNotEquals(firstVersion, repository.publishedVersion());
    }
  }

  @Test
  void failedPublicationRollsBackPreviousCompleteVersionAndFloorIsMonotonic() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    long published =
        SqliteTestSupport.publish(
            file, SqliteTestSupport.row("reliable", "可靠的", List.of(), List.of()));
    try (var persistence = SqlitePersistence.open(SqliteTestSupport.url(file))) {
      var repository = persistence.repository();
      LexiconImportMetadata metadata = SqliteTestSupport.metadata("changed-source");
      var failure =
          assertThrows(
              UncheckedIOException.class,
              () ->
                  repository.publish(
                      metadata,
                      2,
                      2,
                      (version, consumer) -> {
                        consumer.accept(
                            LexiconImportPlan.prepare(
                                    List.of(
                                        SqliteTestSupport.row(
                                            "changed", "变化", List.of(), List.of())),
                                    version,
                                    metadata.sourceDigest(),
                                    metadata.acquiredAt())
                                .getFirst());
                        throw new IOException("source changed during publication");
                      },
                      new LexiconPublicationRepository.PublicationProgress() {
                        @Override
                        public void persistStarted() {}

                        @Override
                        public void persisted(long entries, long lookups) {}

                        @Override
                        public void rolledBack() {}
                      }));
      assertTrue(failure.getMessage().contains("source changed"));
      assertEquals(published, repository.publishedVersion());
      assertEquals(1, repository.findByForms(published, List.of("reliable")).size());
    }
  }

  @Test
  void usesOnePriorityForHintDisplayAndPrewarmWhileZeroHintRemainsHint() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    long version =
        SqliteTestSupport.publish(
            file,
            SqliteTestSupport.row("priorityhint", "优先提示", List.of(), List.of()),
            SqliteTestSupport.row("quiet-hint", "仍可查询", List.of(), List.of(), false));
    try (var persistence = SqlitePersistence.open(SqliteTestSupport.url(file))) {
      var repository = persistence.repository();
      var displayed = repository.findByForms(version, List.of("priorityhint")).getFirst();
      assertEquals(LexiconHintAction.HINT, displayed.finalAction());
      assertEquals(900, displayed.priority());
      var warmed =
          repository.findPrewarmForms(version, LexiconHintAction.HINT, 10).stream()
              .filter(candidate -> candidate.normalizedForm().equals("priorityhint"))
              .findFirst()
              .orElseThrow();
      assertEquals(displayed.priority(), warmed.priority());

      var zeroPriority = repository.findByForms(version, List.of("quiet-hint")).getFirst();
      assertEquals(LexiconHintAction.HINT, zeroPriority.finalAction());
      assertEquals(0, zeroPriority.priority());
      assertTrue(
          repository.findPrewarmForms(version, LexiconHintAction.HINT, 10).stream()
              .noneMatch(candidate -> candidate.normalizedForm().equals("quiet-hint")));
    }
  }

  @Test
  void externalSingleConnectionNestedTransactionDoesNotReconfigureJournal() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    try (var connection = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
      connection.setAutoCommit(false);
      try (var source = new SingleConnectionDataSource(connection, true)) {
        var repository = SqlitePersistence.repository(source, 9_000_000_000_000L);
        assertEquals(0, repository.publishedVersion());
        connection.rollback();
        assertEquals("delete", pragmaText(connection, "journal_mode"));
        assertEquals(1, pragmaInt(connection, "foreign_keys"));
      }
    }
  }

  @Test
  void concurrentWriterWaitsOnlyForBusyTimeout() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    SqliteTestSupport.publish(file, SqliteTestSupport.row("reliable", "可靠的", List.of(), List.of()));
    try (var lock = SqlitePersistence.connect(SqliteTestSupport.url(file));
        var persistence = SqlitePersistence.open(SqliteTestSupport.url(file));
        var executor = Executors.newSingleThreadExecutor()) {
      lock.setAutoCommit(false);
      try (var statement = lock.createStatement()) {
        statement.executeUpdate("UPDATE lexicon_entry SET priority=priority");
      }
      var future =
          executor.submit(
              () ->
                  persistence
                      .repository()
                      .publish(
                          SqliteTestSupport.metadata("second"),
                          1,
                          1,
                          (version, consumer) ->
                              consumer.accept(
                                  LexiconImportPlan.prepare(
                                          List.of(
                                              SqliteTestSupport.row(
                                                  "dependable", "可靠", List.of(), List.of())),
                                          version,
                                          SqliteTestSupport.metadata("second").sourceDigest(),
                                          SqliteTestSupport.metadata("second").acquiredAt())
                                      .getFirst())));
      long started = System.nanoTime();
      assertThrows(ExecutionException.class, () -> future.get(8, TimeUnit.SECONDS));
      long waitedMillis = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started);
      assertTrue(
          waitedMillis >= 4_500 && waitedMillis < 8_000,
          "unexpected SQLite writer wait: " + waitedMillis);
      lock.rollback();
      assertTrue(persistence.repository().publishedVersion() > 0);
    }
  }

  @Test
  void readerTransactionKeepsContentAndVersionOnOneSnapshot() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    long first =
        SqliteTestSupport.publish(
            file, SqliteTestSupport.row("reliable", "可靠的", List.of(), List.of()));
    String readUrl = SqliteTestSupport.url(file) + "?mode=ro";
    try (var reader = SqlitePersistence.connect(readUrl);
        var readerSource = new SingleConnectionDataSource(reader, true);
        var writer = SqlitePersistence.open(SqliteTestSupport.url(file))) {
      reader.setAutoCommit(false);
      var jdbc = new org.springframework.jdbc.core.JdbcTemplate(readerSource);
      assertEquals(
          first,
          jdbc.queryForObject(
              "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1", Long.class));
      try (var executor = Executors.newSingleThreadExecutor()) {
        var future =
            executor.submit(
                () ->
                    writer
                        .repository()
                        .publish(
                            SqliteTestSupport.metadata("snapshot-next"),
                            1,
                            1,
                            (version, consumer) ->
                                consumer.accept(
                                    LexiconImportPlan.prepare(
                                            List.of(
                                                SqliteTestSupport.row(
                                                    "dependable", "可靠", List.of(), List.of())),
                                            version,
                                            SqliteTestSupport.metadata("snapshot-next")
                                                .sourceDigest(),
                                            SqliteTestSupport.metadata("snapshot-next")
                                                .acquiredAt())
                                        .getFirst())));
        assertTrue(!future.isDone());
        assertEquals(
            first,
            jdbc.queryForObject(
                "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1", Long.class));
        assertEquals(
            1,
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM lexicon_lookup WHERE normalized_form='reliable'",
                Integer.class));
        assertEquals(
            0,
            jdbc.queryForObject(
                "SELECT COUNT(*) FROM lexicon_lookup WHERE normalized_form='dependable'",
                Integer.class));
        reader.commit();
        assertTrue(future.get(8, TimeUnit.SECONDS) > first);
      }
    }
  }

  @Test
  void closingUncommittedWriterRollsBackPartialChanges() throws Exception {
    Path file = SqliteTestSupport.newDatabase(temporary);
    SqliteTestSupport.initialize(file);
    long published =
        SqliteTestSupport.publish(
            file, SqliteTestSupport.row("reliable", "可靠的", List.of(), List.of()));
    try (var writer = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
      writer.setAutoCommit(false);
      try (var statement = writer.createStatement()) {
        statement.executeUpdate("DELETE FROM lexicon_dataset");
        statement.executeUpdate("DELETE FROM lexicon_entry");
      }
    }
    try (var check = SqlitePersistence.connect(SqliteTestSupport.url(file))) {
      assertEquals(
          published,
          SqliteTestSupport.scalar(
              check, "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1"));
      assertEquals(
          1,
          SqliteTestSupport.scalar(
              check, "SELECT COUNT(*) FROM lexicon_lookup WHERE normalized_form='reliable'"));
    }
  }

  private static String pragmaText(java.sql.Connection c, String name) throws SQLException {
    try (var s = c.createStatement();
        var r = s.executeQuery("PRAGMA " + name)) {
      r.next();
      return r.getString(1);
    }
  }

  private static int pragmaInt(java.sql.Connection c, String name) throws SQLException {
    try (var s = c.createStatement();
        var r = s.executeQuery("PRAGMA " + name)) {
      r.next();
      return r.getInt(1);
    }
  }
}
