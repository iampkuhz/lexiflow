package io.lexiflow.enrichment.application.caption;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import java.util.List;
import java.util.Objects;

/** 将字幕组级 UTF-16 半开范围映回片段局部坐标。 */
final class IncrementalHintMapper {
  /**
   * 将规划区间内选中的提示转换为片段 key 定位结果。
   *
   * @param request 当前请求及其片段快照。
   * @param interval 单个新增区间的组内位置规划。
   * @param annotations 区间内已排序且不重叠的提示。
   * @return 定位到片段 key 与局部半开坐标的提示列表。
   */
  List<IncrementalHintResult.Hint> map(
      CaptionIncrementalRequest request,
      IncrementalCaptionPlan.Interval interval,
      List<AnnotationHint> annotations) {
    Objects.requireNonNull(request, "request");
    Objects.requireNonNull(interval, "interval");
    Objects.requireNonNull(annotations, "annotations");
    var segments = request.current().captions().get(interval.groupIndex()).segments();
    var starts = new int[segments.size()];
    int at = 0;
    for (int i = 0; i < segments.size(); i++) {
      starts[i] = at;
      at += segments.get(i).text().length();
    }
    return annotations.stream()
        .map(
            hint -> {
              if (hint.startOffset() < interval.contextStart()
                  || hint.startOffset() >= interval.appendEndOffset()
                  || hint.endOffset() <= interval.appendStart()
                  || hint.endOffset() > interval.appendEndOffset()) {
                throw new IllegalArgumentException(
                    "hint range is outside the planned append interval");
              }
              int first =
                  startSegment(
                      segments,
                      starts,
                      interval.contextFirst(),
                      interval.appendEnd(),
                      hint.startOffset());
              int last =
                  endSegment(
                      segments,
                      starts,
                      interval.contextFirst(),
                      interval.appendEnd(),
                      hint.endOffset());
              if (last < interval.appendFirst()) {
                throw new IllegalArgumentException("hint must end within appended range");
              }
              return new IncrementalHintResult.Hint(
                  segments.get(first).key(),
                  hint.startOffset() - starts[first],
                  segments.get(last).key(),
                  hint.endOffset() - starts[last],
                  hint.chineseGloss(),
                  hint.lexiconEntryId(),
                  hint.lexiconVersion(),
                  hint.senseId());
            })
        .toList();
  }

  private static int startSegment(
      List<CaptionIncrementalRequest.Segment> segments,
      int[] starts,
      int first,
      int end,
      int offset) {
    for (int i = first; i < end; i++)
      if (offset < starts[i] + segments.get(i).text().length()) return i;
    throw new IllegalArgumentException("hint start is outside append interval");
  }

  private static int endSegment(
      List<CaptionIncrementalRequest.Segment> segments,
      int[] starts,
      int first,
      int end,
      int offset) {
    for (int i = first; i < end; i++)
      if (offset <= starts[i] + segments.get(i).text().length()) return i;
    throw new IllegalArgumentException("hint end is outside append interval");
  }
}
