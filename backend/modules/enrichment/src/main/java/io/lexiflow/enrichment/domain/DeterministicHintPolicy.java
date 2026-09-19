package io.lexiflow.enrichment.domain;

import io.lexiflow.lexicon.domain.LexiconEntry;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
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
  public CaptionHintResult evaluate(CaptionContext context, List<LexiconEntry> candidates) {
    Objects.requireNonNull(context, "context");
    Objects.requireNonNull(candidates, "candidates");
    var normalized = context.caption().toLowerCase(Locale.ROOT);
    var result = new ArrayList<AnnotationHint>();
    for (var candidate : candidates) {
      var offset = normalized.indexOf(candidate.term(), context.startOffset());
      if (withinContext(context, offset, candidate.term().length())
          && isWordBoundary(normalized, offset, candidate.term().length())) {
        result.add(
            new AnnotationHint(
                offset,
                offset + candidate.term().length(),
                candidate.entryId().toString(),
                candidate.lexiconVersion(),
                candidate.chineseGloss()));
      }
    }
    if (result.isEmpty()) {
      return new CaptionHintResult(context.caption(), HintState.NO_PENDING, List.of());
    }
    return new CaptionHintResult(context.caption(), HintState.READY, result);
  }

  private static boolean withinContext(CaptionContext context, int offset, int length) {
    return offset >= context.startOffset() && offset + length <= context.endOffset();
  }

  private static boolean isWordBoundary(String text, int offset, int length) {
    var before = offset == 0 || !Character.isLetterOrDigit(text.charAt(offset - 1));
    var end = offset + length;
    var after = end == text.length() || !Character.isLetterOrDigit(text.charAt(end));
    return before && after;
  }
}
