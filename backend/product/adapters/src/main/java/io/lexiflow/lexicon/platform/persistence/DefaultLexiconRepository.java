package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.policy.ClassificationPolicy;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.io.IOException;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** 三表词库的单一持久化实现；观看只查准确词形投影。 */
final class DefaultLexiconRepository implements LexiconRepository {
  private static final int BATCH_SIZE = 500;
  private static final String LOOKUP_COLUMNS =
      "h.lexicon_entry_id, h.final_sense_id, "
          + "h.normalized_form, h.canonical_lemma, h.entry_kind, h.final_action, h.final_gloss, "
          + "h.final_priority, h.final_frequency_zipf, h.final_complex_list_count";
  private final JdbcTemplate jdbc;
  private final TransactionTemplate transaction;

  DefaultLexiconRepository(JdbcTemplate jdbc, TransactionTemplate transaction) {
    this.jdbc = Objects.requireNonNull(jdbc, "jdbc");
    this.transaction = Objects.requireNonNull(transaction, "transaction");
  }

  @Override
  public long publishedVersion() {
    return jdbc.query(
        "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id = 1",
        result -> result.next() ? result.getLong(1) : 0L);
  }

  @Override
  public List<LexiconHintCandidate> findByForms(long version, Collection<String> forms) {
    if (version == 0 || forms.isEmpty()) return List.of();
    var unique = List.copyOf(new java.util.LinkedHashSet<>(forms));
    var placeholders = String.join(",", java.util.Collections.nCopies(unique.size(), "?"));
    var args = new ArrayList<Object>(unique);
    args.add(version);
    return jdbc.query(
        "SELECT "
            + LOOKUP_COLUMNS
            + " FROM lexicon_hint_lookup h "
            + "WHERE h.language_tag = 'en' AND h.normalized_form IN ("
            + placeholders
            + ") "
            + "AND EXISTS (SELECT 1 FROM lexicon_dataset d WHERE d.dataset_id = 1 "
            + "AND d.lexicon_version = ?)",
        (result, row) -> candidate(result, version),
        args.toArray());
  }

  @Override
  public List<LexiconHintCandidate> findPrewarmForms(
      long version, LexiconHintAction action, int limit) {
    Objects.requireNonNull(action, "action");
    if (version == 0 || limit <= 0) return List.of();
    return jdbc.query(
        "WITH top_rows AS (SELECT normalized_form FROM lexicon_hint_lookup "
            + "WHERE language_tag = 'en' AND final_action = ? AND cache_priority > 0 "
            + "ORDER BY cache_priority DESC, normalized_form LIMIT ?), "
            + "selected AS (SELECT DISTINCT normalized_form FROM top_rows) "
            + "SELECT "
            + LOOKUP_COLUMNS
            + " FROM selected s JOIN lexicon_hint_lookup h "
            + "ON h.language_tag = 'en' AND h.normalized_form = s.normalized_form "
            + "WHERE EXISTS (SELECT 1 FROM lexicon_dataset d WHERE d.dataset_id = 1 "
            + "AND d.lexicon_version = ?) ORDER BY h.normalized_form, h.lexicon_entry_id",
        (result, row) -> candidate(result, version),
        action.name(),
        limit,
        version);
  }

  private static LexiconHintCandidate candidate(java.sql.ResultSet result, long version)
      throws SQLException {
    return new LexiconHintCandidate(
        result.getObject("lexicon_entry_id", UUID.class),
        result.getObject("final_sense_id", UUID.class),
        version,
        "en",
        result.getString("normalized_form"),
        result.getString("canonical_lemma"),
        LexiconEntryKind.valueOf(result.getString("entry_kind").toUpperCase(Locale.ROOT)),
        LexiconHintAction.valueOf(result.getString("final_action")),
        result.getString("final_gloss"),
        result.getInt("final_priority"),
        result.getDouble("final_frequency_zipf"),
        result.getInt("final_complex_list_count"));
  }

  @Override
  public long publish(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconPublicationRepository.PreparedEntrySource source) {
    Objects.requireNonNull(metadata, "metadata");
    Objects.requireNonNull(source, "source");
    if (sourceRowsTotal < expectedEntries || expectedEntries < 1) {
      throw new IllegalArgumentException("invalid completed source counts");
    }
    return transaction.execute(
        status -> {
          jdbc.execute("SELECT pg_advisory_xact_lock(643981781)");
          var version = publishedVersion() + 1;
          jdbc.update("DELETE FROM lexicon_dataset WHERE dataset_id = 1");
          jdbc.update("DELETE FROM lexicon_prepared_entry");
          var writer = new BatchWriter();
          try {
            source.read(version, writer::add);
          } catch (IOException exception) {
            throw new java.io.UncheckedIOException("source changed during publication", exception);
          }
          writer.flush();
          if (writer.entries != expectedEntries) {
            throw new IllegalStateException("source entry count changed after preflight");
          }
          jdbc.update(
              "INSERT INTO lexicon_dataset (dataset_id, lexicon_version, source_manifest, "
                  + "source_row_count, entry_count, lookup_count, preparation_policy) "
                  + "VALUES (1, ?, jsonb_build_array(jsonb_build_object('source_id', ?, "
                  + "'license_id', ?, 'source_digest', ?, 'acquired_at', ?::timestamptz)), ?, ?, ?, ?)",
              version,
              metadata.sourceId(),
              metadata.licenseId(),
              metadata.sourceDigest(),
              Timestamp.from(metadata.acquiredAt()),
              sourceRowsTotal,
              writer.entries,
              writer.lookups,
              metadata.sourceId());
          return version;
        });
  }

