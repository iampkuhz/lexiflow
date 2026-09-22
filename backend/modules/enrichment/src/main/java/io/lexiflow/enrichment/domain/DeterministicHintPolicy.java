package io.lexiflow.enrichment.domain;

import io.lexiflow.lexicon.domain.LexiconEntry;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

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
    var result = new ArrayList<AnnotationHint>();
    for (var candidate : candidates) {
      var hint = locate(context, candidate);
      if (hint != null) {
        result.add(hint);
      }
    }
    if (result.isEmpty()) {
      return new CaptionHintResult(context.caption(), HintState.NO_PENDING, List.of());
    }
    return new CaptionHintResult(context.caption(), HintState.READY, result);
  }

  private static AnnotationHint locate(CaptionContext context, LexiconEntry candidate) {
    var surfaces = new ArrayList<String>();
    surfaces.add(candidate.term());
    candidate.aliases().forEach(alias -> surfaces.add(alias.normalizedForm()));
    candidate.inflections().forEach(inflection -> surfaces.add(inflection.normalizedForm()));
    surfaces.sort((left, right) -> Integer.compare(right.length(), left.length()));
    for (var surface : surfaces) {
      var matches =
          Pattern.compile(Pattern.quote(surface), Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE)
              .matcher(context.caption())
              .region(context.startOffset(), context.endOffset());
      while (matches.find()) {
        if (isWordBoundary(context.caption(), matches.start(), matches.end() - matches.start())) {
          return new AnnotationHint(
              matches.start(),
              matches.end(),
              candidate.entryId().toString(),
              candidate.lexiconVersion(),
              candidate.chineseGloss());
        }
      }
    }
    return null;
  }

  private static boolean isWordBoundary(String text, int offset, int length) {
    var before = offset == 0 || !Character.isLetterOrDigit(text.charAt(offset - 1));
    var end = offset + length;
    var after = end == text.length() || !Character.isLetterOrDigit(text.charAt(end));
    return before && after;
  }
}
