package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.LexiconImportPlan;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;

/** PostgreSQL 中词条、义项、别名和屈折形的表级 DAO。 */
final class PostgresLexiconEntryDao implements LexiconEntryDao {
  private static final String ENTRY_COLUMNS =
      "e.lexicon_entry_id, e.lexicon_version, e.language_tag, e.entry_kind, e.lemma, "
          + "e.provenance_source_id, e.provenance_license_id, e.provenance_digest, e.acquired_at, "
          + "e.frequency_zipf, e.complex_list_count, e.memory_priority, "
          + "s.sense_id, s.chinese_gloss, s.definition_text, s.provenance_reference ";
  private final JdbcClient jdbc;

  PostgresLexiconEntryDao(JdbcClient jdbc) {
    this.jdbc = jdbc;
  }

  @Override
  public List<LexiconEntryDO> findByForms(long version, Collection<String> forms) {
    if (forms.isEmpty()) {
      return List.of();
    }
    var placeholders = bindNames(forms);
    var sql =
        "SELECT "
            + ENTRY_COLUMNS
            + "FROM lexicon_entry e JOIN lexicon_sense s USING (lexicon_entry_id, lexicon_version) "
            + "WHERE e.lexicon_version = :version AND (e.lemma IN (%s) "
            + "OR EXISTS (SELECT 1 FROM lexicon_alias a WHERE a.lexicon_entry_id = e.lexicon_entry_id "
            + "AND a.lexicon_version = e.lexicon_version AND a.normalized_form IN (%s)) "
            + "OR EXISTS (SELECT 1 FROM lexicon_inflection i WHERE i.lexicon_entry_id = e.lexicon_entry_id "
            + "AND i.lexicon_version = e.lexicon_version AND i.normalized_form IN (%s)))";
    var query = jdbc.sql(sql.formatted(placeholders, placeholders, placeholders));
    query = query.param("version", version);
    var index = 0;
    for (var form : forms) {
      query = query.param("form" + index++, form);
    }
    var rows = query.query((resultSet, rowNumber) -> raw(resultSet)).list();
    return enrich(rows);
  }

  @Override
  public List<LexiconEntryDO> findPrewarmCandidates(long version, int limit) {
    var rows =
        jdbc.sql(
                "SELECT "
                    + ENTRY_COLUMNS
                    + "FROM lexicon_entry e JOIN lexicon_sense s USING (lexicon_entry_id, lexicon_version) "
                    + "WHERE e.lexicon_version = :version AND e.prewarm_eligible "
                    + "ORDER BY e.memory_priority DESC, e.normalized_key LIMIT :limit")
            .param("version", version)
            .param("limit", limit)
            .query((resultSet, rowNumber) -> raw(resultSet))
            .list();
    return enrich(rows);
  }

  @Override
  public void insertEntries(List<LexiconImportPlan.PlannedEntry> entries, long version) {
    for (var planned : entries) {
      var entry = planned.entry();
      jdbc.sql(
              "INSERT INTO lexicon_entry (lexicon_entry_id, lexicon_version, language_tag, entry_kind, lemma, normalized_key, "
                  + "provenance_source_id, provenance_license_id, provenance_digest, acquired_at, frequency_zipf, complex_list_count, memory_priority, prewarm_eligible) "
                  + "VALUES (:id, :version, :language, :kind, :lemma, :key, :source, :license, :digest, :acquired, :zipf, :complex, :priority, :prewarm)")
          .param("id", entry.entryId())
          .param("version", version)
          .param("language", entry.languageTag())
          .param("kind", entry.entryKind().name().toLowerCase(java.util.Locale.ROOT))
          .param("lemma", entry.lemma())
          .param("key", entry.normalizedKey())
          .param("source", entry.provenance().sourceId())
          .param("license", entry.provenance().licenseId())
          .param("digest", entry.provenance().contentDigest())
          .param("acquired", Timestamp.from(entry.provenance().acquiredAt()))
          .param("zipf", entry.priority().frequencyZipf())
          .param("complex", entry.priority().complexListCount())
          .param("priority", entry.priority().memoryPriority())
          .param("prewarm", planned.row().prewarmEligible())
          .update();
      for (var sense : entry.senses()) {
        jdbc.sql(
                "INSERT INTO lexicon_sense (sense_id, lexicon_entry_id, lexicon_version, chinese_gloss, definition_text, provenance_reference) "
                    + "VALUES (:id, :entryId, :version, :gloss, :definition, :reference)")
            .param("id", sense.senseId())
            .param("entryId", entry.entryId())
            .param("version", version)
            .param("gloss", sense.chineseGloss())
            .param("definition", sense.definition())
            .param("reference", sense.provenanceReference())
            .update();
      }
      insertForms(
          "lexicon_alias",
          entry.aliases().stream().map(value -> value.normalizedForm()).toList(),
          entry.entryId(),
          version);
      insertForms(
          "lexicon_inflection",
          entry.inflections().stream().map(value -> value.normalizedForm()).toList(),
          entry.entryId(),
          version);
    }
  }

  @Override
  public long countEntries(long version) {
    return jdbc.sql("SELECT COUNT(*) FROM lexicon_entry WHERE lexicon_version = :version")
        .param("version", version)
        .query(Long.class)
        .single();
  }

  private static String bindNames(Collection<String> forms) {
    var names = new ArrayList<String>();
    for (var index = 0; index < forms.size(); index++) {
      names.add(":form" + index);
    }
    return String.join(", ", names);
  }

