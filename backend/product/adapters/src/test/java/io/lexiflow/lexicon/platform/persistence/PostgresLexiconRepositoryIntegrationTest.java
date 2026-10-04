package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.LexiconImportObservation;
import io.lexiflow.lexicon.application.importing.LexiconImportService;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.application.query.CachedLexiconQueryService;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.IOException;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;

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
              scalar(jdbcUrl, "SELECT pg_get_indexdef('lexicon_form_pk'::regclass)")
                  .contains("(normalized_form, entry_id)"));
          assertTrue(
              scalar(jdbcUrl, "SELECT pg_get_indexdef('lexicon_entry_lemma_key'::regclass)")
                  .contains("(lemma)"));
          assertTrue(
              scalar(jdbcUrl, "SELECT pg_get_indexdef('lexicon_entry_prewarm_hint_idx'::regclass)")
                  .contains("cache_priority DESC, entry_id"));
          assertEquals(
              "entry_id,lemma,gloss,ranked_word,hint_priority,complex_list_count,cache_priority",
              scalar(
                  jdbcUrl,
                  "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
                      + "FROM information_schema.columns WHERE table_schema=current_schema() "
                      + "AND table_name='lexicon_entry'"));
          assertEquals(
              "normalized_form,entry_id",
              scalar(
                  jdbcUrl,
                  "SELECT string_agg(column_name, ',' ORDER BY ordinal_position) "
                      + "FROM information_schema.columns WHERE table_schema=current_schema() "
                      + "AND table_name='lexicon_form'"));
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
            long version =
                new LexiconImportService(repository)
                    .publish(
                        new io.lexiflow.lexicon.application.importing.model.LexiconImportRequest(
                            List.of(first, second), metadata('a')));
            assertTrue(version > 9_000_000_000L);
            assertEquals(version, repository.publishedVersion());
            var matches =
                repository.findByForms(version, List.of("reliable", "dependable", "reliably"));
            assertEquals(4, matches.size());
            assertEquals(
                2,
                matches.stream()
                    .filter(value -> value.normalizedForm().equals("reliably"))
                    .count());
            assertEquals("4", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_form"));
            assertEquals("2", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_entry"));
            assertEquals(
                Long.toString(version),
                scalar(jdbcUrl, "SELECT lexicon_version::text FROM lexicon_dataset"));
            assertEquals(0, repository.findByForms(0, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(version + 1, List.of("reliable")).size());
            assertEquals(
                1,
                repository
                    .findPrewarmForms(repository.publishedVersion(), LexiconHintAction.HINT, 1)
                    .stream()
                    .map(value -> value.normalizedForm())
                    .distinct()
                    .count());
          }
        });
  }

  @Test
  void springProvidesOneRepositoryForReadAndPublicationRolesAndKeepsAtomicVersions()
      throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var context = new AnnotationConfigApplicationContext()) {
            context
                .getEnvironment()
                .getPropertySources()
                .addFirst(
                    new MapPropertySource(
                        "isolated-postgres", Map.of("spring.datasource.url", jdbcUrl)));
            context.register(PostgresPersistenceConfiguration.class);
            context.refresh();

            var read = context.getBean(LexiconReadRepository.class);
            var publication = context.getBean(LexiconPublicationRepository.class);
            var aggregate = context.getBean(LexiconRepository.class);
            assertSame(read, publication);
            assertSame(read, aggregate);
            assertEquals(0, read.publishedVersion());

            var hint = row("bore", "钻孔", List.of(), List.of());
            var block = row("bear", "熊；承受", List.of(), List.of("bore"));
            long firstVersion =
                new LexiconImportService(publication)
                    .publish(
                        new io.lexiflow.lexicon.application.importing.model.LexiconImportRequest(
                            List.of(hint, block), metadata('m')));
            var mixed = read.findByForms(firstVersion, List.of("bore"));
            assertEquals(2, mixed.size());
            assertEquals(
                java.util.Set.of(LexiconHintAction.HINT, LexiconHintAction.BLOCK),
                mixed.stream()
                    .map(candidate -> candidate.finalAction())
                    .collect(java.util.stream.Collectors.toSet()));

            assertThrows(
                RuntimeException.class,
                () ->
                    new LexiconImportService(publication)
                        .publishStreaming(
                            metadata('n'),
                            2,
                            1,
                            consumer -> {
                              consumer.accept(row("replacement", "替换资料", List.of(), List.of()));
                              throw new IOException("synthetic publication failure");
                            }));
            assertEquals(firstVersion, read.publishedVersion());
            assertEquals(2, read.findByForms(firstVersion, List.of("bore")).size());
            assertEquals(0, read.findByForms(firstVersion, List.of("replacement")).size());

            long secondVersion =
                new LexiconImportService(publication)
                    .publish(
                        new LexiconImportRequest(
                            List.of(row("replacement", "替换资料", List.of(), List.of())),
                            metadata('o')));
            assertTrue(secondVersion > firstVersion);
            assertEquals(secondVersion, read.publishedVersion());
            assertEquals(0, read.findByForms(firstVersion, List.of("replacement")).size());
            assertEquals(1, read.findByForms(secondVersion, List.of("replacement")).size());
          }
        });
  }

  @Test
  void publishesOnlyPreparedRuntimeValuesAndKeepsSourceEvidenceOutOfTables() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var stardict = preparedRow("medical-term", "[医]甲[床]瘤", "甲床瘤", false, false, List.of());
            var csvDictionary = new SourceReference("lexicon-csv", "CC0", "csv-dictionary-row");
            var csvFrequency = new SourceReference("zipf-csv", "CC0", "csv-frequency-row");
            var zeroFrequency =
                new LexiconImportRow(
                    "zero-frequency",
                    "零频词",
                    "",
                    List.of(),
                    List.of(),
                    new LexiconPriority(0, 0, 100),
                    csvDictionary,
                    csvFrequency,
                    List.of(),
                    true);
            var blocked = row("unsafe-medical", "可持续性；持续性", List.of(), List.of());
            var aliasOwner =
                new LexiconImportRow(
                    "alias-owner",
                    "拥有别名",
                    "",
                    List.of("owner-alias"),
                    List.of(),
                    new LexiconPriority(4.2, 1, 100),
                    csvDictionary,
                    csvFrequency,
                    List.of(),
                    true);
            long version =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(stardict, zeroFrequency, blocked, aliasOwner), metadata('i')));
            assertEquals(
                "甲床瘤",
                scalar(jdbcUrl, "SELECT gloss FROM lexicon_entry WHERE lemma='medical-term'"));
            assertEquals(
                "false",
                scalar(
                    jdbcUrl,
                    "SELECT ranked_word::text FROM lexicon_entry WHERE lemma='medical-term'"));
            assertEquals(
                "false",
                scalar(
                    jdbcUrl,
                    "SELECT ranked_word::text FROM lexicon_entry WHERE lemma='zero-frequency'"));
            var zeroFrequencyMatches = repository.findByForms(version, List.of("zero-frequency"));
            assertEquals(1, zeroFrequencyMatches.size());
            assertEquals(LexiconHintAction.HINT, zeroFrequencyMatches.getFirst().finalAction());
            assertEquals("零频词", zeroFrequencyMatches.getFirst().finalGloss());
            assertEquals(1, repository.findByForms(version, List.of("owner-alias")).size());
            assertEquals(0, repository.findByForms(version, List.of("one two three four")).size());
            assertEquals(
                LexiconHintAction.BLOCK,
                repository
                    .findByForms(version, List.of("unsafe-medical"))
                    .getFirst()
                    .finalAction());
            assertEquals(
                "0",
                scalar(
                    jdbcUrl,
                    "SELECT COUNT(*)::text FROM information_schema.columns "
                        + "WHERE table_schema=current_schema() AND table_name IN ('lexicon_entry','lexicon_form') "
                        + "AND column_name IN ('source_gloss','source_gloss_ref','decisive_rule','matched_rules','frequency_zipf')"));
          }
        });
  }

  @Test
  void rejectsInvalidPublicationEvidenceAndRollsBackReplacement() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            long firstVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(
                                row("reliable", "可靠的", List.of(), List.of()),
                                row("ambiguous", "含混；歧义", List.of(), List.of())),
                            metadata('j')));
            for (var invalidSql :
                List.of(
                    "UPDATE lexicon_dataset SET source_manifest='[]'::jsonb",
                    "UPDATE lexicon_dataset SET entry_count=3, lookup_count=3",
                    "UPDATE lexicon_dataset SET source_row_count=0",
                    "UPDATE lexicon_dataset SET preparation_policy=chr(9)||chr(10)",
                    "UPDATE lexicon_entry SET lemma=chr(9)||chr(10)",
                    "UPDATE lexicon_entry SET gloss=chr(9)||chr(10) WHERE gloss IS NOT NULL",
                    "UPDATE lexicon_entry SET ranked_word=NULL",
                    "UPDATE lexicon_entry SET hint_priority=1001",
                    "UPDATE lexicon_entry SET complex_list_count=-1",
                    "UPDATE lexicon_entry SET cache_priority=1001",
                    "UPDATE lexicon_form SET normalized_form=chr(9)||chr(10)",
                    "UPDATE lexicon_form SET normalized_form='uh'",
                    "UPDATE lexicon_form SET normalized_form='one two three four'",
                    "UPDATE lexicon_form SET entry_id=0")) {
              assertThrows(SQLException.class, () -> execute(jdbcUrl, invalidSql), invalidSql);
            }
            assertThrows(
                RuntimeException.class,
                () ->
                    new LexiconImportService(repository)
                        .publishStreaming(
                            metadata('k'),
                            2,
                            2,
                            consumer -> {
                              var duplicate = row("replacement", "替代", List.of(), List.of());
                              consumer.accept(duplicate);
                              consumer.accept(duplicate);
                              return new LexiconImportRowSource.ReadReceipt(
                                  metadata('k').sourceDigest(), 2);
                            }));
            long currentVersion = repository.publishedVersion();
            assertTrue(currentVersion > 9_000_000_000L);
            assertEquals(firstVersion, currentVersion);
            assertEquals(
                1,
                repository.findByForms(repository.publishedVersion(), List.of("reliable")).size());
            assertEquals(
                0,
                repository
                    .findByForms(repository.publishedVersion(), List.of("replacement"))
                    .size());
          }
        });
  }

  @Test
  void prewarmReturnsEveryOwnerOfAnAmbiguousSelectedForm() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            long firstVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(
                                row("zoology", "动物学", List.of(), List.of("able")),
                                row("zymurgy", "酿造学", List.of(), List.of("able")),
                                row("the", "这个", List.of("able"), List.of())),
                            metadata('c')));
            assertEquals(firstVersion, repository.publishedVersion());
            var candidates =
                repository.findPrewarmForms(
                    repository.publishedVersion(), LexiconHintAction.HINT, 1);
            assertEquals(3, candidates.size());
            assertEquals(
                java.util.Set.of(LexiconHintAction.HINT, LexiconHintAction.BLOCK),
                candidates.stream()
                    .map(value -> value.finalAction())
                    .collect(java.util.stream.Collectors.toSet()));
            assertTrue(
                candidates.stream().allMatch(value -> value.normalizedForm().equals("able")));
          }
        });
  }

  @Test
  void blocksUnsafeGlossButWarmsFixedNegativeFormWithoutPersistingRawEvidence() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            long version =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(
                                row("the", "这个", List.of(), List.of()),
                                row("sustainability", "可持续性；持续性", List.of(), List.of())),
                            metadata('b')));
            assertEquals(
                LexiconHintAction.BLOCK,
                repository.findByForms(version, List.of("the")).getFirst().finalAction());
            assertEquals(
                LexiconHintAction.BLOCK,
                repository
                    .findByForms(version, List.of("sustainability"))
                    .getFirst()
                    .finalAction());
            assertEquals(
                "the",
                repository
                    .findPrewarmForms(version, LexiconHintAction.BLOCK, 1)
                    .getFirst()
                    .normalizedForm());
            assertEquals(
                "true",
                scalar(
                    jdbcUrl,
                    "SELECT (gloss IS NULL)::text FROM lexicon_entry WHERE lemma='sustainability'"));
          }
        });
  }

  @Test
  void keepsCacheScoreForFinalBlockedFormsAndSeparatesPositiveNegativePrewarm() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var source = new SourceReference("fixture", "MIT", "cache-score");
            var score = new LexiconPriority(4.2, 0, 765);
            var blocked =
                new LexiconImportRow(
                    "opaque", "含混；歧义", "", List.of(), List.of(), score, source, source, List.of(),
                    true);
            var longForm =
                new LexiconImportRow(
                    "bright distant stellar system",
                    "明亮的恒星系",
                    "",
                    List.of("quasar"),
                    List.of(),
                    score,
                    source,
                    source,
                    List.of(),
                    true);
            var noPrewarm =
                new LexiconImportRow(
                    "nebula", "星云", "", List.of(), List.of(), score, source, source, List.of(),
                    false);
            new LexiconImportService(persistence.repository())
                .publish(
                    new LexiconImportRequest(
                        List.of(
                            row("the", "这个", List.of(), List.of()), blocked, longForm, noPrewarm),
                        metadata('h')));
            assertEquals(
                "BLOCK:1000",
                scalar(
                    jdbcUrl,
                    "SELECT CASE WHEN e.gloss IS NULL THEN 'BLOCK' ELSE 'HINT' END || ':' || e.cache_priority FROM lexicon_form f JOIN lexicon_entry e USING (entry_id) WHERE"
                        + " normalized_form='the'"));
            assertEquals(
                "BLOCK:765",
                scalar(
                    jdbcUrl,
                    "SELECT CASE WHEN e.gloss IS NULL THEN 'BLOCK' ELSE 'HINT' END || ':' || e.cache_priority FROM lexicon_form f JOIN lexicon_entry e USING (entry_id) WHERE"
                        + " normalized_form='opaque'"));
            assertEquals(
                "0",
                scalar(
                    jdbcUrl,
                    "SELECT COUNT(*)::text FROM lexicon_form WHERE normalized_form='bright distant stellar system'"));
            assertEquals(
                "HINT:765",
                scalar(
                    jdbcUrl,
                    "SELECT CASE WHEN e.gloss IS NULL THEN 'BLOCK' ELSE 'HINT' END || ':' || e.cache_priority FROM lexicon_form f JOIN lexicon_entry e USING (entry_id) WHERE"
                        + " normalized_form='quasar'"));
            assertEquals(
                "HINT:0",
                scalar(
                    jdbcUrl,
                    "SELECT CASE WHEN e.gloss IS NULL THEN 'BLOCK' ELSE 'HINT' END || ':' || e.cache_priority FROM lexicon_form f JOIN lexicon_entry e USING (entry_id) WHERE"
                        + " normalized_form='nebula'"));
            assertEquals(
                java.util.Set.of("the", "opaque"),
                persistence
                    .repository()
                    .findPrewarmForms(
                        persistence.repository().publishedVersion(), LexiconHintAction.BLOCK, 10)
                    .stream()
                    .map(value -> value.normalizedForm())
                    .collect(java.util.stream.Collectors.toSet()));
            assertEquals(
                List.of("quasar"),
                persistence
                    .repository()
                    .findPrewarmForms(
                        persistence.repository().publishedVersion(), LexiconHintAction.HINT, 10)
                    .stream()
                    .map(value -> value.normalizedForm())
                    .toList());
          }
        });
  }

  @Test
  void failedStreamingReplacementRollsBackAllRowsAndVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            long originalVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(row("reliable", "可靠的", List.of(), List.of())), metadata('a')));
            var events = new java.util.ArrayList<LexiconImportObserver.Event>();
            var observation = new LexiconImportObservation(events::add);
            assertThrows(
                RuntimeException.class,
                () ->
                    new LexiconImportService(repository)
                        .publishStreaming(
                            metadata('b'),
                            2,
                            2,
                            consumer -> {
                              consumer.accept(row("context", "语境", List.of(), List.of()));
                              throw new IOException("source disappeared");
                            },
                            observation));
            assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
            assertEquals(
                LexiconImportObserver.Reason.SOURCE_INVALID,
                events.stream()
                    .filter(LexiconImportObserver.Event::terminal)
                    .findFirst()
                    .orElseThrow()
                    .reason());
            assertEquals(originalVersion, repository.publishedVersion());
            assertEquals(1, repository.findByForms(originalVersion, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(originalVersion, List.of("context")).size());
          }
        });
  }

  @Test
  void databaseConstraintRollbackHasConfirmedTerminalAndKeepsPublishedData() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var service = new LexiconImportService(repository);
            long originalVersion =
                service.publish(
                    new LexiconImportRequest(
                        List.of(row("reliable", "可靠的", List.of(), List.of())), metadata('a')));
            execute(
                jdbcUrl,
                "ALTER TABLE lexicon_entry ADD CONSTRAINT synthetic_reject_context CHECK (lemma <> 'context')");
            var events = new java.util.ArrayList<LexiconImportObserver.Event>();
            assertThrows(
                org.springframework.dao.DataAccessException.class,
                () ->
                    service.publishStreaming(
                        metadata('b'),
                        1,
                        1,
                        consumer -> {
                          consumer.accept(row("context", "语境", List.of(), List.of()));
                          return new LexiconImportRowSource.ReadReceipt(
                              metadata('b').sourceDigest(), 1);
                        },
                        new LexiconImportObservation(events::add)));
            assertEquals(1, events.stream().filter(LexiconImportObserver.Event::terminal).count());
            assertEquals(
                LexiconImportObserver.Reason.PUBLISH_ROLLED_BACK, events.getLast().reason());
            assertEquals(null, events.getLast().lexiconVersion());
            assertTrue(events.getLast().counts().isEmpty());
            assertEquals(originalVersion, repository.publishedVersion());
            assertEquals(1, repository.findByForms(originalVersion, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(originalVersion, List.of("context")).size());
          }
        });
  }

  @Test
  void canonicalConflictAfterFlushedBatchRollsBackPreparedRowsAndVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var service = new LexiconImportService(repository);
            long originalVersion =
                service.publish(
                    new LexiconImportRequest(
                        List.of(row("reliable", "可靠的", List.of(), List.of())), metadata('a')));
            assertThrows(
                IllegalArgumentException.class,
                () ->
                    service.publishStreaming(
                        metadata('b'),
                        501,
                        501,
                        consumer -> {
                          for (int index = 0; index < 500; index++) {
                            var lemma =
                                "batch" + (char) ('a' + index / 26) + (char) ('a' + index % 26);
                            consumer.accept(
                                row(
                                    lemma,
                                    "合成词条",
                                    index == 0 ? List.of("shared") : List.of(),
                                    List.of()));
                          }
                          consumer.accept(row("collision", "冲突词条", List.of("shared"), List.of()));
                          return new LexiconImportRowSource.ReadReceipt(
                              metadata('b').sourceDigest(), 501);
                        }));
            assertEquals(originalVersion, repository.publishedVersion());
            assertEquals(1, repository.findByForms(originalVersion, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(originalVersion, List.of("batchaa")).size());
            assertEquals("1", scalar(jdbcUrl, "SELECT COUNT(*)::text FROM lexicon_entry"));
          }
        });
  }

  @Test
  void importServiceRejectsChangedStreamAndPostgresKeepsPublishedVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var service = new LexiconImportService(repository);
            long originalVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(row("reliable", "可靠的", List.of(), List.of())), metadata('a')));
            for (var failure :
                List.of("digest", "raw-count", "fewer-entries", "more-entries", "io")) {
              assertThrows(
                  RuntimeException.class,
                  () ->
                      service.publishStreaming(
                          metadata('b'),
                          2,
                          1,
                          consumer -> {
                            switch (failure) {
                              case "digest" -> {
                                consumer.accept(row("context", "语境", List.of(), List.of()));
                                return new LexiconImportRowSource.ReadReceipt(
                                    metadata('a').sourceDigest(), 2);
                              }
                              case "raw-count" -> {
                                consumer.accept(row("context", "语境", List.of(), List.of()));
                                return new LexiconImportRowSource.ReadReceipt(
                                    metadata('b').sourceDigest(), 3);
                              }
                              case "fewer-entries" -> {
                                return new LexiconImportRowSource.ReadReceipt(
                                    metadata('b').sourceDigest(), 2);
                              }
                              case "more-entries" -> {
                                consumer.accept(row("context", "语境", List.of(), List.of()));
                                consumer.accept(row("environment", "环境", List.of(), List.of()));
                                return new LexiconImportRowSource.ReadReceipt(
                                    metadata('b').sourceDigest(), 2);
                              }
                              case "io" -> {
                                consumer.accept(row("context", "语境", List.of(), List.of()));
                                throw new IOException("synthetic stream failure");
                              }
                              default -> throw new IllegalStateException("unknown failure case");
                            }
                          }));
              assertEquals(originalVersion, repository.publishedVersion(), failure);
              assertEquals(
                  1, repository.findByForms(originalVersion, List.of("reliable")).size(), failure);
              assertEquals(
                  0, repository.findByForms(originalVersion, List.of("context")).size(), failure);
            }
          }
        });
  }

  @Test
  void publishesOneSafeGlossSharedByLemmaAliasesAndInflections() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var multiSense =
                rowWithSource("bank", "银行", "银行；河岸", List.of("banks"), List.of("banked"));
            var singleSense = rowWithSource("reliable", "可靠的", "可靠的", List.of(), List.of());
            new LexiconImportService(repository)
                .publish(new LexiconImportRequest(List.of(multiSense, singleSense), metadata('d')));
            assertEquals(
                "银行", scalar(jdbcUrl, "SELECT gloss FROM lexicon_entry WHERE lemma='bank'"));
            assertEquals(
                "3",
                scalar(
                    jdbcUrl,
                    "SELECT COUNT(*)::text FROM lexicon_form "
                        + "WHERE entry_id=(SELECT entry_id FROM lexicon_entry WHERE lemma='bank')"));
            var matches =
                repository.findByForms(
                    repository.publishedVersion(), List.of("bank", "banks", "banked", "reliable"));
            assertEquals(4, matches.size());
            assertTrue(matches.stream().allMatch(m -> m.finalAction() == LexiconHintAction.HINT));
            assertEquals(
                "可靠的", scalar(jdbcUrl, "SELECT gloss FROM lexicon_entry WHERE lemma='reliable'"));
          }
        });
  }

  @Test
  void blocksInvalidGlossWithoutPersistingSourceGloss() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var emptyFirst = rowWithSource("emptyfirst", "", "；银行", List.of(), List.of());
            new LexiconImportService(repository)
                .publish(new LexiconImportRequest(List.of(emptyFirst), metadata('e')));
            assertEquals(
                LexiconHintAction.BLOCK,
                repository
                    .findByForms(repository.publishedVersion(), List.of("emptyfirst"))
                    .getFirst()
                    .finalAction());
            assertEquals(
                "true",
                scalar(
                    jdbcUrl,
                    "SELECT (gloss IS NULL)::text FROM lexicon_entry WHERE lemma='emptyfirst'"));
          }
        });
  }

  @Test
  void versionSwitchUpdatesSenseIdentityAndInvalidatesOldVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            long firstVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(row("context", "语境", List.of(), List.of())), metadata('f')));
            var v1 = repository.findByForms(firstVersion, List.of("context")).getFirst();
            assertEquals("语境", v1.finalGloss());
            assertEquals(firstVersion, v1.lexiconVersion());
            long secondVersion =
                new LexiconImportService(repository)
                    .publish(
                        new LexiconImportRequest(
                            List.of(row("context", "环境", List.of(), List.of())), metadata('g')));
            assertTrue(secondVersion > firstVersion);
            assertEquals(secondVersion, repository.publishedVersion());
            assertEquals(0, repository.findByForms(firstVersion, List.of("context")).size());
            var v2 = repository.findByForms(secondVersion, List.of("context")).getFirst();
            assertEquals("环境", v2.finalGloss());
            assertEquals(secondVersion, v2.lexiconVersion());
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
            new LexiconImportService(repository)
                .publish(
                    new LexiconImportRequest(
                        List.of(row("false", "错误的", List.of(), List.of())), metadata('a')));
            var cache = new CachedLexiconQueryService(repository, 20, 1, 1);
            assertEquals(
                LexiconHintAction.HINT,
                cache.lookupForms(List.of("false")).candidates().getFirst().finalAction());
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
                      true,
                      false,
                      false));
            }
            words.add(row("specialist", "专家", List.of(), List.of()));
            long secondVersion =
                new LexiconImportService(repository)
                    .publish(new LexiconImportRequest(words, metadata('b')));
            assertThrows(IllegalArgumentException.class, () -> cache.lookupForms(List.of("FALSE")));
            for (var form : List.of("false", "ability", "abilities", "abilityalias")) {
              var candidate = cache.lookupForms(List.of(form)).candidates().getFirst();
              assertEquals(secondVersion, candidate.lexiconVersion());
              assertEquals(LexiconHintAction.BLOCK, candidate.finalAction(), form);
            }
            for (var form : List.of("false alarm", "specialist")) {
              assertEquals(
                  LexiconHintAction.HINT,
                  cache.lookupForms(List.of(form)).candidates().getFirst().finalAction(),
                  form);
            }
            assertEquals(
                "2",
                scalar(
                    jdbcUrl,
                    "SELECT COUNT(*)::text FROM lexicon_entry "
                        + "WHERE gloss IS NULL AND lemma IN ('false','ability')"));
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
        false,
        false,
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

  @Test
  void cleaningDecisionIsIdenticalForListAndStreamingWithWindowedAliasProtection()
      throws Exception {
    for (boolean streaming : List.of(false, true)) {
      inInitializedSchema(
          jdbcUrl -> {
            try (var persistence = PostgresPersistence.open(jdbcUrl)) {
              var repository = persistence.repository();
              var records =
                  List.of(
                      preparedRow("synthetic-organ", "[医]甲[床]瘤", "甲[床]瘤", false, false, List.of()),
                      preparedRow("give up", "放弃", "放弃", true, false, List.of()),
                      preparedRow(
                          "one distant stellar system",
                          "星系",
                          "星系",
                          false,
                          false,
                          List.of("quasar")),
                      preparedRow("curated-term", "原始释义(说明)", "指定短释", false, true, List.of()));
              var metadata =
                  new LexiconImportMetadata(
                      "d".repeat(64), "ecdict-stardict", "MIT", Instant.EPOCH);
              if (streaming)
                new LexiconImportService(repository)
                    .publishStreaming(
                        metadata,
                        records.size(),
                        records.size(),
                        consumer -> {
                          records.forEach(consumer);
                          return new LexiconImportRowSource.ReadReceipt(
                              metadata.sourceDigest(), records.size());
                        });
              else
                new LexiconImportService(repository)
                    .publish(new LexiconImportRequest(records, metadata));
              long version = repository.publishedVersion();
              assertEquals(
                  "甲床瘤",
                  scalar(
                      jdbcUrl,
                      "SELECT gloss FROM lexicon_entry WHERE" + " lemma='synthetic-organ'"));
              assertEquals(
                  LexiconHintAction.BLOCK,
                  repository.findByForms(version, List.of("give up")).getFirst().finalAction());
              assertEquals(
                  "星系",
                  scalar(
                      jdbcUrl,
                      "SELECT gloss FROM lexicon_entry WHERE lemma='one distant stellar system'"));
              assertEquals(
                  "0",
                  scalar(
                      jdbcUrl,
                      "SELECT COUNT(*)::text FROM lexicon_form WHERE normalized_form='one distant stellar system'"));
              assertEquals(
                  "指定短释",
                  scalar(jdbcUrl, "SELECT gloss FROM lexicon_entry WHERE lemma='curated-term'"));
              assertEquals(1, repository.findByForms(version, List.of("quasar")).size());
            }
          });
    }
  }

  private static LexiconImportRow preparedRow(
      String lemma,
      String raw,
      String candidate,
      boolean allBasic,
      boolean curated,
      List<String> aliases) {
    var source = new SourceReference("ecdict-stardict", "MIT", "fixture-" + lemma);
    return new LexiconImportRow(
        lemma,
        candidate,
        "",
        aliases,
        List.of(),
        new LexiconPriority(0, 0, 100),
        source,
        source,
        List.of(),
        false,
        false,
        "cleaning-fixture",
        raw,
        null,
        null,
        List.of(),
        false,
        allBasic,
        curated);
  }

  @Test
  void publishedVersionRejectsWrongPolicyAndExplicitRepublishReplacesIt() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            assertEquals(0, repository.publishedVersion());
            var first = row("context", "语境", List.of(), List.of());
            long firstVersion =
                new LexiconImportService(repository)
                    .publish(new LexiconImportRequest(List.of(first), metadata('a')));
            assertEquals(
                "lexiflow.deterministic-preparation.v1",
                scalar(jdbcUrl, "SELECT preparation_policy FROM lexicon_dataset"));
            execute(jdbcUrl, "UPDATE lexicon_dataset SET preparation_policy = 'wrong-policy'");
            assertThrows(
                io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException.class,
                repository::publishedVersion);
            long secondVersion =
                new LexiconImportService(repository)
                    .publish(new LexiconImportRequest(List.of(first), metadata('b')));
            assertTrue(secondVersion > firstVersion);
            assertEquals(secondVersion, repository.publishedVersion());
          }
        });
  }

  @Test
  void publishedVersionRejectsIncompleteSourceManifest() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var first = row("context", "语境", List.of(), List.of());
            new LexiconImportService(repository)
                .publish(new LexiconImportRequest(List.of(first), metadata('a')));
            execute(
                jdbcUrl,
                "UPDATE lexicon_dataset SET source_manifest = '[{\"source_id\":\"fixture\"}]'::jsonb");
            assertThrows(
                io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException.class,
                repository::publishedVersion);
          }
        });
  }

  @Test
  void publishedVersionRejectsMissingWatchingProjectionColumnEvenWhenEmpty() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            execute(jdbcUrl, "ALTER TABLE lexicon_entry DROP COLUMN gloss CASCADE");
            assertThrows(
                org.springframework.dao.DataAccessException.class, repository::publishedVersion);
          }
        });
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

  private static void execute(String jdbcUrl, String sql) throws SQLException {
    try (var connection = DriverManager.getConnection(jdbcUrl);
        var statement = connection.createStatement()) {
      statement.executeUpdate(sql);
    }
  }

  @FunctionalInterface
  private interface JdbcUrlTest {
    void run(String jdbcUrl) throws Exception;
  }
}
