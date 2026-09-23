package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportRow;
import io.lexiflow.lexicon.application.SourceReference;
import io.lexiflow.lexicon.domain.LexiconPriority;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 在显式隔离 schema 上验证初始化、索引、持久化发布和查询路径。 */
@Tag("postgres")
class PostgresLexiconRepositoryIntegrationTest {
  private static final String JDBC_URL_PROPERTY = "lexiflow.postgres.test.jdbcUrl";
  private static final String SCHEMA_PROPERTY = "lexiflow.postgres.schema.file";
  private static String adminJdbcUrl;
  private static Path schemaFile;

  @BeforeAll
  static void captureProperties() {
    adminJdbcUrl = requiredJdbcUrl();
    schemaFile = Path.of(requiredSchemaFile());
  }

  @Test
  void initializesAnEmptySchemaAndCreatesAllDeclaredTablesAndIndexes() throws Exception {
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
      assertEquals(
          "6",
          scalar(
              jdbcUrl,
              "SELECT COUNT(*)::text FROM information_schema.tables "
                  + "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"));
      assertEquals(
          "lexicon_entry_lookup_idx",
          scalar(jdbcUrl, "SELECT to_regclass('lexicon_entry_lookup_idx')"));
      assertEquals(
          "lexicon_inflection_lookup_idx",
          scalar(jdbcUrl, "SELECT to_regclass('lexicon_inflection_lookup_idx')"));
      assertEquals(
          "lexicon_entry_prewarm_idx",
          scalar(jdbcUrl, "SELECT to_regclass('lexicon_entry_prewarm_idx')"));
      assertEquals(
          "lexicon_import_single_published_idx",
          scalar(jdbcUrl, "SELECT to_regclass('lexicon_import_single_published_idx')"));
      assertEquals(
          "lexicon_source_evidence_entry_idx",
          scalar(jdbcUrl, "SELECT to_regclass('lexicon_source_evidence_entry_idx')"));
    } finally {
      dropSchema(testSchema);
    }
  }

  @Test
  void rejectsNonEmptySchemaWithoutModification() throws Exception {
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
      assertThrows(
          IllegalStateException.class,
          () -> PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile));
      assertEquals(
          "6",
          scalar(
              jdbcUrl,
              "SELECT COUNT(*)::text FROM information_schema.tables "
                  + "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"));
    } finally {
      dropSchema(testSchema);
    }
  }

  @Test
  void rejectsSchemasContainingOnlyNonTableObjects() throws Exception {
    for (var ddl :
        List.of(
            "CREATE VIEW occupied AS SELECT 42 AS value",
            "CREATE SEQUENCE occupied",
            "CREATE TYPE occupied AS ENUM ('value')",
            "CREATE FUNCTION occupied() RETURNS integer LANGUAGE sql AS 'SELECT 42'")) {
      var testSchema = createSchema();
      var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
      try {
        try (var connection = DriverManager.getConnection(jdbcUrl);
            var statement = connection.createStatement()) {
          statement.execute(ddl);
        }
        var countSql =
            "SELECT COUNT(*)::text FROM pg_catalog.pg_depend "
                + "WHERE refclassid = 'pg_catalog.pg_namespace'::regclass "
                + "AND refobjid = current_schema()::regnamespace";
        var objectsBefore = scalar(jdbcUrl, countSql);
        assertThrows(
            IllegalStateException.class,
            () -> PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile));
        assertEquals(objectsBefore, scalar(jdbcUrl, countSql));
        assertEquals(
            "", scalar(jdbcUrl, "SELECT COALESCE(to_regclass('lexicon_entry')::text, '')"));
      } finally {
        dropSchema(testSchema);
      }
    }
  }

  @Test
  void rollsBackAFailingInitAndRetriesWithCorrectedSql(@TempDir Path temporaryDirectory)
      throws Exception {
    var brokenSql = temporaryDirectory.resolve("broken.sql");
    Files.writeString(
        brokenSql,
        Files.readString(schemaFile)
            + "\nCREATE TABLE atomic_probe (id INTEGER PRIMARY KEY);\nSELECT 1 / 0;\n");
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      assertThrows(
          SQLException.class, () -> PostgresSchemaInitializer.initialize(jdbcUrl, brokenSql));
      assertEquals("", scalar(jdbcUrl, "SELECT COALESCE(to_regclass('atomic_probe')::text, '')"));
      assertEquals(
          "0",
          scalar(
              jdbcUrl,
              "SELECT COUNT(*)::text FROM information_schema.tables "
                  + "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"));
      PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
      assertEquals(
          "6",
          scalar(
              jdbcUrl,
              "SELECT COUNT(*)::text FROM information_schema.tables "
                  + "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"));
    } finally {
      dropSchema(testSchema);
    }
  }

  @Test
  void publishesAndReadsACompleteLexiconEntryThroughPostgres() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            assertEquals(0, repository.publishedVersion());
            var published =
                repository.publish(
                    request(row("reliable", List.of("dependable"), List.of("reliably"))));
            assertEquals(published, repository.publishedVersion());
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
                List.of("可靠的"),
                entry.senses().stream().map(value -> value.chineseGloss()).toList());
            assertEquals(
                List.of("reliable"),
                repository.findPrewarmCandidates(published, 1).stream()
                    .map(value -> value.lemma())
                    .toList());
          }
        });
  }

  @Test
  void persistsPreparedEligibilityAndDeduplicatedGlossWithoutChangingOldPublishedData()
      throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var reference = new SourceReference("fixture", "MIT", "fixture#prepared");
            var basic =
                new LexiconImportRow(
                    "ability",
                    "能力",
                    "",
                    List.of(),
                    List.of("abilities"),
                    LexiconPriority.fromEvidence(4.3, 2),
                    reference,
                    reference,
                    List.of(),
                    true,
                    true,
                    "fixture-top2000-v1");
            var geometry =
                new LexiconImportRow(
                    "parallelogram",
                    "平行四边形；[机] 平行四边形",
                    "",
                    List.of(),
                    List.of(),
                    LexiconPriority.unranked(),
                    reference,
                    reference,
                    List.of(),
                    false,
                    false,
                    "fixture-top2000-v1");
            var version = repository.publish(request(basic, geometry));
            var loaded = repository.findByForms(version, List.of("abilities")).getFirst();
            assertEquals(
                io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY,
                loaded.hintEligibility());
            assertTrue(repository.findPrewarmCandidates(version, 2000).isEmpty());
            var shape = repository.findByForms(version, List.of("parallelogram")).getFirst();
            assertEquals("平行四边形", shape.senses().getFirst().chineseGloss());
            assertEquals(
                io.lexiflow.lexicon.domain.LexiconHintEligibility.CANDIDATE,
                shape.hintEligibility());
            assertEquals(
                "fixture-top2000-v1",
                scalar(
                    jdbcUrl,
                    "SELECT hint_policy_reference FROM lexicon_entry WHERE lemma='ability'"));
            assertEquals(
                "UNPROCESSED",
                scalar(
                        jdbcUrl,
                        "SELECT replace(column_default, chr(39), '') FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='lexicon_entry' AND column_name='hint_eligibility'")
                    .replace("::text", ""));
          }
        });
  }

  @Test
  void returnsEveryEntryForAnAmbiguousInflection() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var published =
                repository.publish(
                    request(
                        row("hang", List.of(), List.of("hung")),
                        row("sling", List.of(), List.of("hung"))));

            assertEquals(
                List.of("hang", "sling"),
                repository.findByForms(published, List.of("hung")).stream()
                    .map(value -> value.lemma())
                    .sorted()
                    .toList());
          }
        });
  }

  @Test
  void keepsThePreviousPublishedVersionVisibleAcrossFailedAndResumedStaging() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var previousPublished =
                repository.publish(request(row("reliable", List.of(), List.of())));
            var stagedMetadata = metadata('b');
            var staged = repository.openOrResume(stagedMetadata);
            assertEquals(previousPublished + 1, staged.version());

            repository.stage(
                staged, List.of(row("sling", List.of(), List.of("hung"))), 1, stagedMetadata);
            var resumed = repository.openOrResume(stagedMetadata);
            assertEquals(1, resumed.sourceRowsProcessed());
            assertThrows(
                IllegalArgumentException.class,
                () ->
                    repository.stage(
                        resumed,
                        List.of(row("rope", List.of("sling"), List.of())),
                        2,
                        stagedMetadata));

            assertEquals(previousPublished, repository.publishedVersion());
            assertEquals(
                List.of("reliable"),
                repository.findByForms(previousPublished, List.of("reliable")).stream()
                    .map(value -> value.lemma())
                    .toList());
            var resumedAfterRollback = repository.openOrResume(stagedMetadata);
            assertEquals(1, resumedAfterRollback.sourceRowsProcessed());
            repository.stage(
                resumedAfterRollback,
                List.of(row("rope", List.of(), List.of())),
                2,
                stagedMetadata);
            repository.publish(resumedAfterRollback, 2);

            assertEquals(resumedAfterRollback.version(), repository.publishedVersion());
          }
        });
  }

  private static String requiredJdbcUrl() {
    var value = System.getProperty(JDBC_URL_PROPERTY, "").trim();
    if (value.isEmpty()) {
      throw new IllegalStateException(
          "PostgreSQL integration runner requires explicit isolated " + JDBC_URL_PROPERTY);
    }
    return value;
  }

  private static String requiredSchemaFile() {
    var value = System.getProperty(SCHEMA_PROPERTY, "").trim();
    if (value.isEmpty()) {
      throw new IllegalStateException("PostgreSQL integration runner requires " + SCHEMA_PROPERTY);
    }
    return value;
  }

  private static String withSchema(String jdbcUrl, String value) {
    return jdbcUrl + (jdbcUrl.contains("?") ? "&" : "?") + "currentSchema=" + value;
  }

  private static String createSchema() throws SQLException {
    var value = "lexiflow_test_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + value + "\"");
    }
    return value;
  }

  private static void dropSchema(String value) throws SQLException {
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("DROP SCHEMA IF EXISTS \"" + value + "\" CASCADE");
    }
  }

  private static String scalar(String jdbcUrl, String sql) throws SQLException {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement();
        var rows = statement.executeQuery(sql)) {
      assertTrue(rows.next());
      return rows.getString(1);
    }
  }

  private static void inInitializedSchema(JdbcUrlTest test) throws Exception {
    var testSchema = createSchema();
    var jdbcUrl = withSchema(adminJdbcUrl, testSchema);
    try {
      PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
      test.run(jdbcUrl);
    } finally {
      dropSchema(testSchema);
    }
  }

  private static LexiconImportRequest request(LexiconImportRow... rows) {
    return new LexiconImportRequest(List.of(rows), metadata('a'));
  }

  private static LexiconImportMetadata metadata(char digestCharacter) {
    return new LexiconImportMetadata(
        String.valueOf(digestCharacter).repeat(64), "fixture", "MIT", Instant.EPOCH);
  }

  private static LexiconImportRow row(
      String lemma, List<String> aliases, List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        "可靠的",
        "fixture definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true);
  }

  @FunctionalInterface
  private interface JdbcUrlTest {
    void run(String jdbcUrl) throws Exception;
  }
}
