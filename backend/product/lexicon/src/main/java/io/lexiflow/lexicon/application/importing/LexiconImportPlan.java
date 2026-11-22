package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.PreparedHint;
import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import io.lexiflow.lexicon.domain.model.LexiconAlias;
import io.lexiflow.lexicon.domain.model.LexiconEntry;
import io.lexiflow.lexicon.domain.model.LexiconEntryKind;
import io.lexiflow.lexicon.domain.model.LexiconInflection;
import io.lexiflow.lexicon.domain.model.LexiconProvenance;
import io.lexiflow.lexicon.domain.model.LexiconSense;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;

/** 将受控导入行转为完整领域词条，并拒绝跨行 canonical 表面冲突。 */
public final class LexiconImportPlan {
  private LexiconImportPlan() {}

  /** 在同一行序列上执行全量规范表面校验并准备发布记录。 */
  public static List<PlannedEntry> prepare(
      List<LexiconImportRow> rows, long version, String sourceDigest, Instant acquiredAt) {
    var surfaces = canonicalSurfaceValidator();
    return List.copyOf(rows).stream()
        .map(row -> prepareNext(row, version, sourceDigest, acquiredAt, surfaces))
        .toList();
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
    return new CanonicalSurfaceValidator();
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
    var prepared = HintPreparation.prepare(row);
    var entry =
        new LexiconEntry(
            LexiconIdentity.entryId(row.lemma()),
            lexiconVersion,
            "en",
            row.lemma().trim().contains(" ") ? LexiconEntryKind.PHRASE : LexiconEntryKind.WORD,
            row.lemma(),
            List.of(
                new LexiconSense(
                    LexiconIdentity.senseId(lexiconVersion, row.lemma()),
                    // 空首项仍保留有来源的词条，但不能构造虚假的空义项；发布资格由同一次 prepared 决策确定。
                    prepared.gloss() != null
                        ? prepared.gloss()
                        : row.chineseGloss().isBlank() ? row.sourceGloss() : row.chineseGloss(),
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
            row.sourceBncRank());
    return new PlannedEntry(entry, row, prepared);
  }

  /** 跨行维护 lemma/alias 唯一性；不登记自然屈折形。 */
  public static final class CanonicalSurfaceValidator {
    private final HashMap<String, String> owners = new HashMap<>();

    private CanonicalSurfaceValidator() {}

    /**
     * 登记一条领域词条的 lemma 和 alias。
     *
     * @param entry 含义：已经通过单条领域校验的词条。取值范围：非空且 canonical 表面不与已登记词条冲突。
     */
    public void register(LexiconEntry entry) {
      registerLemma(entry.lemma());
      for (var alias : entry.aliases()) {
        registerAlias(alias.normalizedForm(), entry.lemma());
      }
    }

    private void registerLemma(String lemma) {
      var existing = owners.putIfAbsent(lemma, lemma);
      if (existing != null && existing.equals(lemma)) {
        throw new IllegalArgumentException("lemma is duplicated: " + lemma);
      }
      if (existing != null) {
        throw collision(lemma, existing, lemma);
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
   * @param prepared 同一来源行的确定性准备结果，供释义与查询投影一致使用。
   */
  public record PlannedEntry(LexiconEntry entry, LexiconImportRow row, PreparedHint prepared) {
    /** 校验持久化计划必备值。 */
    public PlannedEntry {
      if (entry == null || row == null || prepared == null) {
        throw new IllegalArgumentException("planned entry values are required");
      }
    }
  }
}
