package io.lexiflow.enrichment.domain.policy;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.CaptionHintResult;
import io.lexiflow.enrichment.domain.model.HintSelectionResult;
import io.lexiflow.enrichment.domain.model.HintState;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** 根据公开词汇材料生成确定性提示，绝不调用模型或建立伪造的 pending 工作。 */
public final class DeterministicHintPolicy {
  /**
   * 在字幕目标范围内定位词汇候选，并返回可直接显示的中文提示。
   *
   * @param context 含义：已验证的字幕上下文。取值范围：非空，区间必须在字幕文本内。
   * @param candidates 含义：Lexicon 提供的版本化候选。取值范围：非空，可为空集合。
   * @return 有提示时为 READY，否则为 NO_PENDING。
   */
  public CaptionHintResult evaluate(CaptionContext context, List<LexiconHintCandidate> candidates) {
    Objects.requireNonNull(context, "context");
    var selected =
        evaluate(context.caption(), context.startOffset(), context.endOffset(), candidates);
    return new CaptionHintResult(
        context.caption(), selected.isEmpty() ? HintState.NO_PENDING : HintState.READY, selected);
  }

  /**
   * 在完整 caption 组的指定新增区间定位，词边界仍取完整组文字。
   *
   * @param caption 含义：当前字幕组的完整文字。取值范围：非空对象，可为空字符串。
   * @param startOffset 含义：新增区间的 UTF-16 起点。取值范围：零至 endOffset，包含该位置。
   * @param endOffset 含义：新增区间的 UTF-16 终点。取值范围：startOffset 至文字长度，不包含该位置。
   * @param candidates 含义：新增区间查询到的已发布候选。取值范围：非空列表，可为空集合。
   * @return 完全位于新增区间内的非重叠提示；歧义或无命中时不补造提示。
   */
  public List<AnnotationHint> evaluate(
      String caption, int startOffset, int endOffset, List<LexiconHintCandidate> candidates) {
    return evaluate(caption, startOffset, endOffset, startOffset, candidates);
  }

  /**
   * 允许有界旧上下文补全新词组，但不重新选择完全位于已处理区间内的提示。
   *
   * @param caption 含义：当前字幕组的完整文字。取值范围：非空对象，可为空字符串。
   * @param startOffset 含义：查询上下文的 UTF-16 起点。取值范围：零至 endOffset。
   * @param endOffset 含义：新增区间的 UTF-16 终点。取值范围：startOffset 至文字长度。
   * @param requiredEndAfter 含义：新增区间的 UTF-16 起点；提示终点必须越过此处。取值范围：startOffset 至 endOffset。
   * @param candidates 含义：上下文查询到的已发布候选。取值范围：非空列表，可为空集合。
   * @return 延伸到新增片段的非重叠提示；歧义或无命中时为空。
   */
  public List<AnnotationHint> evaluate(
      String caption,
      int startOffset,
      int endOffset,
      int requiredEndAfter,
      List<LexiconHintCandidate> candidates) {
    return evaluateSelection(caption, startOffset, endOffset, requiredEndAfter, candidates).hints();
  }

  /**
   * 在新增范围选择提示并返回真实歧义及重叠淘汰计数。
   *
   * @param caption 含义：完整字幕文字。取值范围：非空，偏移使用 UTF-16 半开范围。
   * @param startOffset 含义：查询上下文起点。取值范围：零至 endOffset。
   * @param endOffset 含义：查询上下文终点。取值范围：不超过文字长度。
   * @param requiredEndAfter 含义：新增范围起点。取值范围：提示终点必须严格越过此偏移。
   * @param candidates 含义：已发布词库候选。取值范围：非空列表，可为空集合。
   * @return 选择结果及基于输入候选计算的计数。
   */
  public HintSelectionResult evaluateSelection(
      String caption,
      int startOffset,
      int endOffset,
      int requiredEndAfter,
      List<LexiconHintCandidate> candidates) {
    Objects.requireNonNull(caption, "caption");
    Objects.requireNonNull(candidates, "candidates");
    if (startOffset < 0
        || endOffset < startOffset
        || endOffset > caption.length()
        || requiredEndAfter < startOffset
        || requiredEndAfter > endOffset) {
      throw new IllegalArgumentException("caption range is invalid");
    }
    if (candidates.stream().anyMatch(Objects::isNull)) {
      return new HintSelectionResult(List.of(), 0, 0);
    }
    if (candidates.stream().map(LexiconHintCandidate::lexiconVersion).distinct().limit(2).count()
        > 1) {
      return new HintSelectionResult(List.of(), 0, 0);
    }

    var matches = new ArrayList<HintSelection.CandidateMatch>();
    for (var candidate : candidates) {
      for (var match : locate(caption, startOffset, endOffset, candidate)) {
        if (match.endOffset() > requiredEndAfter) matches.add(match);
      }
    }
    return HintSelection.select(matches);
  }

  private static List<HintSelection.CandidateMatch> locate(
      String caption, int startOffset, int endOffset, LexiconHintCandidate candidate) {
    var matches = new ArrayList<HintSelection.CandidateMatch>();
    var displayable = PublishedCandidateEligibility.isDisplayable(candidate);
    var qualified = displayable ? candidate.finalGloss() : null;
    var valueTier = candidate.rankedWord() ? 1 : 0;
    for (var occurrence :
        CandidateMatcher.locate(caption, startOffset, endOffset, candidate.normalizedForm())) {
      matches.add(
          new HintSelection.CandidateMatch(
              occurrence.startOffset(),
              occurrence.endOffset(),
              Long.toString(candidate.entryId()),
              qualified == null ? null : candidate.senseId().toString(),
              candidate.lexiconVersion(),
              qualified,
              valueTier,
              candidate.finalPriority(),
              candidate.complexListCount()));
    }
    return matches;
  }
}
