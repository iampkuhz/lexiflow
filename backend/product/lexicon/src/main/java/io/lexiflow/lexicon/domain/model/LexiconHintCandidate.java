package io.lexiflow.lexicon.domain.model;

import java.util.Objects;
import java.util.UUID;

/**
 * 从已准备查询投影取得的一种精确词形；观看不再读取来源准备记录。
 *
 * @param entryId 含义：来源无关的规范词条身份。取值范围：正 long。
 * @param senseId 含义：可展示提示对应的义项身份。取值范围：提示时非空，阻断时为空。
 * @param lexiconVersion 含义：完整资料发布版本。取值范围：正 long。
 * @param normalizedForm 含义：字幕可直接反查的准确词形。取值范围：非空且已规范化。
 * @param finalAction 含义：冻结的发布决定。取值范围：提示或阻断。
 * @param finalGloss 含义：可展示时的安全短释。取值范围：提示时非空，阻断时为空。
 * @param priority 含义：非个人化提示与预热共用优先级。取值范围：0 至 1000，零不代表阻断。
 * @param frequencyRank 含义：已发布资料的连续展示排名。取值范围：可空；存在时为正数。
 * @param bncRank 含义：原始 BNC 过滤排名。取值范围：可空；存在时为正数。
 */
public record LexiconHintCandidate(
    long entryId,
    UUID senseId,
    long lexiconVersion,
    String normalizedForm,
    LexiconHintAction finalAction,
    String finalGloss,
    int priority,
    Long frequencyRank,
    Long bncRank) {

  /** 校验来源无关身份、准确词形和可靠提示与阻断互斥。 */
  public LexiconHintCandidate {
    if (entryId < 1) throw new IllegalArgumentException("entryId must be positive");
    if (lexiconVersion < 1) throw new IllegalArgumentException("candidate version is invalid");
    normalizedForm = LexiconEntry.normalizeEnglishForm(normalizedForm, "normalizedForm");
    Objects.requireNonNull(finalAction, "finalAction");
    if (priority < 0 || priority > 1000) {
      throw new IllegalArgumentException("candidate priority is invalid");
    }
    if (frequencyRank != null && frequencyRank < 1)
      throw new IllegalArgumentException("frequencyRank must be positive when present");
    if (bncRank != null && bncRank < 1)
      throw new IllegalArgumentException("bncRank must be positive when present");
    if (finalAction == LexiconHintAction.HINT) {
      Objects.requireNonNull(senseId, "senseId");
      if (finalGloss == null || finalGloss.isBlank()) {
        throw new IllegalArgumentException("HINT requires a final gloss");
      }
    } else if (senseId != null || finalGloss != null) {
      throw new IllegalArgumentException("BLOCK cannot expose a display sense");
    }
  }

  public LexiconHintCandidate(long entryId, UUID senseId, long lexiconVersion,
      String normalizedForm, LexiconHintAction finalAction, String finalGloss, int priority,
      Long frequencyRank) {
    this(entryId, senseId, lexiconVersion, normalizedForm, finalAction, finalGloss, priority,
        frequencyRank, null);
  }

  /** 创建不含来源词频排名的提示候选。 */
  public LexiconHintCandidate(
      long entryId,
      UUID senseId,
      long lexiconVersion,
      String normalizedForm,
      LexiconHintAction finalAction,
      String finalGloss,
      int priority) {
    this(
        entryId,
        senseId,
        lexiconVersion,
        normalizedForm,
        finalAction,
        finalGloss,
        priority,
        null,
        null);
  }
}