  private List<LexiconEntryDO> enrich(List<RawEntryDO> rows) {
    var grouped = new LinkedHashMap<UUID, MutableEntryDO>();
    for (var row : rows) {
      grouped
          .computeIfAbsent(row.entryId(), ignored -> new MutableEntryDO(row))
          .addSense(row.sense());
    }
    if (grouped.isEmpty()) {
      return List.of();
    }
    var aliases =
        forms("lexicon_alias", grouped.keySet(), grouped.values().iterator().next().version());
    var inflections =
        forms("lexicon_inflection", grouped.keySet(), grouped.values().iterator().next().version());
    return grouped.values().stream()
        .map(
            value ->
                value.freeze(
                    aliases.getOrDefault(value.entryId(), List.<String>of()),
                    inflections.getOrDefault(value.entryId(), List.<String>of())))
        .toList();
  }

  private Map<UUID, List<String>> forms(String table, Collection<UUID> ids, long version) {
    var query =
        jdbc.sql(
                "SELECT lexicon_entry_id, normalized_form FROM "
                    + table
                    + " WHERE lexicon_version = :version AND lexicon_entry_id IN ("
                    + bindIds(ids)
                    + ")")
            .param("version", version);
    var index = 0;
    for (var id : ids) {
      query = query.param("id" + index++, id);
    }
    var result = new LinkedHashMap<UUID, List<String>>();
    query
        .query(
            (resultSet, rowNumber) ->
                Map.entry(resultSet.getObject(1, UUID.class), resultSet.getString(2)))
        .list()
        .forEach(
            value ->
                result
                    .computeIfAbsent(value.getKey(), ignored -> new ArrayList<>())
                    .add(value.getValue()));
    return result;
  }

  private static String bindIds(Collection<UUID> ids) {
    var names = new ArrayList<String>();
    for (var index = 0; index < ids.size(); index++) {
      names.add(":id" + index);
    }
    return String.join(", ", names);
  }

  private void insertForms(String table, List<String> forms, UUID entryId, long version) {
    for (var form : forms) {
      jdbc.sql(
              "INSERT INTO "
                  + table
                  + " (lexicon_entry_id, lexicon_version, normalized_form) VALUES (:entryId, :version, :form)")
          .param("entryId", entryId)
          .param("version", version)
          .param("form", form)
          .update();
    }
  }

  private static RawEntryDO raw(java.sql.ResultSet row) throws java.sql.SQLException {
    return new RawEntryDO(
        row.getObject("lexicon_entry_id", UUID.class),
        row.getLong("lexicon_version"),
        row.getString("language_tag"),
        row.getString("entry_kind"),
        row.getString("lemma"),
        row.getString("provenance_source_id"),
        row.getString("provenance_license_id"),
        row.getString("provenance_digest"),
        row.getTimestamp("acquired_at").toInstant(),
        row.getDouble("frequency_zipf"),
        row.getInt("complex_list_count"),
        row.getInt("memory_priority"),
        new LexiconSenseDO(
            row.getObject("sense_id", UUID.class),
            row.getString("chinese_gloss"),
            java.util.Objects.requireNonNullElse(row.getString("definition_text"), ""),
            row.getString("provenance_reference")));
  }

  /**
   * 单行联表读取的临时数据库对象。
   *
   * @param entryId 含义：词条主键。取值范围：由方法调用前置条件限定。
   * @param version 含义：词库版本。取值范围：由方法调用前置条件限定。
   * @param language 含义：语言标签。取值范围：由方法调用前置条件限定。
   * @param kind 含义：词条种类。取值范围：由方法调用前置条件限定。
   * @param lemma 含义：词条原形。取值范围：由方法调用前置条件限定。
   * @param source 含义：来源标识。取值范围：由方法调用前置条件限定。
   * @param license 含义：许可证标识。取值范围：由方法调用前置条件限定。
   * @param digest 含义：来源摘要。取值范围：由方法调用前置条件限定。
   * @param acquiredAt 含义：来源获取时刻。取值范围：由方法调用前置条件限定。
   * @param zipf 含义：Zipf 词频。取值范围：由方法调用前置条件限定。
   * @param complex 含义：复杂词表计数。取值范围：由方法调用前置条件限定。
   * @param priority 含义：记忆优先级。取值范围：由方法调用前置条件限定。
   * @param sense 含义：当前联表行的义项。取值范围：由方法调用前置条件限定。
   */
  private record RawEntryDO(
      UUID entryId,
      long version,
      String language,
      String kind,
      String lemma,
      String source,
      String license,
      String digest,
      java.time.Instant acquiredAt,
      double zipf,
      int complex,
      int priority,
      LexiconSenseDO sense) {}

  /** 在单次联表读取期间聚合一个词条的多个义项。 */
  private static final class MutableEntryDO {
    private final UUID entryId;
    private final long version;
    private final String language;
    private final String kind;
    private final String lemma;
    private final String source;
    private final String license;
    private final String digest;
    private final java.time.Instant acquiredAt;
    private final double zipf;
    private final int complex;
    private final int priority;
    private final List<LexiconSenseDO> senses = new ArrayList<>();

    MutableEntryDO(RawEntryDO row) {
      entryId = row.entryId();
      version = row.version();
      language = row.language();
      kind = row.kind();
      lemma = row.lemma();
      source = row.source();
      license = row.license();
      digest = row.digest();
      acquiredAt = row.acquiredAt();
      zipf = row.zipf();
      complex = row.complex();
      priority = row.priority();
    }

    UUID entryId() {
      return entryId;
    }

    long version() {
      return version;
    }

    void addSense(LexiconSenseDO sense) {
      senses.add(sense);
    }

    LexiconEntryDO freeze(List<String> aliases, List<String> inflections) {
      return new LexiconEntryDO(
          entryId,
          version,
          language,
          kind,
          lemma,
          source,
          license,
          digest,
          acquiredAt,
          zipf,
          complex,
          priority,
          List.copyOf(senses),
          List.copyOf(aliases),
          List.copyOf(inflections));
    }
  }
}
