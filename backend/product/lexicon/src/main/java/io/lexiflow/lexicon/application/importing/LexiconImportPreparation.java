package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import java.io.IOException;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.PriorityQueue;

/** 无持久化依赖的流式导入预检与有界预热预览。 */
public final class LexiconImportPreparation {
  /**
   * 统计完整扫描中观察到的来源行与可发布候选数。
   *
   * @param sourceRowsTotal 含义：来源凭据中的原始行数。取值范围：非负整数。
   * @param entries 含义：实际准备的可导入行数。取值范围：非负且不大于原始行数。
   */
  public record Counts(long sourceRowsTotal, long entries) {
    /** 校验扫描计数。 */
    public Counts {
      if (sourceRowsTotal < 0 || entries < 0 || entries > sourceRowsTotal)
        throw new IllegalArgumentException("invalid preparation counts");
    }
  }

  /**
   * 不可变预检结果；预热词条数不超过调用方预算。
   *
   * @param counts 含义：真实来源和准备条目计数。取值范围：非 null。
   * @param prewarmEntries 含义：按优先级排序的有界预览。取值范围：非 null、允许空且防御性复制。
   */
  public record Inspection(Counts counts, List<LexiconImportPlan.PlannedEntry> prewarmEntries) {
    /** 防御性复制预热预览。 */
    public Inspection {
      Objects.requireNonNull(counts, "counts");
      prewarmEntries = List.copyOf(Objects.requireNonNull(prewarmEntries, "prewarmEntries"));
    }

    /**
     * 在打开发布资源之前确认资料可作为完整版本发布。
     *
     * @throws IllegalArgumentException 预检没有任何可导入条目。
     */
    public void requirePublishable() {
      if (counts.entries() == 0) {
        throw new IllegalArgumentException("source contains no importable entries");
      }
    }
  }

  private LexiconImportPreparation() {}

  /**
   * 流式准备并核对来源身份、原始计数、可导入计数及跨行 canonical 冲突。
   *
   * @param metadata 含义：预检来源身份和策略元数据。取值范围：非 null。
   * @param rowSource 含义：可重读解析行来源。取值范围：非 null。
   * @param prewarmLimit 含义：预览上限。取值范围：非负整数，0 表示只校验。
   * @return 只含计数和有界预热预览的不可变结果
   */
  public static Inspection inspect(
      LexiconImportMetadata metadata, LexiconImportRowSource rowSource, int prewarmLimit) {
    Objects.requireNonNull(metadata, "metadata");
    Objects.requireNonNull(rowSource, "rowSource");
    if (prewarmLimit < 0) throw new IllegalArgumentException("prewarmLimit must be non-negative");
    var comparator =
        Comparator.comparingInt(
                (LexiconImportPlan.PlannedEntry value) -> value.row().priority().memoryPriority())
            .thenComparing(value -> value.entry().lemma(), Comparator.reverseOrder());
    var top =
        new PriorityQueue<LexiconImportPlan.PlannedEntry>(Math.max(1, prewarmLimit), comparator);
    var state = new ScanState(metadata, prewarmLimit, top, comparator);
    try {
      var receipt = rowSource.read(state::accept);
      if (receipt == null
          || !metadata.sourceDigest().equals(receipt.sourceDigest())
          || receipt.sourceRowsTotal() < state.entries) {
        throw new IllegalStateException("source receipt or counts mismatch");
      }
      state.receiptRows = receipt.sourceRowsTotal();
    } catch (IOException exception) {
      throw new IllegalStateException("source preparation failed", exception);
    }
    return new Inspection(
        new Counts(state.receiptRows, state.entries),
        state.top.stream()
            .sorted(
                Comparator.comparingInt(
                        (LexiconImportPlan.PlannedEntry value) ->
                            value.row().priority().memoryPriority())
                    .reversed()
                    .thenComparing(value -> value.entry().lemma()))
            .toList());
  }

  /** 应用层单次扫描状态，保留全扫描 canonical 所有权及有界候选。 */
  static final class ScanState {
    private final LexiconImportMetadata metadata;
    private final int limit;
    private final PriorityQueue<LexiconImportPlan.PlannedEntry> top;
    private final Comparator<LexiconImportPlan.PlannedEntry> comparator;
    private final LexiconImportPlan.CanonicalSurfaceValidator surfaces =
        LexiconImportPlan.canonicalSurfaceValidator();
    private long receiptRows;
    private long entries;

    ScanState(
        LexiconImportMetadata metadata,
        int limit,
        PriorityQueue<LexiconImportPlan.PlannedEntry> top,
        Comparator<LexiconImportPlan.PlannedEntry> comparator) {
      this.metadata = metadata;
      this.limit = limit;
      this.top = top;
      this.comparator = comparator;
    }

    void accept(LexiconImportRow row) {
      Objects.requireNonNull(row, "source row");
      var planned =
          LexiconImportPlan.prepareNext(
              row, 1, metadata.sourceDigest(), metadata.acquiredAt(), surfaces);
      entries++;
      if (limit == 0 || !row.prewarmEligible() || row.priority().memoryPriority() <= 0) return;
      if (top.size() < limit) top.add(planned);
      else if (comparator.compare(planned, top.peek()) > 0) {
        top.remove();
        top.add(planned);
      }
    }
  }
}
