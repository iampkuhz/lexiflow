package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.zaxxer.hikari.HikariDataSource;
import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportRow;
import io.lexiflow.lexicon.application.SourceReference;
import io.lexiflow.lexicon.domain.LexiconPriority;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/** Exercises the published PostgreSQL DAO composition against the migrated schema. */
@Tag("postgres")
class PostgresLexiconRepositoryIntegrationTest {
  private static final String JDBC_URL_PROPERTY = "lexiflow.postgres.test.jdbcUrl";

  @Test
  void publishesAndReadsACompleteLexiconEntryThroughPostgres() throws Exception {
    var jdbcUrl = requiredJdbcUrl();
    var dataSource = new HikariDataSource();
    dataSource.setJdbcUrl(jdbcUrl);
    try (dataSource;
        var persistence = PostgresPersistence.open(jdbcUrl)) {
      try (var connection = dataSource.getConnection()) {
        assertTrue(connection.getMetaData().getDatabaseProductName().contains("PostgreSQL"));
      }

      var repository = persistence.repository();
      assertEquals(0, repository.publishedVersion());

      var published = repository.publish(request());

      assertEquals(1, published);
      assertEquals(1, repository.publishedVersion());
      var entry =
          repository
              .findByForms(published, List.of("reliable", "dependable", "reliably"))
              .getFirst();
      assertEquals("reliable", entry.lemma());
      assertEquals(
          List.of("dependable"),
          entry.aliases().stream().map(value -> value.normalizedForm()).toList());
      assertEquals(
          List.of("reliably"),
          entry.inflections().stream().map(value -> value.normalizedForm()).toList());
      assertEquals(
          List.of("可靠的"), entry.senses().stream().map(value -> value.chineseGloss()).toList());
      assertEquals(
          List.of("reliable"),
          repository.findPrewarmCandidates(published, 1).stream()
              .map(value -> value.lemma())
              .toList());
    }
  }

  private static String requiredJdbcUrl() {
    var value = System.getProperty(JDBC_URL_PROPERTY, "").trim();
    if (value.isEmpty()) {
      throw new IllegalStateException(
          "PostgreSQL integration runner must provide " + JDBC_URL_PROPERTY);
    }
    return value;
  }

  private static LexiconImportRequest request() {
    var source = new SourceReference("fixture", "MIT", "row-1");
    var row =
        new LexiconImportRow(
            "reliable",
            "可靠的",
            "worthy of trust",
            List.of("dependable"),
            List.of("reliably"),
            new LexiconPriority(4.2, 1, 900),
            source,
            source,
            List.of(source),
            true);
    return new LexiconImportRequest(
        List.of(row), new LexiconImportMetadata("a".repeat(64), "fixture", "MIT", Instant.EPOCH));
  }
}
