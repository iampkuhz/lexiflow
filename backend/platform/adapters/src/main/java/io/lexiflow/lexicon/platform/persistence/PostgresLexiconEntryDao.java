package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;

/** PostgreSQL 中词条、义项、别名和屈折形的表级 DAO。 */
final class PostgresLexiconEntryDao implements LexiconEntryDao {
  private static final String ENTRY_COLUMNS =
      "e.lexicon_entry_id, e.lexicon_version, e.language_tag, e.entry_kind, e.lemma, "
          + "e.provenance_source_id, e.provenance_license_id, e.provenance_digest, e.acquired_at, "
          + "e.frequency_zipf, e.complex_list_count, e.memory_priority, e.hint_eligibility, "
          + "s.sense_id, s.chinese_gloss, s.definition_text, s.provenance_reference ";
  private final JdbcClient jdbc;
  private final JdbcTemplate batchJdbc;

  PostgresLexiconEntryDao(JdbcClient jdbc, JdbcTemplate batchJdbc) {
    this.jdbc = jdbc;
    this.batchJdbc = batchJdbc;
  }

  @Override
  public List<LexiconEntryDO> findByForms(long version, Collection<String> forms) {
    if (forms.isEmpty()) {
      return List.of();
    }
    var formPlaceholders = bindNames("form", forms.size());
    var entryKeyPlaceholders = bindNames("entryKey", forms.size());
    var query =
        jdbc.sql(findByFormsSql(formPlaceholders, entryKeyPlaceholders)).param("version", version);
    var index = 0;
    for (var form : forms) {
      query = query.param("form" + index, form);
      query = query.param("entryKey" + index, normalizedEntryKey(form));
      index += 1;
    }
    return enrich(query.query((resultSet, rowNumber) -> raw(resultSet)).list());
  }

  static String findByFormsSql(String formPlaceholders, String entryKeyPlaceholders) {
    var sql =
        "WITH matched_entries AS ("
            + "SELECT lexicon_entry_id, lexicon_version FROM lexicon_entry "
            + "WHERE language_tag = 'en' AND lexicon_version = :version AND normalized_key IN (%s) "
            + "UNION SELECT lexicon_entry_id, lexicon_version FROM lexicon_alias "
            + "WHERE lexicon_version = :version AND normalized_form IN (%s) "
            + "UNION SELECT lexicon_entry_id, lexicon_version FROM lexicon_inflection "
            + "WHERE lexicon_version = :version AND normalized_form IN (%s)) "
            + "SELECT "
            + ENTRY_COLUMNS
            + "FROM matched_entries m JOIN lexicon_entry e USING (lexicon_entry_id, lexicon_version) "
            + "JOIN lexicon_sense s USING (lexicon_entry_id, lexicon_version)";
    return sql.formatted(entryKeyPlaceholders, formPlaceholders, formPlaceholders);
  }

  static String normalizedEntryKey(String form) {
    return "en:" + (form.contains(" ") ? "phrase" : "word") + ":" + form;
  }

  static String findCanonicalOwnersSql(String entryKeyPlaceholders, String formPlaceholders) {
    var sql =
        "SELECT lemma AS surface, lemma AS owner FROM lexicon_entry "
            + "WHERE language_tag = 'en' AND lexicon_version = :version AND normalized_key IN (%s) "
            + "UNION ALL "
            + "SELECT a.normalized_form AS surface, e.lemma AS owner "
            + "FROM lexicon_alias a JOIN lexicon_entry e "
            + "USING (lexicon_entry_id, lexicon_version) "
            + "WHERE a.lexicon_version = :version AND a.normalized_form IN (%s)";
    return sql.formatted(entryKeyPlaceholders, formPlaceholders);
  }

  @Override
  public List<LexiconEntryDO> findPrewarmCandidates(long version, int limit) {
    var rows =
        jdbc.sql(
                "SELECT "
                    + ENTRY_COLUMNS
                    + "FROM lexicon_entry e JOIN lexicon_sense s USING (lexicon_entry_id, lexicon_version) "
                    + "WHERE e.lexicon_version = :version AND e.prewarm_eligible AND e.hint_eligibility = 'CANDIDATE' "
                    + "ORDER BY e.memory_priority DESC, e.normalized_key LIMIT :limit")
            .param("version", version)
            .param("limit", limit)
            .query((resultSet, rowNumber) -> raw(resultSet))
            .list();
    return enrich(rows);
  }

