package io.lexiflow.lexicon.platform.persistence;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Lexicon 快照允许传输的三张表及其固定列白名单。 */
public final class DatasetPackageTables {
  /**
   * 固定表名、列、排序及每列的 SQL 类型。
   *
   * @param name 含义：受控 Lexicon 表名。取值范围：三表之一。
   * @param columns 含义：最新 SQL 的固定列顺序。取值范围：非空。
   * @param orderBy 含义：固定导出排序表达式。取值范围：内置常量。
   */
  public record Table(String name, List<String> columns, String orderBy) {
    /** 返回本表固定列名集合。 */
    Set<String> columnSet() {
      return Set.copyOf(columns);
    }
  }

  public static final Table DATASET =
      new Table(
          "lexicon_dataset",
          List.of(
              "dataset_id",
              "lexicon_version",
              "source_manifest",
              "source_row_count",
              "entry_count",
              "lookup_count",
              "preparation_policy",
              "imported_at"),
          "dataset_id");
  public static final Table PREPARED =
      new Table(
          "lexicon_prepared_entry",
          List.of(
              "lexicon_entry_id",
              "language_tag",
              "lemma",
              "entry_kind",
              "source_gloss",
              "source_gloss_ref",
              "source_dictionary_id",
              "source_frequency_id",
              "source_frequency_ref",
              "source_bnc_rank",
              "source_frq_rank",
              "source_complex_tags",
              "source_oxford_basic",
              "prepared_gloss",
              "exclusion_reason",
              "frequency_evidence",
              "decisive_rule",
              "matched_rules",
              "prepared_priority",
              "frequency_zipf",
              "complex_list_count"),
          "lexicon_entry_id");
  public static final Table LOOKUP =
      new Table(
          "lexicon_hint_lookup",
          List.of(
              "language_tag",
              "normalized_form",
              "lexicon_entry_id",
              "form_kind",
              "canonical_lemma",
              "entry_kind",
              "final_action",
              "final_decision_reason",
              "final_gloss",
              "final_priority",
              "final_sense_id",
              "final_frequency_zipf",
              "final_complex_list_count",
              "cache_priority"),
          "language_tag, normalized_form COLLATE \"C\", lexicon_entry_id");
  public static final List<Table> TABLES = List.of(DATASET, PREPARED, LOOKUP);
  public static final Map<String, String> TYPES =
      Map.ofEntries(
          Map.entry("dataset_id", "smallint"), Map.entry("lexicon_version", "bigint"),
          Map.entry("source_manifest", "jsonb"), Map.entry("source_row_count", "bigint"),
          Map.entry("entry_count", "bigint"), Map.entry("lookup_count", "bigint"),
          Map.entry("preparation_policy", "text"), Map.entry("imported_at", "timestamptz"),
          Map.entry("lexicon_entry_id", "uuid"), Map.entry("language_tag", "text"),
          Map.entry("lemma", "text"), Map.entry("entry_kind", "text"),
          Map.entry("source_gloss", "text"), Map.entry("source_gloss_ref", "text"),
          Map.entry("source_dictionary_id", "text"), Map.entry("source_frequency_id", "text"),
          Map.entry("source_frequency_ref", "text"), Map.entry("source_bnc_rank", "bigint"),
          Map.entry("source_frq_rank", "bigint"), Map.entry("source_complex_tags", "text[]"),
          Map.entry("source_oxford_basic", "boolean"), Map.entry("prepared_gloss", "text"),
          Map.entry("exclusion_reason", "text"), Map.entry("frequency_evidence", "text"),
          Map.entry("decisive_rule", "text"), Map.entry("matched_rules", "text[]"),
          Map.entry("prepared_priority", "integer"), Map.entry("frequency_zipf", "numeric"),
          Map.entry("complex_list_count", "smallint"), Map.entry("normalized_form", "text"),
          Map.entry("form_kind", "text"), Map.entry("canonical_lemma", "text"),
          Map.entry("final_action", "text"), Map.entry("final_decision_reason", "text"),
          Map.entry("final_gloss", "text"), Map.entry("final_priority", "integer"),
          Map.entry("final_sense_id", "uuid"), Map.entry("final_frequency_zipf", "numeric"),
          Map.entry("final_complex_list_count", "smallint"),
              Map.entry("cache_priority", "integer"));

  private DatasetPackageTables() {}
}
