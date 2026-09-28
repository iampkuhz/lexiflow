package io.lexiflow.enrichment.application.caption;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** 为单次请求规划连续新增区间与有界紧邻上下文。 */
final class IncrementalCaptionPlan {
  /**
   * 单个字幕组内的新增区间，以及查询起点和新增终点。
   *
   * @param groupIndex 所属字幕组索引。
   * @param text 字幕组完整文字，用于边界定位。
   * @param contextFirst 查询上下文首片段索引。
   * @param appendFirst 连续新增区间首片段索引。
   * @param appendEnd 连续新增区间末尾后一片段索引。
   * @param contextStart 上下文 UTF-16 起点。
   * @param appendStart 新增区间 UTF-16 起点。
   * @param appendEndOffset 新增区间 UTF-16 半开终点。
   */
  record Interval(
      int groupIndex,
      String text,
      int contextFirst,
      int appendFirst,
      int appendEnd,
      int contextStart,
      int appendStart,
      int appendEndOffset) {}

  /**
   * 按显示顺序生成新增区间，旧片段只完整纳入而不截断。
   *
   * @param request 已通过校验的双快照增量请求。
   * @return 本次请求内按字幕组和片段顺序排列的区间。
   */
  List<Interval> plan(CaptionIncrementalRequest request) {
    Objects.requireNonNull(request, "request");
    var result = new ArrayList<Interval>();
    var groups = request.current().captions();
    for (int g = 0; g < groups.size(); g++) {
      var segments = groups.get(g).segments();
      var text = new StringBuilder();
      var starts = new ArrayList<Integer>();
      for (var segment : segments) {
        starts.add(text.length());
        text.append(segment.text());
      }
      for (int i = 0; i < segments.size(); ) {
        if (!segments.get(i).append()) {
          i++;
          continue;
        }
        int first = i;
        long line = segments.get(i).line();
        while (i < segments.size() && segments.get(i).append() && segments.get(i).line() == line)
          i++;
        int context = first, budget = 0;
        while (context > 0
            && first - context < 2
            && !segments.get(context - 1).append()
            && segments.get(context - 1).line() == line
            && budget + segments.get(context - 1).text().length() <= 48) {
          context--;
          budget += segments.get(context).text().length();
        }
        result.add(
            new Interval(
                g,
                text.toString(),
                context,
                first,
                i,
                starts.get(context),
                starts.get(first),
                starts.get(i - 1) + segments.get(i - 1).text().length()));
      }
    }
    return List.copyOf(result);
  }
}
