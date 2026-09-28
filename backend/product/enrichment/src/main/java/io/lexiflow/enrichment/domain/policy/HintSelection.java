package io.lexiflow.enrichment.domain.policy;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 拥有确定性的歧义识别、优先排序、去重和重叠选择。 */
final class HintSelection {
  private static final Comparator<CandidateMatch> PRIORITY =
      Comparator.comparingInt(CandidateMatch::valueTier)
          .reversed()
          .thenComparing(Comparator.comparingInt(CandidateMatch::finalPriority).reversed())
          .thenComparing(Comparator.comparingInt(CandidateMatch::complexListCount).reversed())
          .thenComparing(Comparator.comparingInt(CandidateMatch::length).reversed())
          .thenComparingInt(CandidateMatch::startOffset)
          .thenComparingInt(CandidateMatch::endOffset)
          .thenComparing(CandidateMatch::entryId)
          .thenComparingLong(CandidateMatch::lexiconVersion)
          .thenComparing(
              CandidateMatch::chineseGloss, Comparator.nullsFirst(Comparator.naturalOrder()));

  /** 按既有优先顺序选择无歧义且可展示的匹配项。 */
  static List<AnnotationHint> select(List<CandidateMatch> matches) {
    var idsByRange = new HashMap<Range, Set<String>>();
    for (var match : matches)
      idsByRange
          .computeIfAbsent(
              new Range(match.startOffset(), match.endOffset()), ignored -> new HashSet<>())
          .add(match.entryId());
    var ambiguous =
        idsByRange.entrySet().stream()
            .filter(entry -> entry.getValue().size() > 1)
            .map(Map.Entry::getKey)
            .collect(java.util.stream.Collectors.toUnmodifiableSet());
    var selected = new ArrayList<CandidateMatch>();
    var ids = new HashSet<String>();
    matches.stream()
        .filter(CandidateMatch::isDisplayable)
        .filter(match -> !ambiguous.contains(new Range(match.startOffset(), match.endOffset())))
        .sorted(PRIORITY)
        .forEach(
            match -> {
              if (!ids.contains(match.entryId())
                  && selected.stream().noneMatch(existing -> overlaps(existing, match))) {
                ids.add(match.entryId());
                selected.add(match);
              }
            });
    return selected.stream()
        .sorted(Comparator.comparingInt(CandidateMatch::startOffset))
        .map(
            match ->
                new AnnotationHint(
                    match.startOffset(),
                    match.endOffset(),
                    match.entryId(),
                    match.senseId(),
                    match.lexiconVersion(),
                    match.chineseGloss()))
        .toList();
  }

  private static boolean overlaps(CandidateMatch left, CandidateMatch right) {
    return left.startOffset() < right.endOffset() && right.startOffset() < left.endOffset();
  }

  /**
   * 用于识别相同表面位置竞争词条的范围键。
   *
   * @param startOffset 范围起点。
   * @param endOffset 范围半开终点。
   */
  private record Range(int startOffset, int endOffset) {}

  /**
   * 保留不可展示候选，使其仍参与同形歧义判断。
   *
   * @param startOffset 匹配起点。
   * @param endOffset 匹配半开终点。
   * @param entryId 词条身份。
   * @param senseId 通过资格检查后的义项身份；不可展示时为空。
   * @param lexiconVersion 候选所属词库版本。
   * @param chineseGloss 安全的中文短释；不可展示时为空。
   * @param valueTier 冻结的候选优先级层级。
   * @param finalPriority 发布资料中的最终优先级。
   * @param complexListCount 独立复杂词表证据数量。
   */
  static record CandidateMatch(
      int startOffset,
      int endOffset,
      String entryId,
      String senseId,
      long lexiconVersion,
      String chineseGloss,
      int valueTier,
      int finalPriority,
      int complexListCount) {
    int length() {
      return endOffset - startOffset;
    }

    boolean isDisplayable() {
      return senseId != null && chineseGloss != null;
    }
  }
}
