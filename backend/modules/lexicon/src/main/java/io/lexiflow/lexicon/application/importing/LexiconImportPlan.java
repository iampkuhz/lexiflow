package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.domain.model.LexiconAlias;
import io.lexiflow.lexicon.domain.model.LexiconEntry;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconInflection;
import io.lexiflow.lexicon.domain.model.LexiconProvenance;
import io.lexiflow.lexicon.domain.model.LexiconSense;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** 将受控导入行转为完整领域词条，并拒绝跨行 canonical 表面冲突。 */
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
    var canonicalSurfaces = canonicalSurfaceValidator();
    for (var row : rows) {
      entries.add(prepareNext(row, lexiconVersion, sourceDigest, acquiredAt, canonicalSurfaces));
    }
    return List.copyOf(entries);
  }

  /**
   * 将一条流式来源行投影并登记到跨分块 canonical 表面校验器。
   *
   * <p>lemma 和 alias 是唯一 canonical 表面；inflection 可以自然地指向多个词条，因此只保留其 词条内领域校验，不参与跨词条冲突拒绝。
   *
   * @param row 含义：已解析的来源行。取值范围：由方法调用前置条件限定。
   * @param lexiconVersion 含义：将写入的词库版本。取值范围：由方法调用前置条件限定。
   * @param sourceDigest 含义：整个来源文件的内容摘要。取值范围：由方法调用前置条件限定。
   * @param acquiredAt 含义：获取来源的时刻。取值范围：由方法调用前置条件限定。
   * @param canonicalSurfaces 含义：同一完整来源扫描共享的 canonical 表面校验器。取值范围：非空。
   * @return 已完成单行与跨分块 canonical 校验的计划词条
   */
  public static PlannedEntry prepareNext(
      LexiconImportRow row,
      long lexiconVersion,
      String sourceDigest,
      Instant acquiredAt,
      CanonicalSurfaceValidator canonicalSurfaces) {
    var planned = fromRow(row, lexiconVersion, sourceDigest, acquiredAt);
    canonicalSurfaces.register(planned.entry());
    return planned;
  }

  /**
   * 创建供一整个流式来源扫描复用的 canonical 表面校验器。
   *
   * @return 空的 canonical 表面校验器
   */
  public static CanonicalSurfaceValidator canonicalSurfaceValidator() {
    return new CanonicalSurfaceValidator(Map.of());
  }

  /**
   * 以已持久化的 canonical 表面初始化校验器，用于 staged 分块续入。
   *
   * @param owners 含义：canonical 表面到其 lemma 的映射。取值范围：不可为 null。
   * @return 已登记给定表面的 canonical 校验器
   */
  public static CanonicalSurfaceValidator canonicalSurfaceValidator(Map<String, String> owners) {
    return new CanonicalSurfaceValidator(owners);
  }

  /**
   * 返回计划词条的 canonical 表面，用于仅查询同版本的既有 staged 词条。
   *
   * @param entries 含义：已完成单行领域校验的计划词条。取值范围：不可为 null。
   * @return 去重后的 lemma 与 alias 表面
   */
  public static List<String> canonicalSurfaces(List<PlannedEntry> entries) {
    var surfaces = new HashSet<String>();
    for (var planned : entries) {
      surfaces.add(planned.entry().term());
      planned.entry().aliases().forEach(alias -> surfaces.add(alias.normalizedForm()));
    }
    return List.copyOf(surfaces);
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
            row.priority(),
            row.basicVocabulary()
                ? io.lexiflow.lexicon.domain.model.LexiconHintEligibility.BASIC_VOCABULARY
                : io.lexiflow.lexicon.domain.model.LexiconHintEligibility.CANDIDATE);
    return new PlannedEntry(entry, row);
  }

  private static UUID stableId(String value) {
    return UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8));
  }

  /** 跨行维护 lemma/alias 唯一性；不登记自然屈折形。 */
  public static final class CanonicalSurfaceValidator {
    private final HashMap<String, String> owners;

    private CanonicalSurfaceValidator(Map<String, String> owners) {
      this.owners = new HashMap<>(owners);
    }

    /**
     * 登记一条领域词条的 lemma 和 alias。
     *
     * @param entry 含义：已经通过单条领域校验的词条。取值范围：非空且 canonical 表面不与已登记词条冲突。
     */
    public void register(LexiconEntry entry) {
      registerLemma(entry.term(), entry.lemma());
      for (var alias : entry.aliases()) {
        registerAlias(alias.normalizedForm(), entry.lemma());
      }
    }

    private void registerLemma(String surface, String lemma) {
      var existing = owners.putIfAbsent(surface, lemma);
      if (existing != null && existing.equals(lemma)) {
        throw new IllegalArgumentException("lemma is duplicated: " + lemma);
      }
      if (existing != null) {
        throw collision(surface, existing, lemma);
      }
    }

    private void registerAlias(String surface, String lemma) {
      var existing = owners.putIfAbsent(surface, lemma);
      if (existing != null && !existing.equals(lemma)) {
        throw collision(surface, existing, lemma);
      }
    }

    private static IllegalArgumentException collision(
        String surface, String existing, String lemma) {
      return new IllegalArgumentException(
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
