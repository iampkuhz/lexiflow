package io.lexiflow.lexicon.application;

import io.lexiflow.lexicon.domain.LexiconAlias;
import io.lexiflow.lexicon.domain.LexiconEntry;
import io.lexiflow.lexicon.domain.LexiconEntryKind;
import io.lexiflow.lexicon.domain.LexiconInflection;
import io.lexiflow.lexicon.domain.LexiconProvenance;
import io.lexiflow.lexicon.domain.LexiconSense;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.UUID;

/** 将受控导入行转为完整领域词条，并拒绝跨行表面冲突。 */
public final class LexiconImportPlan {
  private LexiconImportPlan() {}

  /**
   * 为一个完整发布版本准备全部词条。
   *
   * @param rows 含义：已解析的完整来源行。取值范围：由方法调用前置条件限定。
   * @param lexiconVersion 含义：将写入的词库版本。取值范围：由方法调用前置条件限定。
   * @param sourceDigest 含义：整个来源文件的内容摘要。取值范围：由方法调用前置条件限定。
   * @param acquiredAt 含义：获取来源的时刻。取值范围：由方法调用前置条件限定。
   * @return 已完成冲突校验的持久化计划
   */
  public static List<PlannedEntry> prepare(
      List<LexiconImportRow> rows, long lexiconVersion, String sourceDigest, Instant acquiredAt) {
    var entries = new ArrayList<PlannedEntry>();
    var owners = new HashMap<String, String>();
    var lemmas = new java.util.HashSet<String>();
    for (var row : rows) {
      var planned = fromRow(row, lexiconVersion, sourceDigest, acquiredAt);
      var entry = planned.entry();
      if (!lemmas.add(entry.lemma())) {
        throw new IllegalArgumentException("lemma is duplicated: " + entry.lemma());
      }
      registerSurfaces(owners, entry);
      entries.add(planned);
    }
    return List.copyOf(entries);
  }

  /**
   * 将一条已解析行投影为指定版本的领域词条。
   *
   * @param row 含义：已解析的来源行。取值范围：由方法调用前置条件限定。
   * @param lexiconVersion 含义：将写入的词库版本。取值范围：由方法调用前置条件限定。
   * @param sourceDigest 含义：整个来源文件的内容摘要。取值范围：由方法调用前置条件限定。
   * @param acquiredAt 含义：获取来源的时刻。取值范围：由方法调用前置条件限定。
   * @return 词条及其原始来源行
   */
  public static PlannedEntry fromRow(
      LexiconImportRow row, long lexiconVersion, String sourceDigest, Instant acquiredAt) {
    var entry =
        new LexiconEntry(
            stableId("entry:" + row.lemma()),
            lexiconVersion,
            "en",
            row.lemma().trim().contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
            row.lemma(),
            List.of(
                new LexiconSense(
                    stableId("sense:" + lexiconVersion + ":" + row.lemma()),
                    row.chineseGloss(),
                    row.definition(),
                    row.dictionary().recordReference())),
            row.aliases().stream().map(LexiconAlias::new).toList(),
            row.inflections().stream().map(LexiconInflection::new).toList(),
            new LexiconProvenance(
                row.dictionary().sourceId(),
                row.dictionary().licenseId(),
                sourceDigest,
                acquiredAt),
            row.priority());
    return new PlannedEntry(entry, row);
  }

  private static UUID stableId(String value) {
    return UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8));
  }

  private static void registerSurfaces(HashMap<String, String> owners, LexiconEntry entry) {
    register(owners, entry.term(), entry.lemma());
    for (var alias : entry.aliases()) {
      register(owners, alias.normalizedForm(), entry.lemma());
    }
    for (var inflection : entry.inflections()) {
      register(owners, inflection.normalizedForm(), entry.lemma());
    }
  }

  private static void register(HashMap<String, String> owners, String surface, String lemma) {
    var existing = owners.putIfAbsent(surface, lemma);
    if (existing != null && !existing.equals(lemma)) {
      throw new IllegalArgumentException(
          "surface " + surface + " belongs to both " + existing + " and " + lemma);
    }
  }

  /**
   * 已通过冲突校验且可被持久化的词条及其来源行。
   *
   * @param entry 含义：完整领域词条。取值范围：由方法调用前置条件限定。
   * @param row 含义：对应的来源行。取值范围：由方法调用前置条件限定。
   */
  public record PlannedEntry(LexiconEntry entry, LexiconImportRow row) {}
}
