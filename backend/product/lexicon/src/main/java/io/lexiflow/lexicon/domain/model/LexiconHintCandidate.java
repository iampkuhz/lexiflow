package io.lexiflow.lexicon.domain.model;

import java.util.Objects;
import java.util.UUID;

/**
 * 从已准备查询投影取得的一种精确词形；观看不再读取来源准备记录。
 *
 * @param entryId 含义：来源无关的规范词条身份。取值范围：正 long。
 * @param senseId 含义：可展示提示对应的义项身份。取值范围：提示时非空，阻断时为空。
 * @param lexiconVersion 含义：完整资料发布版本。取值范围：正 long。
 * @param languageTag 含义：词条语言标记。取值范围：当前仅为 en。
 * @param normalizedForm 含义：字幕可直接反查的准确词形。取值范围：非空且已规范化。
 * @param canonicalLemma 含义：所属规范主词形。取值范围：非空且已规范化。
 * @param entryKind 含义：主词条类别。取值范围：单词或固定短语。
 * @param finalAction 含义：冻结的发布决定。取值范围：提示或阻断。
 * @param finalGloss 含义：可展示时的安全短释。取值范围：提示时非空，阻断时为空。
 * @param priority 含义：非个人化提示与预热共用优先级。取值范围：0 至 1000，零不代表阻断。
 * @param frequencyRank 含义：已发布的原始 BNC 排名。取值范围：可空；存在时为正数。
 */
public record LexiconHintCandidate(
    long entryId,
    UUID senseId,
    long lexiconVersion,
    String languageTag,
    String normalizedForm,
    String canonicalLemma,
    LexiconEntryKind entryKind,
    LexiconHintAction finalAction,
    String finalGloss,
    int priority,
    Long frequencyRank) {

  /** 校验来源无关身份、准确词形和可靠提示与阻断互斥。 */
  public LexiconHintCandidate {
    if (entryId < 1) throw new IllegalArgumentException("entryId must be positive");
    if (lexiconVersion < 1 || !"en".equals(languageTag)) {
      throw new IllegalArgumentException("candidate version or language is invalid");
    }
    normalizedForm = LexiconEntry.normalizeEnglishForm(normalizedForm, "normalizedForm");
    canonicalLemma = LexiconEntry.normalizeEnglishForm(canonicalLemma, "canonicalLemma");
    Objects.requireNonNull(entryKind, "entryKind");
    Objects.requireNonNull(finalAction, "finalAction");
    if (priority < 0 || priority > 1000) {
      throw new IllegalArgumentException("candidate priority is invalid");
    }
    if (frequencyRank != null && frequencyRank < 1)
      throw new IllegalArgumentException("frequencyRank must be positive when present");
    if (finalAction == LexiconHintAction.HINT) {
      Objects.requireNonNull(senseId, "senseId");
      if (finalGloss == null || finalGloss.isBlank()) {
        throw new IllegalArgumentException("HINT requires a final gloss");
      }
    } else if (senseId != null || finalGloss != null) {
      throw new IllegalArgumentException("BLOCK cannot expose a display sense");
    }
  }

  /** 创建不含来源词频排名的提示候选。 */
  public LexiconHintCandidate(
      long entryId,
      UUID senseId,
      long lexiconVersion,
      String languageTag,
      String normalizedForm,
      String canonicalLemma,
      LexiconEntryKind entryKind,
      LexiconHintAction finalAction,
      String finalGloss,
      int priority) {
    this(
        entryId,
        senseId,
        lexiconVersion,
        languageTag,
        normalizedForm,
        canonicalLemma,
        entryKind,
        finalAction,
        finalGloss,
        priority,
        null);
  }
}