  /** 在一个事务内按固定分块批量写入主词条及其所有准确词形。 */
  private final class BatchWriter {
    private final List<LexiconImportPlan.PlannedEntry> rows = new ArrayList<>(BATCH_SIZE);
    private long entries;
    private long lookups;

    void add(LexiconImportPlan.PlannedEntry planned) {
      rows.add(planned);
      if (rows.size() == BATCH_SIZE) flush();
    }

    void flush() {
      if (rows.isEmpty()) return;
      var prepared = new ArrayList<Object[]>(rows.size());
      var lookup = new ArrayList<Object[]>();
      for (var planned : rows) {
        var entry = planned.entry();
        var row = planned.row();
        var exclusion = planned.prepared().exclusionReason();
        var gloss = planned.prepared().gloss();
        var action = gloss == null ? LexiconHintAction.BLOCK : LexiconHintAction.HINT;
        prepared.add(
            new Object[] {
              entry.entryId(),
              entry.languageTag(),
              entry.lemma(),
              entry.entryKind().name().toLowerCase(Locale.ROOT),
              row.sourceGloss(),
              row.dictionary().recordReference(),
              row.dictionary().sourceId(),
              row.frequency().sourceId(),
              row.frequency().recordReference(),
              row.sourceBncRank(),
              row.sourceFrqRank(),
              row.sourceComplexTags().toArray(String[]::new),
              row.sourceOxfordBasic(),
              gloss,
              exclusion,
              planned.prepared().classification().frequencyEvidence().name(),
              planned.prepared().decisiveRule(),
              planned.prepared().matchedRules().toArray(String[]::new),
              row.priority().memoryPriority(),
              row.priority().frequencyZipf(),
              row.priority().complexListCount()
            });
        addLookup(lookup, planned, entry.lemma(), "lemma", action, gloss);
        entry
            .aliases()
            .forEach(
                alias ->
                    addLookup(lookup, planned, alias.normalizedForm(), "alias", action, gloss));
        entry
            .inflections()
            .forEach(
                form ->
                    addLookup(lookup, planned, form.normalizedForm(), "inflection", action, gloss));
      }
      jdbc.batchUpdate(
          "INSERT INTO lexicon_prepared_entry (lexicon_entry_id, language_tag, "
              + "lemma, entry_kind, source_gloss, source_gloss_ref, source_dictionary_id, "
              + "source_frequency_id, source_frequency_ref, source_bnc_rank, source_frq_rank, "
              + "source_complex_tags, source_oxford_basic, prepared_gloss, exclusion_reason, "
              + "frequency_evidence, decisive_rule, matched_rules, prepared_priority, frequency_zipf, complex_list_count) "
              + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          batch(prepared));
      jdbc.batchUpdate(
          "INSERT INTO lexicon_hint_lookup (language_tag, normalized_form, "
              + "lexicon_entry_id, form_kind, canonical_lemma, entry_kind, final_action, final_gloss, "
              + "final_priority, final_sense_id, final_frequency_zipf, final_complex_list_count, "
              + "cache_priority, final_decision_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          batch(lookup));
      entries += prepared.size();
      lookups += lookup.size();
      rows.clear();
    }
  }

  private static void addLookup(
      List<Object[]> lookup,
      LexiconImportPlan.PlannedEntry planned,
      String form,
      String kind,
      LexiconHintAction action,
      String gloss) {
    var entry = planned.entry();
    var finalReason = planned.prepared().decisiveRule();
    if (!LexiconSurfacePolicy.withinQueryWindow(form)) {
      action = LexiconHintAction.BLOCK;
      gloss = null;
      finalReason = "outside_query_window";
    }
    var priority = entry.priority();
    var cachePriority =
        ClassificationPolicy.cachePriority(
            planned.row(), planned.prepared().classification(), action);
    lookup.add(
        new Object[] {
          entry.languageTag(),
          form,
          entry.entryId(),
          kind,
          entry.lemma(),
          entry.entryKind().name().toLowerCase(Locale.ROOT),
          action.name(),
          gloss,
          priority.memoryPriority(),
          gloss == null ? null : entry.senses().getFirst().senseId(),
          priority.frequencyZipf(),
          priority.complexListCount(),
          cachePriority,
          finalReason
        });
  }

  private static BatchPreparedStatementSetter batch(List<Object[]> rows) {
    return new BatchPreparedStatementSetter() {
      @Override
      public int getBatchSize() {
        return rows.size();
      }

      @Override
      public void setValues(PreparedStatement statement, int index) throws SQLException {
        var values = rows.get(index);
        for (int column = 0; column < values.length; column++) {
          if (values[column] instanceof String[] array) {
            statement.setArray(column + 1, statement.getConnection().createArrayOf("text", array));
          } else {
            statement.setObject(column + 1, values[column]);
          }
        }
      }
    };
  }
}
