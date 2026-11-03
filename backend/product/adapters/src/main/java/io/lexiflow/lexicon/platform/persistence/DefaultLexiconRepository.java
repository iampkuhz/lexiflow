package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.policy.ClassificationPolicy;
import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException;
import io.lexiflow.lexicon.application.port.LexiconPublicationRepository;
import io.lexiflow.lexicon.application.port.LexiconRepository;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.io.IOException;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.time.Clock;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import org.springframework.jdbc.core.BatchPreparedStatementSetter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** 两表词库的单一持久化实现；来源明细只在受控导入阶段使用。 */
final class DefaultLexiconRepository implements LexiconRepository {
  private static final int BATCH_SIZE = 500;
  private final JdbcTemplate jdbc;
  private final TransactionTemplate transaction;
  private final Clock clock;
  private final long previousVersionFloor;

  DefaultLexiconRepository(JdbcTemplate jdbc, TransactionTemplate transaction) {
    this(jdbc, transaction, Clock.systemUTC(), 0);
  }

  DefaultLexiconRepository(
      JdbcTemplate jdbc, TransactionTemplate transaction, long previousVersionFloor) {
    this(jdbc, transaction, Clock.systemUTC(), previousVersionFloor);
  }

  DefaultLexiconRepository(JdbcTemplate jdbc, TransactionTemplate transaction, Clock clock) {
    this(jdbc, transaction, clock, 0);
  }

  DefaultLexiconRepository(
      JdbcTemplate jdbc, TransactionTemplate transaction, Clock clock, long previousVersionFloor) {
    this.jdbc = Objects.requireNonNull(jdbc, "jdbc");
    this.transaction = Objects.requireNonNull(transaction, "transaction");
    this.clock = Objects.requireNonNull(clock, "clock");
    if (previousVersionFloor < 0)
      throw new IllegalArgumentException("version floor must be non-negative");
    this.previousVersionFloor = previousVersionFloor;
  }

  @Override
  public long publishedVersion() {
    return jdbc.query(
        "SELECT lexicon_version, CASE WHEN json_valid(source_manifest) "
            + "AND json_type(source_manifest)='array' AND json_array_length(source_manifest)>0 "
            + "AND NOT EXISTS (SELECT 1 FROM json_each(source_manifest) item WHERE "
            + "coalesce(length(trim(json_extract(item.value,'$.source_id'))),0)=0 OR "
            + "coalesce(length(trim(json_extract(item.value,'$.license_id'))),0)=0 OR "
            + "coalesce(length(trim(json_extract(item.value,'$.source_digest'))),0)=0 OR "
            + "coalesce(length(trim(json_extract(item.value,'$.acquired_at'))),0)=0) "
            + "THEN 1 ELSE 0 END, source_row_count, entry_count, lookup_count, preparation_policy "
            + "FROM lexicon_dataset WHERE dataset_id=1",
        result -> {
          if (!result.next()) return 0L;
          long version = result.getLong(1);
          boolean manifestValid = result.getBoolean(2);
          long sourceRows = result.getLong(3);
          long entries = result.getLong(4);
          long lookups = result.getLong(5);
          String policy = result.getString(6);
          if (version < 1
              || !manifestValid
              || sourceRows < entries
              || entries < 1
              || lookups < entries
              || !HintPreparation.POLICY_ID.equals(policy)) {
            throw new InvalidPublishedLexiconException();
          }
          return version;
        });
  }

  private long rawPublishedVersion() {
    Long version =
        jdbc.queryForObject(
            "SELECT COALESCE(MAX(lexicon_version),0) FROM lexicon_dataset WHERE dataset_id=1",
            Long.class);
    return Math.max(previousVersionFloor, version == null ? 0 : version);
  }

  @Override
  public List<LexiconHintCandidate> findByForms(long version, Collection<String> forms) {
    if (version == 0 || forms.isEmpty()) return List.of();
    var unique = List.copyOf(new LinkedHashSet<>(forms));
    var placeholders = String.join(",", java.util.Collections.nCopies(unique.size(), "?"));
    var args = new ArrayList<Object>(unique);
    args.add(version);
    return jdbc.query(
        "SELECT l.entry_id, l.normalized_form, e.lemma, e.gloss, e.priority FROM lexicon_lookup l "
            + "JOIN lexicon_entry e USING (entry_id) WHERE l.normalized_form IN ("
            + placeholders
            + ") AND EXISTS (SELECT 1 FROM lexicon_dataset d "
            + "WHERE d.dataset_id = 1 AND d.lexicon_version = ?)",
        (result, row) -> candidate(result, version),
        args.toArray());
  }