  @Override
  public Map<String, String> findCanonicalOwners(long version, Collection<String> surfaces) {
    if (surfaces.isEmpty()) {
      return Map.of();
    }
    var formPlaceholders = bindNames("form", surfaces.size());
    var entryKeyPlaceholders = bindNames("entryKey", surfaces.size());
    var query =
        jdbc.sql(findCanonicalOwnersSql(entryKeyPlaceholders, formPlaceholders))
            .param("version", version);
    var index = 0;
    for (var surface : surfaces) {
      query = query.param("entryKey" + index, normalizedEntryKey(surface));
      query = query.param("form" + index, surface);
      index += 1;
    }
    var owners = new HashMap<String, String>();
    query
        .query(
            (resultSet, rowNumber) ->
                Map.entry(resultSet.getString("surface"), resultSet.getString("owner")))
        .list()
        .forEach(
            value -> {
              var previous = owners.putIfAbsent(value.getKey(), value.getValue());
              if (previous != null && !previous.equals(value.getValue())) {
                throw new IllegalStateException(
                    "staged canonical surface "
                        + value.getKey()
                        + " belongs to both "
                        + previous
                        + " and "
                        + value.getValue());
              }
            });
    return Map.copyOf(owners);
  }

  @Override
  public void insertEntries(List<LexiconImportPlan.PlannedEntry> entries, long version) {
    var entryArguments = new ArrayList<Object[]>();
    var senseArguments = new ArrayList<Object[]>();
    var aliasArguments = new ArrayList<Object[]>();
    var inflectionArguments = new ArrayList<Object[]>();
    for (var planned : entries) {
      var entry = planned.entry();
      entryArguments.add(
          new Object[] {
            entry.entryId(),
            version,
            entry.languageTag(),
            entry.entryKind().name().toLowerCase(java.util.Locale.ROOT),
            entry.lemma(),
            entry.normalizedKey(),
            entry.provenance().sourceId(),
            entry.provenance().licenseId(),
            entry.provenance().contentDigest(),
            Timestamp.from(entry.provenance().acquiredAt()),
            entry.priority().frequencyZipf(),
            entry.priority().complexListCount(),
            entry.priority().memoryPriority(),
            planned.row().prewarmEligible(),
            entry.hintEligibility().name(),
            planned.row().hintPolicyReference()
          });
      for (var sense : entry.senses()) {
        senseArguments.add(
            new Object[] {
              sense.senseId(),
              entry.entryId(),
              version,
              sense.chineseGloss(),
              sense.definition(),
              sense.provenanceReference()
            });
      }
      entry
          .aliases()
          .forEach(
              alias ->
                  aliasArguments.add(
                      new Object[] {entry.entryId(), version, alias.normalizedForm()}));
      entry
          .inflections()
          .forEach(
              inflection ->
                  inflectionArguments.add(
                      new Object[] {entry.entryId(), version, inflection.normalizedForm()}));
    }
    batchJdbc.batchUpdate(
        "INSERT INTO lexicon_entry (lexicon_entry_id, lexicon_version, language_tag, entry_kind, lemma, normalized_key, "
            + "provenance_source_id, provenance_license_id, provenance_digest, acquired_at, frequency_zipf, complex_list_count, memory_priority, prewarm_eligible, hint_eligibility, hint_policy_reference) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        entryArguments);
    batchJdbc.batchUpdate(
        "INSERT INTO lexicon_sense (sense_id, lexicon_entry_id, lexicon_version, chinese_gloss, definition_text, provenance_reference) "
            + "VALUES (?, ?, ?, ?, ?, ?)",
        senseArguments);
    insertForms("lexicon_alias", aliasArguments);
    insertForms("lexicon_inflection", inflectionArguments);
  }

  @Override
  public long countEntries(long version) {
    return jdbc.sql("SELECT COUNT(*) FROM lexicon_entry WHERE lexicon_version = :version")
        .param("version", version)
        .query(Long.class)
        .single();
  }

  private static String bindNames(String prefix, int count) {
    var names = new ArrayList<String>();
    for (var index = 0; index < count; index++) {
      names.add(":" + prefix + index);
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

  private void insertForms(String table, List<Object[]> arguments) {
    if (arguments.isEmpty()) {
      return;
    }
    batchJdbc.batchUpdate(
        "INSERT INTO "
            + table
            + " (lexicon_entry_id, lexicon_version, normalized_form) VALUES (?, ?, ?)",
        arguments);
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
            row.getString("provenance_reference")),
        row.getString("hint_eligibility"));
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
   * @param hintEligibility 发布资料的提示资格。
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
      LexiconSenseDO sense,
      String hintEligibility) {}

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
    private final String hintEligibility;
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
      hintEligibility = row.hintEligibility();
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
          List.copyOf(inflections),
          hintEligibility);
    }
  }
}
