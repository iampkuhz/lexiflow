package io.lexiflow.lexicon.platform.persistence;

import java.util.List;
import java.util.Map;
import java.util.Set;

/** Lexicon 快照允许传输的三张表及其固定列白名单。 */
public final class DatasetPackageTables {
  /**
   * 固定表名、列、排序及每列的 SQL 类型。
   *
   * @param name 含义：受控 Lexicon 表名。取值范围：三张最新词库表之一。
   * @param columns 含义：最新 SQL 的固定列顺序。取值范围：非空且与表结构一致。
   * @param orderBy 含义：固定导出排序表达式。取值范围：内置列表达式。
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
              "generation_version",
              "generation_code_version",
              "generation_source_digest",
              "generation_config",
              "generation_inputs",
              "content_sha256"),
          "dataset_id");
  public static final Table ENTRY =
      new Table(
          "lexicon_entry",
          List.of("entry_id", "lemma", "gloss", "canonical_form", "priority"),
          "entry_id");
  public static final Table FORM =
      new Table(
          "lexicon_form",
          List.of("normalized_form", "entry_id"),
          "normalized_form COLLATE BINARY, entry_id");
  public static final List<Table> TABLES = List.of(DATASET, ENTRY, FORM);
  public static final Map<String, String> TYPES =
      Map.ofEntries(
          Map.entry("dataset_id", "INTEGER"),
          Map.entry("lexicon_version", "INTEGER"),
          Map.entry("source_manifest", "TEXT"),
          Map.entry("source_row_count", "INTEGER"),
          Map.entry("entry_count", "INTEGER"),
          Map.entry("lookup_count", "INTEGER"),
          Map.entry("preparation_policy", "TEXT"),
          Map.entry("generation_version", "TEXT"),
          Map.entry("generation_code_version", "TEXT"),
          Map.entry("generation_source_digest", "TEXT"),
          Map.entry("generation_config", "TEXT"),
          Map.entry("generation_inputs", "TEXT"),
          Map.entry("content_sha256", "TEXT"),
          Map.entry("entry_id", "INTEGER"),
          Map.entry("lemma", "TEXT"),
          Map.entry("gloss", "TEXT"),
          Map.entry("canonical_form", "INTEGER"),
          Map.entry("priority", "INTEGER"),
          Map.entry("normalized_form", "TEXT"));

  private DatasetPackageTables() {}
}
