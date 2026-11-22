package io.lexiflow.enrichment.domain.policy;

import io.lexiflow.enrichment.domain.model.AnnotationHint;
import io.lexiflow.enrichment.domain.model.HintSelectionResult;
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
      Comparator.comparingInt(CandidateMatch::priority)
          .reversed()
          .thenComparing(Comparator.comparingInt(CandidateMatch::length).reversed())
          .thenComparingInt(CandidateMatch::startOffset)
          .thenComparingInt(CandidateMatch::endOffset)
          .thenComparing(CandidateMatch::entryId)
          .thenComparingLong(CandidateMatch::lexiconVersion)
          .thenComparing(
              CandidateMatch::chineseGloss, Comparator.nullsFirst(Comparator.naturalOrder()));

  static HintSelectionResult select(List<CandidateMatch> matches, int commonWordCutoff) {
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
    int overlapDropped = 0;
    var eligible =
        matches.stream()
            .filter(CandidateMatch::isDisplayable)
            .filter(match -> !ambiguous.contains(new Range(match.startOffset(), match.endOffset())))
            .filter(
                match ->
                    match.phrase()
                        || match.frequencyRank() == null
                        || match.frequencyRank() > commonWordCutoff)
            .sorted(PRIORITY)
            .toList();
    for (var match : containmentOrder(eligible)) {
      if (selected.stream().anyMatch(existing -> overlaps(existing, match))) overlapDropped++;
      else {
        selected.add(match);
      }
    }
    var hints =
        selected.stream()
            .sorted(Comparator.comparingInt(CandidateMatch::startOffset))
            .map(
                match ->
                    new AnnotationHint(
                        match.startOffset(),
                        match.endOffset(),
                        match.entryId(),
                        match.senseId(),
                        match.lexiconVersion(),
                        match.chineseGloss(),
                        match.lemma(),
                        match.displayFrequencyRank()))
            .toList();
    return new HintSelectionResult(hints, ambiguous.size(), overlapDropped);
  }

  // 只为完整短语 -> 其内部单词建边。交叉匹配与短语间仍按原 priority 竞争，
  // 失败短语不会提前删除内部单词；发布的排名和分数均不变。
  private static List<CandidateMatch> containmentOrder(List<CandidateMatch> eligible) {
    int size = eligible.size();
    int[] pending = new int[size];
    var edges = new ArrayList<List<Integer>>(size);
    for (int i = 0; i < size; i++) edges.add(new ArrayList<>());
    for (int i = 0; i < size; i++) {
      var phrase = eligible.get(i);
      if (!phrase.phrase()) continue;
      for (int j = 0; j < size; j++) {
        var word = eligible.get(j);
        if (!word.phrase()
            && phrase.startOffset() <= word.startOffset()
            && phrase.endOffset() >= word.endOffset()) {
          edges.get(i).add(j);
          pending[j]++;
        }
      }
    }
    var ready =
        new java.util.PriorityQueue<Integer>(
            (left, right) -> PRIORITY.compare(eligible.get(left), eligible.get(right)));
    for (int i = 0; i < size; i++) if (pending[i] == 0) ready.add(i);
    var ordered = new ArrayList<CandidateMatch>(size);
    while (!ready.isEmpty()) {
      int index = ready.remove();
      ordered.add(eligible.get(index));
      for (int next : edges.get(index)) if (--pending[next] == 0) ready.add(next);
    }
    return ordered;
  }

  private static boolean overlaps(CandidateMatch left, CandidateMatch right) {
    return left.startOffset() < right.endOffset() && right.startOffset() < left.endOffset();
  }

  /**
   * 用于识别相同表面位置竞争词条的范围键。
   *
   * @param startOffset 含义：匹配起点。取值范围：非负 UTF-16 偏移。
   * @param endOffset 含义：匹配半开终点。取值范围：大于 startOffset 的偏移。
   */
  private record Range(int startOffset, int endOffset) {}

  /**
   * 保留不可展示候选，使其仍参与同形歧义判断。
   *
   * @param startOffset 含义：匹配起点。取值范围：非负 UTF-16 偏移。
   * @param endOffset 含义：匹配半开终点。取值范围：大于 startOffset 的偏移。
   * @param entryId 含义：词条身份。取值范围：非空字符串。
   * @param senseId 含义：通过资格检查后的义项身份。取值范围：不可展示时为空。
   * @param lexiconVersion 含义：候选所属词库版本。取值范围：正 long。
   * @param chineseGloss 含义：安全的中文短释。取值范围：不可展示时为空。
   * @param priority 含义：发布资料中的统一优先级。取值范围：非负整数。
   * @param lemma 含义：匹配词条的真实规范主词形。取值范围：非空字符串。
   * @param frequencyRank 含义：原始 BNC 排名。取值范围：可空；存在时为正数。
   * @param phrase 含义：候选是否为短语。取值范围：true 或 false。
   */
  static record CandidateMatch(
      int startOffset,
      int endOffset,
      String entryId,
      String senseId,
      long lexiconVersion,
      String chineseGloss,
      int priority,
      String lemma,
      Long frequencyRank,
      boolean phrase,
      Long displayFrequencyRank) {
    int length() {
      return endOffset - startOffset;
    }

    boolean isDisplayable() {
      return senseId != null && chineseGloss != null;
    }
  }
}
