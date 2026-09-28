package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.query.CachedLexiconQueryService;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.IOException;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;

/** 隔离 PostgreSQL schema 验证三表结构、完整发布、单表查询及回滚。 */
@Tag("postgres")
class PostgresLexiconRepositoryIntegrationTest {
  private static String adminJdbcUrl;
  private static Path schemaFile;

  @BeforeAll
  static void captureProperties() {
    adminJdbcUrl = required("lexiflow.postgres.test.jdbcUrl");
    schemaFile = Path.of(required("lexiflow.postgres.schema.file"));
  }

  @Test
  void declaresOnlyThreeTablesAndDocumentsEveryColumn() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          assertEquals(
              "3",
              scalar(
                  jdbcUrl,
                  "SELECT COUNT(*)::text FROM information_schema.tables "
                      + "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"));
          assertEquals(
              "0",
              scalar(
                  jdbcUrl,
                  "SELECT COUNT(*)::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                      + "JOIN pg_namespace n ON n.oid=c.relnamespace "
                      + "WHERE n.nspname=current_schema() AND c.relname LIKE 'lexicon_%' "
                      + "AND c.relkind='r' AND a.attnum > 0 AND NOT a.attisdropped "
                      + "AND col_description(c.oid,a.attnum) IS NULL"));
          assertTrue(
              scalar(jdbcUrl, "SELECT pg_get_indexdef('lexicon_hint_lookup_pk'::regclass)")
                  .contains("(language_tag, normalized_form, lexicon_entry_id)"));
          assertTrue(
              scalar(
                      jdbcUrl,
                      "SELECT pg_get_indexdef('lexicon_prepared_entry_language_lemma_uk'::regclass)")
                  .contains("(language_tag, lemma)"));
          assertTrue(
              scalar(jdbcUrl, "SELECT pg_get_indexdef('lexicon_hint_lookup_prewarm_idx'::regclass)")
                  .contains("(language_tag, final_action, cache_priority DESC, normalized_form)"));
        });
  }

  @Test
  void publishesCompleteDataAndQueriesOneServingTableIncludingAmbiguity() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            assertEquals(0, repository.publishedVersion());
            var first = row("reliable", "可靠的", List.of("dependable"), List.of("reliably"));
            var second = row("reliably", "可靠地", List.of(), List.of());
            assertEquals(
                1,
                repository.publish(
                    new LexiconImportRequest(List.of(first, second), metadata('a'))));
            assertEquals(1, repository.publishedVersion());
            var matches = repository.findByForms(1, List.of("reliable", "dependable", "reliably"));
            assertEquals(4, matches.size());
            assertEquals(
                2,
                matches.stream()
                    .filter(value -> value.normalizedForm().equals("reliably"))
                    .count());
            assertEquals("4", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_hint_lookup"));
            assertEquals("2", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_prepared_entry"));
            assertEquals("1", scalar(jdbcUrl, "SELECT lexicon_version::text FROM lexicon_dataset"));
            assertEquals(0, repository.findByForms(0, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(2, List.of("reliable")).size());
            assertEquals(
                1,
                repository.findPrewarmForms(1, LexiconHintAction.HINT, 1).stream()
                    .map(value -> value.normalizedForm())
                    .distinct()
                    .count());
          }
        });
  }

  @Test
  void prewarmReturnsEveryOwnerOfAnAmbiguousSelectedForm() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
                new LexiconImportRequest(
                    List.of(
                        row("zoology", "动物学", List.of(), List.of("able")),
                        row("zymurgy", "酿造学", List.of(), List.of("able"))),
                    metadata('c')));
            var candidates = repository.findPrewarmForms(1, LexiconHintAction.HINT, 1);
            assertEquals(2, candidates.size());
            assertEquals(
                List.of("able", "able"),
                candidates.stream().map(value -> value.normalizedForm()).toList());
          }
        });
  }

  @Test
  void blocksUnsafeGlossButWarmsFixedNegativeFormAndKeepsRawEvidence() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
                new LexiconImportRequest(
                    List.of(
                        row("the", "这个", List.of(), List.of()),
                        row("sustainability", "可持续性；持续性", List.of(), List.of())),
                    metadata('b')));
            assertEquals(
                LexiconHintAction.BLOCK,
                repository.findByForms(1, List.of("the")).getFirst().finalAction());
            assertEquals(
                LexiconHintAction.BLOCK,
                repository.findByForms(1, List.of("sustainability")).getFirst().finalAction());
            assertEquals(
                "the",
                repository
                    .findPrewarmForms(1, LexiconHintAction.BLOCK, 1)
                    .getFirst()
                    .normalizedForm());
            assertEquals(
                "可持续性；持续性",
                scalar(
                    jdbcUrl,
                    "SELECT source_gloss FROM lexicon_prepared_entry WHERE lemma='sustainability'"));
            assertEquals(
                "unsafe_default_candidate",
                scalar(
                    jdbcUrl,
                    "SELECT exclusion_reason FROM lexicon_prepared_entry WHERE lemma='sustainability'"));
          }
        });
  }

  @Test
  void failedStreamingReplacementRollsBackAllRowsAndVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
                new LexiconImportRequest(
                    List.of(row("reliable", "可靠的", List.of(), List.of())), metadata('a')));
            assertThrows(
                RuntimeException.class,
                () ->
                    repository.publishStreaming(
                        metadata('b'),
                        2,
                        2,
                        consumer -> {
                          consumer.accept(row("context", "语境", List.of(), List.of()));
                          throw new IOException("source disappeared");
                        }));
            assertEquals(1, repository.publishedVersion());
            assertEquals(1, repository.findByForms(1, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(1, List.of("context")).size());
          }
        });
  }

  @Test
  void publishesFirstCandidateAsPreparedGlossAndPreservesFullSourceGloss() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var multiSense =
                rowWithSource("bank", "银行", "银行；河岸", List.of("banks"), List.of("banked"));
            var singleSense = rowWithSource("reliable", "可靠的", "可靠的", List.of(), List.of());
            repository.publish(
                new LexiconImportRequest(List.of(multiSense, singleSense), metadata('d')));
            assertEquals(
                "银行；河岸",
                scalar(
                    jdbcUrl, "SELECT source_gloss FROM lexicon_prepared_entry WHERE lemma='bank'"));
            assertEquals(
                "银行",
                scalar(
                    jdbcUrl,
                    "SELECT prepared_gloss FROM lexicon_prepared_entry WHERE lemma='bank'"));
            assertEquals(
                "HINT",
                scalar(
                    jdbcUrl,
                    "SELECT final_action FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='bank' AND form_kind='lemma'"));
            assertEquals(
                "银行",
                scalar(
                    jdbcUrl,
                    "SELECT final_gloss FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='bank' AND form_kind='lemma'"));
            assertEquals(
                "银行",
                scalar(
                    jdbcUrl,
                    "SELECT final_gloss FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='banks' AND form_kind='alias'"));
            assertEquals(
                "银行",
                scalar(
                    jdbcUrl,
                    "SELECT final_gloss FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='banked' AND form_kind='inflection'"));
            var matches = repository.findByForms(1, List.of("bank", "banks", "banked", "reliable"));
            assertEquals(4, matches.size());
            assertTrue(matches.stream().allMatch(m -> m.finalAction() == LexiconHintAction.HINT));
            assertEquals(
                "可靠的",
                scalar(
                    jdbcUrl,
                    "SELECT final_gloss FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='reliable' AND form_kind='lemma'"));
          }
        });
  }

  @Test
  void blocksInvalidFirstCandidateAndPreservesSourceGloss() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var emptyFirst = rowWithSource("emptyfirst", "", "；银行", List.of(), List.of());
            repository.publish(new LexiconImportRequest(List.of(emptyFirst), metadata('e')));
            assertEquals(
                LexiconHintAction.BLOCK,
                repository.findByForms(1, List.of("emptyfirst")).getFirst().finalAction());
            assertEquals(
                "；银行",
                scalar(
                    jdbcUrl,
                    "SELECT source_gloss FROM lexicon_prepared_entry WHERE lemma='emptyfirst'"));
            assertEquals(
                "unsafe_default_candidate",
                scalar(
                    jdbcUrl,
                    "SELECT exclusion_reason FROM lexicon_prepared_entry "
                        + "WHERE lemma='emptyfirst'"));
          }
        });
  }

  @Test
  void versionSwitchUpdatesSenseIdentityAndInvalidatesOldVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
                new LexiconImportRequest(
                    List.of(row("context", "语境", List.of(), List.of())), metadata('f')));
            assertEquals(1, repository.publishedVersion());
            var v1 = repository.findByForms(1, List.of("context")).getFirst();
            assertEquals("语境", v1.finalGloss());
            assertEquals(1, v1.lexiconVersion());
            repository.publish(
                new LexiconImportRequest(
                    List.of(row("context", "环境", List.of(), List.of())), metadata('g')));
            assertEquals(2, repository.publishedVersion());
            assertEquals(0, repository.findByForms(1, List.of("context")).size());
            var v2 = repository.findByForms(2, List.of("context")).getFirst();
            assertEquals("环境", v2.finalGloss());
            assertEquals(2, v2.lexiconVersion());
            assertFalse(v1.senseId().equals(v2.senseId()));
          }
        });
  }

  @Test
  void oxfordPublicationBlocksAllFormsAndInvalidatesWarmHintsWithoutBlockingPhrases()
      throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
                new LexiconImportRequest(
                    List.of(row("false", "错误的", List.of(), List.of())), metadata('a')));
            var cache = new CachedLexiconQueryService(repository, 20, 1, 1);
            assertEquals(
                LexiconHintAction.HINT, cache.candidatesFor("false").getFirst().finalAction());
            var source = new SourceReference("fixture", "MIT", "oxford");
            var words = new java.util.ArrayList<LexiconImportRow>();
            for (var lemma : List.of("false", "ability", "false alarm")) {
              words.add(
                  new LexiconImportRow(
                      lemma,
                      "可靠短释",
                      "",
                      lemma.equals("ability") ? List.of("abilityalias") : List.of(),
                      lemma.equals("ability") ? List.of("abilities") : List.of(),
                      new LexiconPriority(4.2, 1, 900),
                      source,
                      source,
                      List.of(source),
                      true,
                      false,
                      "oxford-all-words-fixture",
                      "可靠短释",
                      lemma.equals("false") ? 2370L : null,
                      null,
                      List.of(),
                      true));
            }
            words.add(row("specialist", "专家", List.of(), List.of()));
            repository.publish(new LexiconImportRequest(words, metadata('b')));
            for (var form : List.of("false", "FALSE", "ability", "abilities", "abilityalias")) {
              var candidate = cache.candidatesFor(form).getFirst();
              assertEquals(2, candidate.lexiconVersion());
              assertEquals(LexiconHintAction.BLOCK, candidate.finalAction(), form);
            }
            for (var form : List.of("false alarm", "specialist")) {
              assertEquals(
                  LexiconHintAction.HINT, cache.candidatesFor(form).getFirst().finalAction(), form);
            }
            assertEquals(
                "2",
                scalar(
                    jdbcUrl,
                    "SELECT COUNT(*)::text FROM lexicon_prepared_entry "
                        + "WHERE source_oxford_basic AND exclusion_reason='basic_vocabulary'"));
          }
        });
  }

  private static LexiconImportRow rowWithSource(
      String lemma,
      String chineseGloss,
      String sourceGloss,
      List<String> aliases,
      List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        chineseGloss,
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true,
        false,
        "first-candidate-v3",
        sourceGloss,
        null,
        null,
        List.of(),
        false);
  }

  private static LexiconImportRow row(
      String lemma, String gloss, List<String> aliases, List<String> inflections) {
    var source = new SourceReference("fixture", "MIT", "row-" + lemma);
    return new LexiconImportRow(
        lemma,
        gloss,
        "definition",
        aliases,
        inflections,
        new LexiconPriority(4.2, 1, 900),
        source,
        source,
        List.of(source),
        true);
  }

  private static LexiconImportMetadata metadata(char digest) {
    return new LexiconImportMetadata(
        String.valueOf(digest).repeat(64), "fixture", "MIT", Instant.EPOCH);
  }

  private static String required(String property) {
    var value = System.getProperty(property, "").trim();
    if (value.isEmpty())
      throw new IllegalStateException(property + " must be configured for isolated PostgreSQL");
    return value;
  }

  private static String withSchema(String jdbcUrl, String name) {
    return jdbcUrl + (jdbcUrl.contains("?") ? "&" : "?") + "currentSchema=" + name;
  }

  private static void inInitializedSchema(JdbcUrlTest test) throws Exception {
    var schema = "lexiflow_test_" + UUID.randomUUID().toString().replace("-", "");
    try (var connection = DriverManager.getConnection(adminJdbcUrl);
        var statement = connection.createStatement()) {
      statement.execute("CREATE SCHEMA \"" + schema + "\"");
    }
    try {
      var jdbcUrl = withSchema(adminJdbcUrl, schema);
      PostgresSchemaInitializer.initialize(jdbcUrl, schemaFile);
      test.run(jdbcUrl);
    } finally {
      try (var connection = DriverManager.getConnection(adminJdbcUrl);
          var statement = connection.createStatement()) {
        statement.execute("DROP SCHEMA IF EXISTS \"" + schema + "\" CASCADE");
      }
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

  @FunctionalInterface
  private interface JdbcUrlTest {
    void run(String jdbcUrl) throws Exception;
  }
}