  @Override
  public List<LexiconHintCandidate> findPrewarmForms(
      long version, LexiconHintAction action, int limit) {
    Objects.requireNonNull(action, "action");
    if (version == 0 || limit <= 0) return List.of();
    String glossCondition =
        action == LexiconHintAction.HINT ? "e.gloss IS NOT NULL" : "e.gloss IS NULL";
    return jdbc.query(
        "WITH canonical_rows AS ("
            + "SELECT e.lemma AS normalized_form,e.priority FROM lexicon_entry e WHERE "
            + glossCondition
            + " AND e.canonical_form=1 AND e.priority>0 "
            + "ORDER BY e.priority DESC,e.lemma LIMIT ?), alias_rows AS ("
            + "SELECT f.normalized_form,e.priority FROM lexicon_form f "
            + "JOIN lexicon_entry e USING(entry_id) WHERE "
            + glossCondition
            + " AND e.priority>0 "
            + "ORDER BY e.priority DESC,f.normalized_form LIMIT ?), "
            + "top_rows AS (SELECT normalized_form FROM (SELECT normalized_form,priority FROM canonical_rows "
            + "UNION ALL SELECT normalized_form,priority FROM alias_rows) candidates "
            + "ORDER BY priority DESC,normalized_form LIMIT ?), "
            + "selected AS (SELECT DISTINCT normalized_form FROM top_rows) "
            + "SELECT l.entry_id, l.normalized_form, e.lemma, e.gloss, e.priority FROM selected s JOIN lexicon_lookup l "
            + "USING (normalized_form) JOIN lexicon_entry e USING (entry_id) "
            + "WHERE EXISTS (SELECT 1 FROM lexicon_dataset d "
            + "WHERE d.dataset_id=1 AND d.lexicon_version=?) "
            + "ORDER BY l.normalized_form, l.entry_id",
        (result, row) -> candidate(result, version),
        limit,
        limit,
        limit,
        version);
  }

  private static LexiconHintCandidate candidate(java.sql.ResultSet result, long version)
      throws SQLException {
    String lemma = result.getString("lemma");
    String gloss = result.getString("gloss");
    var action = gloss == null ? LexiconHintAction.BLOCK : LexiconHintAction.HINT;
    return new LexiconHintCandidate(
        result.getLong("entry_id"),
        action == LexiconHintAction.HINT ? LexiconIdentity.senseId(version, lemma) : null,
        version,
        "en",
        result.getString("normalized_form"),
        lemma,
        lemma.contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
        action,
        gloss,
        result.getInt("priority"));
  }

  @Override
  public long publish(
      LexiconImportMetadata metadata,
      long sourceRowsTotal,
      long expectedEntries,
      LexiconPublicationRepository.PreparedEntrySource source,
      LexiconPublicationRepository.PublicationProgress progress) {
    Objects.requireNonNull(metadata, "metadata");
    Objects.requireNonNull(source, "source");
    if (sourceRowsTotal < expectedEntries || expectedEntries < 1) {
      throw new IllegalArgumentException("invalid completed source counts");
    }
    return transaction.execute(
        status -> {
          safe(progress::persistStarted);
          if (org.springframework.transaction.support.TransactionSynchronizationManager
              .isSynchronizationActive()) {
            org.springframework.transaction.support.TransactionSynchronizationManager
                .registerSynchronization(
                    new org.springframework.transaction.support.TransactionSynchronization() {
                      @Override
                      public void afterCompletion(int completionStatus) {
                        if (completionStatus == STATUS_ROLLED_BACK) safe(progress::rolledBack);
                      }
                    });
          }
          long previous = rawPublishedVersion();
          long version = Math.max(previous + 1, clock.millis());
          if (version < 1 || version > 9_007_199_254_740_991L) {
            throw new IllegalStateException("publication version is outside safe integer range");
          }
          jdbc.update("DELETE FROM lexicon_dataset WHERE dataset_id = 1");
          jdbc.update("DELETE FROM lexicon_form");
          jdbc.update("DELETE FROM lexicon_entry");
          var writer = new BatchWriter();
          try {
            source.read(version, writer::add);
          } catch (IOException exception) {
            throw new java.io.UncheckedIOException("source changed during publication", exception);
          }
          writer.flush();
          if (writer.scannedEntries != expectedEntries) {
            throw new IllegalStateException("source entry count changed after preflight");
          }
          if (writer.entries == 0 || writer.lookups == 0) {
            throw new IllegalStateException("publication has no queryable entries");
          }
          long actualEntries =
              jdbc.queryForObject("SELECT COUNT(*) FROM lexicon_entry", Long.class);
          long actualForms = jdbc.queryForObject("SELECT COUNT(*) FROM lexicon_form", Long.class);
          long actualLookups =
              jdbc.queryForObject("SELECT COUNT(*) FROM lexicon_lookup", Long.class);
          if (actualEntries != writer.entries
              || actualForms != writer.forms
              || actualLookups != writer.lookups) {
            throw new IllegalStateException("persisted lexicon row counts are inconsistent");
          }
          safe(() -> progress.persisted(writer.entries, writer.lookups));
          String contentSha256 = jdbc.execute(DatasetContentChecksum::calculate);
          var generation = metadata.generation();
          String sourceManifest =
              jdbc.queryForObject(
                  "SELECT json_array(json_object('source_id', ?, 'license_id', ?, "
                      + "'source_digest', ?, 'acquired_at', ?))",
                  String.class,
                  metadata.sourceId(),
                  metadata.licenseId(),
                  metadata.sourceDigest(),
                  metadata.acquiredAt().toString());
          jdbc.update(
              "INSERT INTO lexicon_dataset (dataset_id, lexicon_version, source_manifest, "
                  + "source_row_count, entry_count, lookup_count, preparation_policy, "
                  + "generation_version,generation_code_version,generation_source_digest,generation_config,generation_inputs,content_sha256) "
                  + "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              version,
              sourceManifest,
              sourceRowsTotal,
              writer.entries,
              writer.lookups,
              HintPreparation.POLICY_ID,
              generation.generationVersion(),
              generation.codeVersion(),
              generation.sourceDigest(),
              generation.configJson(),
              generation.inputsJson(),
              contentSha256);
          return version;
        });
  }

