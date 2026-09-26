package io.lexiflow.lexicon.domain.model;

import java.util.Objects;
import java.util.UUID;

/**
 * 从已准备查询投影取得的一种精确词形；观看不再读取来源准备记录。
 *
 * @param entryId 来源无关的规范词条身份。
 * @param senseId 可展示时的精确义项身份；阻断时为空。
 * @param lexiconVersion 完整资料发布版本。
 * @param languageTag 当前仅为英语。
 * @param normalizedForm 字幕可直接反查的准确词形。
 * @param canonicalLemma 所属规范主词形。
 * @param entryKind 主词条的单词或短语类别。
 * @param finalAction 冻结的提示或阻断决定。
 * @param finalGloss 可展示时的安全短释；阻断时为空。
 * @param finalPriority 非个人化提示排序分数。
 * @param frequencyZipf 来源排名换算的词频值。
 * @param complexListCount 复杂学习词表证据数量。
 */
public record LexiconHintCandidate(
    UUID entryId,
    UUID senseId,
    long lexiconVersion,
    String languageTag,
    String normalizedForm,
    String canonicalLemma,
    LexiconEntryKind entryKind,
    LexiconHintAction finalAction,
    String finalGloss,
    int finalPriority,
    double frequencyZipf,
    int complexListCount) {

  /** 校验来源无关身份、准确词形和可靠提示与阻断互斥。 */
  public LexiconHintCandidate {
    Objects.requireNonNull(entryId, "entryId");
    if (lexiconVersion < 1 || !"en".equals(languageTag)) {
      throw new IllegalArgumentException("candidate version or language is invalid");
    }
    normalizedForm = LexiconEntry.normalizeEnglishForm(normalizedForm, "normalizedForm");
    canonicalLemma = LexiconEntry.normalizeEnglishForm(canonicalLemma, "canonicalLemma");
    Objects.requireNonNull(entryKind, "entryKind");
    Objects.requireNonNull(finalAction, "finalAction");
    if (finalPriority < 0
        || finalPriority > 1000
        || !Double.isFinite(frequencyZipf)
        || frequencyZipf < 0
        || frequencyZipf > 8
        || complexListCount < 0) {
      throw new IllegalArgumentException("candidate priority evidence is invalid");
    }
    if (finalAction == LexiconHintAction.HINT) {
      Objects.requireNonNull(senseId, "senseId");
      if (finalGloss == null || finalGloss.isBlank()) {
        throw new IllegalArgumentException("HINT requires a final gloss");
      }
    } else if (senseId != null || finalGloss != null) {
      throw new IllegalArgumentException("BLOCK cannot expose a display sense");
    }
  }
}
