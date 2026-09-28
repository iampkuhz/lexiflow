package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.lexicon.application.importing.LexiconImportService;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
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
  void publishesSourceClassificationCleaningAndPerSurfaceDecisionEvidence() throws Exception {
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
            var longAlias =
                new LexiconImportRow(
                    "alias-owner",
                    "拥有别名",
                    "",
                    List.of("one two three four"),
                    List.of(),
                    new LexiconPriority(4.2, 1, 100),
                    csvDictionary,
                    csvFrequency,
                    List.of(),
                    true);
            repository.publish(
                new LexiconImportRequest(
                    List.of(stardict, zeroFrequency, blocked, longAlias), metadata('i')));

            assertEquals(
                "ecdict-stardict:fixture-medical-term",
                scalar(
                    jdbcUrl,
                    "SELECT source_dictionary_id || ':' || source_gloss_ref "
                        + "FROM lexicon_prepared_entry WHERE lemma='medical-term'"));
            assertEquals(
                "ecdict-stardict:fixture-medical-term:UNKNOWN",
                scalar(
                    jdbcUrl,
                    "SELECT source_frequency_id || ':' || source_frequency_ref || ':' "
                        + "|| frequency_evidence FROM lexicon_prepared_entry "
                        + "WHERE lemma='medical-term'"));
            assertEquals(
                "source_label,medical_insert",
                scalar(
                    jdbcUrl,
                    "SELECT array_to_string(matched_rules, ',') FROM lexicon_prepared_entry "
                        + "WHERE lemma='medical-term'"));
            assertEquals(
                "medical_insert",
                scalar(
                    jdbcUrl,
                    "SELECT decisive_rule FROM lexicon_prepared_entry "
                        + "WHERE lemma='medical-term'"));
            assertEquals(
                "zipf-csv:csv-frequency-row:KNOWN",
                scalar(
                    jdbcUrl,
                    "SELECT source_frequency_id || ':' || source_frequency_ref || ':' "
                        + "|| frequency_evidence FROM lexicon_prepared_entry "
                        + "WHERE lemma='zero-frequency'"));
            assertEquals(
                "HINT:existing_safe",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || final_decision_reason "
                        + "FROM lexicon_hint_lookup WHERE normalized_form='zero-frequency'"));
            assertEquals(
                "unsafe_default_candidate:unsafe_default_candidate",
                scalar(
                    jdbcUrl,
                    "SELECT exclusion_reason || ':' || decisive_rule "
                        + "FROM lexicon_prepared_entry WHERE lemma='unsafe-medical'"));
            assertEquals(
                "unsafe_default_candidate",
                scalar(
                    jdbcUrl,
                    "SELECT final_decision_reason FROM lexicon_hint_lookup "
                        + "WHERE normalized_form='unsafe-medical'"));
            assertEquals(
                "HINT:existing_safe",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || final_decision_reason "
                        + "FROM lexicon_hint_lookup WHERE normalized_form='alias-owner'"));
            assertEquals(
                "BLOCK:outside_query_window",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || final_decision_reason "
                        + "FROM lexicon_hint_lookup WHERE normalized_form='one two three four'"));
            assertEquals(
                "existing_safe",
                scalar(
                    jdbcUrl,
                    "SELECT decisive_rule FROM lexicon_prepared_entry WHERE lemma='alias-owner'"));
            assertEquals(
                "拥有别名",
                scalar(
                    jdbcUrl,
                    "SELECT prepared_gloss FROM lexicon_prepared_entry WHERE lemma='alias-owner'"));
          }
        });
  }

  @Test
  void rejectsInvalidPublicationEvidenceAndRollsBackReplacement() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            repository.publish(
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
                    "UPDATE lexicon_prepared_entry SET lemma=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET source_gloss=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET source_gloss_ref=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET source_dictionary_id=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET source_frequency_id=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET source_frequency_ref=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET frequency_evidence='MISSING'",
                    "UPDATE lexicon_prepared_entry SET decisive_rule=chr(9)||chr(10)",
                    "UPDATE lexicon_prepared_entry SET prepared_gloss=chr(9)||chr(10) "
                        + "WHERE prepared_gloss IS NOT NULL",
                    "UPDATE lexicon_prepared_entry SET exclusion_reason='different' "
                        + "WHERE lemma='ambiguous'",
                    "UPDATE lexicon_prepared_entry SET exclusion_reason=chr(9)||chr(10) "
                        + "WHERE lemma='ambiguous'",
                    "UPDATE lexicon_prepared_entry SET matched_rules=ARRAY[NULL]::text[]",
                    "UPDATE lexicon_prepared_entry SET matched_rules=ARRAY['valid', chr(9)]::text[]",
                    "UPDATE lexicon_hint_lookup SET normalized_form=chr(9)||chr(10)",
                    "UPDATE lexicon_hint_lookup SET canonical_lemma=chr(9)||chr(10)",
                    "UPDATE lexicon_hint_lookup SET final_decision_reason=chr(9)||chr(10)",
                    "UPDATE lexicon_hint_lookup SET final_gloss=chr(9)||chr(10) "
                        + "WHERE final_action='HINT'",
                    "UPDATE lexicon_hint_lookup SET final_decision_reason='outside_query_window' "
                        + "WHERE final_action='HINT'")) {
              assertThrows(SQLException.class, () -> execute(jdbcUrl, invalidSql), invalidSql);
            }
            assertThrows(
                RuntimeException.class,
                () ->
                    repository.publishStreaming(
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
            assertEquals(1, repository.publishedVersion());
            assertEquals(1, repository.findByForms(1, List.of("reliable")).size());
            assertEquals(0, repository.findByForms(1, List.of("replacement")).size());
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
            persistence
                .repository()
                .publish(
                    new LexiconImportRequest(
                        List.of(
                            row("the", "这个", List.of(), List.of()), blocked, longForm, noPrewarm),
                        metadata('h')));
            assertEquals(
                "BLOCK:1000",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || cache_priority FROM lexicon_hint_lookup WHERE normalized_form='the'"));
            assertEquals(
                "BLOCK:765",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || cache_priority FROM lexicon_hint_lookup WHERE normalized_form='opaque'"));
            assertEquals(
                "BLOCK:765",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || cache_priority FROM lexicon_hint_lookup WHERE normalized_form='bright distant stellar system'"));
            assertEquals(
                "HINT:765",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || cache_priority FROM lexicon_hint_lookup WHERE normalized_form='quasar'"));
            assertEquals(
                "HINT:0",
                scalar(
                    jdbcUrl,
                    "SELECT final_action || ':' || cache_priority FROM lexicon_hint_lookup WHERE normalized_form='nebula'"));
            assertEquals(
                java.util.Set.of("the", "opaque", "bright distant stellar system"),
                persistence.repository().findPrewarmForms(1, LexiconHintAction.BLOCK, 10).stream()
                    .map(value -> value.normalizedForm())
                    .collect(java.util.stream.Collectors.toSet()));
            assertEquals(
                List.of("quasar"),
                persistence.repository().findPrewarmForms(1, LexiconHintAction.HINT, 10).stream()
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
  void importServiceRejectsChangedStreamAndPostgresKeepsPublishedVersion() throws Exception {
    inInitializedSchema(
        jdbcUrl -> {
          try (var persistence = PostgresPersistence.open(jdbcUrl)) {
            var repository = persistence.repository();
            var service = new LexiconImportService(repository);
            repository.publish(
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
              assertEquals(1, repository.publishedVersion(), failure);
              assertEquals(1, repository.findByForms(1, List.of("reliable")).size(), failure);
              assertEquals(0, repository.findByForms(1, List.of("context")).size(), failure);
            }
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
            repository.publish(new LexiconImportRequest(words, metadata('b')));
            assertThrows(IllegalArgumentException.class, () -> cache.lookupForms(List.of("FALSE")));
            for (var form : List.of("false", "ability", "abilities", "abilityalias")) {
              var candidate = cache.lookupForms(List.of(form)).candidates().getFirst();
              assertEquals(2, candidate.lexiconVersion());
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
  void cleaningDecisionIsIdenticalForListAndStreamingWithSourceAndAliasProtection()
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
                repository.publishStreaming(
                    metadata,
                    records.size(),
                    records.size(),
                    consumer -> {
                      records.forEach(consumer);
                      return new LexiconImportRowSource.ReadReceipt(
                          metadata.sourceDigest(), records.size());
                    });
              else repository.publish(new LexiconImportRequest(records, metadata));
              assertEquals(
                  "甲床瘤",
                  scalar(
                      jdbcUrl,
                      "SELECT prepared_gloss FROM lexicon_prepared_entry WHERE lemma='synthetic-organ'"));
              assertEquals(
                  "[医]甲[床]瘤",
                  scalar(
                      jdbcUrl,
                      "SELECT source_gloss FROM lexicon_prepared_entry WHERE lemma='synthetic-organ'"));
              assertEquals(
                  "all_basic_phrase",
                  scalar(
                      jdbcUrl,
                      "SELECT exclusion_reason FROM lexicon_prepared_entry WHERE lemma='give up'"));
              assertEquals(
                  "BLOCK",
                  scalar(
                      jdbcUrl,
                      "SELECT final_action FROM lexicon_hint_lookup WHERE normalized_form='give up'"));
              assertEquals(
                  "星系",
                  scalar(
                      jdbcUrl,
                      "SELECT final_gloss FROM lexicon_hint_lookup WHERE normalized_form='quasar'"));
              assertEquals(
                  "BLOCK",
                  scalar(
                      jdbcUrl,
                      "SELECT final_action FROM lexicon_hint_lookup WHERE normalized_form='one distant stellar system'"));
              assertEquals(
                  "指定短释",
                  scalar(
                      jdbcUrl,
                      "SELECT final_gloss FROM lexicon_hint_lookup WHERE normalized_form='curated-term'"));
              assertEquals(
                  "0",
                  scalar(
                      jdbcUrl,
                      "SELECT count(*)::text FROM lexicon_hint_lookup h JOIN lexicon_prepared_entry p USING (lexicon_entry_id) "
                          + "WHERE h.final_action='HINT' AND h.final_gloss IS DISTINCT FROM p.prepared_gloss"));
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
