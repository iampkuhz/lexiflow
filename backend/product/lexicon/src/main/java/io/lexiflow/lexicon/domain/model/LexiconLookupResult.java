package io.lexiflow.lexicon.domain.model;

import java.util.List;
import java.util.Objects;
import java.util.OptionalLong;

/**
 * 单次准确词形查询的候选、发布身份和局部计数。
 *
 * @param candidates 含义：本次命中的完整候选集合。取值范围：非空引用且会复制为不可变列表。
 * @param publishedVersion 含义：本次查询绑定的发布版本。取值范围：非空引用；已知值非负，未知为空。
 * @param counts 含义：本次查询的局部计数。取值范围：非空且各值非负。
 */
public record LexiconLookupResult(
    List<LexiconHintCandidate> candidates, OptionalLong publishedVersion, Counts counts) {
  /**
   * 本次请求的非负查询计数。
   *
   * @param queryKeys 含义：去重后的输入键数。取值范围：非负。
   * @param positiveHits 含义：缓存中含 HINT 候选的命中键数。取值范围：非负。
   * @param negativeHits 含义：缓存中无候选或仅 BLOCK 的命中键数。取值范围：非负。
   * @param cacheMisses 含义：本次未命中缓存的键数。取值范围：非负。
   * @param dbBatches 含义：本次 findByForms 批量调用数。取值范围：非负。
   * @param versionReads 含义：本次发布版本读取数。取值范围：非负。
   * @param prewarmReads 含义：本次版本切换时预热查询调用数。取值范围：非负。
   */
  public record Counts(
      int queryKeys,
      int positiveHits,
      int negativeHits,
      int cacheMisses,
      int dbBatches,
      int versionReads,
      int prewarmReads) {
    /** 校验非负计数与去重键守恒，使用宽整数避免溢出绕过。 */
    public Counts {
      if (queryKeys < 0
          || positiveHits < 0
          || negativeHits < 0
          || cacheMisses < 0
          || dbBatches < 0
          || versionReads < 0
          || prewarmReads < 0
          || (long) positiveHits + negativeHits + cacheMisses != queryKeys) {
        throw new IllegalArgumentException("lookup counts are invalid");
      }
    }

    /**
     * 创建全部字段为零的计数。
     *
     * @return 七项均为零的不可变计数。
     */
    public static Counts zero() {
      return new Counts(0, 0, 0, 0, 0, 0, 0);
    }

    /**
     * 按字段累加两次查询计数，任何整数溢出都会拒绝。
     *
     * @param other 含义：待累加计数。取值范围：非空。
     * @return 各字段分别相加的新计数。
     */
    public Counts plus(Counts other) {
      Objects.requireNonNull(other, "other");
      return new Counts(
          Math.addExact(queryKeys, other.queryKeys),
          Math.addExact(positiveHits, other.positiveHits),
          Math.addExact(negativeHits, other.negativeHits),
          Math.addExact(cacheMisses, other.cacheMisses),
          Math.addExact(dbBatches, other.dbBatches),
          Math.addExact(versionReads, other.versionReads),
          Math.addExact(prewarmReads, other.prewarmReads));
    }
  }

  /** 冻结候选列表并校验发布身份和本次计数。 */
  public LexiconLookupResult {
    candidates = List.copyOf(Objects.requireNonNull(candidates, "candidates"));
    Objects.requireNonNull(publishedVersion, "publishedVersion");
    Objects.requireNonNull(counts, "counts");
    if (publishedVersion.isPresent() && publishedVersion.getAsLong() < 0) {
      throw new IllegalArgumentException("published version must be nonnegative");
    }
  }
}