  private static void safe(Runnable callback) {
    try {
      callback.run();
    } catch (RuntimeException ignored) {
    }
  }

  /** 在一个事务内按固定分块批量写入词条及窗口内准确词形。 */
  private final class BatchWriter {
    private final List<LexiconImportPlan.PlannedEntry> rows = new ArrayList<>(BATCH_SIZE);
    private long scannedEntries;
    private long entries;
    private long lookups;
    private long forms;

    void add(LexiconImportPlan.PlannedEntry planned) {
      scannedEntries++;
      rows.add(planned);
      if (rows.size() == BATCH_SIZE) flush();
    }

    void flush() {
      if (rows.isEmpty()) return;
      var entryRows = new ArrayList<Object[]>();
      var formRows = new ArrayList<Object[]>();
      for (var planned : rows) {
        var entry = planned.entry();
        var row = planned.row();
        String gloss = planned.prepared().gloss();
        var action = gloss == null ? LexiconHintAction.BLOCK : LexiconHintAction.HINT;
        var forms = new LinkedHashSet<String>();
        entry.aliases().stream()
            .map(alias -> alias.normalizedForm())
            .filter(LexiconSurfacePolicy::withinQueryWindow)
            .filter(form -> !form.equals(entry.lemma()))
            .forEach(forms::add);
        entry.inflections().stream()
            .map(form -> form.normalizedForm())
            .filter(LexiconSurfacePolicy::withinQueryWindow)
            .filter(form -> !form.equals(entry.lemma()))
            .forEach(forms::add);
        boolean canonical = LexiconSurfacePolicy.withinQueryWindow(entry.lemma());
        if (!canonical && forms.isEmpty()) continue;
        int priority =
            ClassificationPolicy.publishedPriority(
                row, planned.prepared().classification(), action);
        entryRows.add(new Object[] {entry.entryId(), entry.lemma(), gloss, canonical, priority});
        for (String form : forms) formRows.add(new Object[] {form, entry.entryId()});
      }
      if (!entryRows.isEmpty()) {
        jdbc.batchUpdate(
            "INSERT INTO lexicon_entry (entry_id, lemma, gloss, canonical_form, priority) VALUES (?, ?, ?, ?, ?)",
            batch(entryRows));
        jdbc.batchUpdate(
            "INSERT INTO lexicon_form (normalized_form, entry_id) VALUES (?, ?)", batch(formRows));
      }
      entries += entryRows.size();
      forms += formRows.size();
      lookups += formRows.size() + entryRows.stream().filter(row -> (boolean) row[3]).count();
      rows.clear();
    }
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
        for (int column = 0; column < values.length; column++)
          statement.setObject(column + 1, values[column]);
      }
    };
  }
}
