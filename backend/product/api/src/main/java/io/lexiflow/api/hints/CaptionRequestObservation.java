package io.lexiflow.api.hints;

import io.lexiflow.enrichment.application.caption.model.MeasuredIncrementalCaptionResult;
import io.lexiflow.observability.platform.StructuredEvent;
import java.util.EnumMap;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/** 单请求私有的固定原因、数字计数和阶段耗时；不保存正文、结果或异常。 */
public final class CaptionRequestObservation {
  /** 当前请求观测对象在请求属性中的固定键名。 */
  public static final String ATTRIBUTE = CaptionRequestObservation.class.getName();

  private final UUID requestId;
  private final long startedNanos;
  private final EnumMap<StructuredEvent.Count, Long> counts =
      new EnumMap<>(StructuredEvent.Count.class);
  private final EnumMap<StructuredEvent.Timing, Long> timings =
      new EnumMap<>(StructuredEvent.Timing.class);
  private StructuredEvent.Reason reason = StructuredEvent.Reason.OK;
  private Long lexiconVersion;
  private boolean versionConflict;
  private boolean completed;
  private boolean reasonSet;

  /**
   * 创建单请求隔离的观测容器。
   *
   * @param requestId 后端生成的请求 UUID。
   * @param startedNanos 请求入口的单调时钟读数。
   */
  public CaptionRequestObservation(UUID requestId, long startedNanos) {
    this.requestId = Objects.requireNonNull(requestId);
    this.startedNanos = startedNanos;
  }

  /**
   * 返回该请求的后端 UUID。
   *
   * @return 由过滤器生成且不受客户端输入影响的请求身份。
   */
  public UUID requestId() {
    return requestId;
  }

  /**
   * 记录输入转换耗时，不接受正文或异常数据。
   *
   * @param nanos 含义：单调纳秒时长。取值范围：负值按零处理。
   */
  public synchronized void validation(long nanos) {
    timings.put(StructuredEvent.Timing.VALIDATION, millis(nanos));
  }

  /**
   * 设置请求终态固定原因码。
   *
   * @param value 含义：请求处理阶段确定的枚举原因。取值范围：固定原因枚举值。
   */
  public synchronized void reason(StructuredEvent.Reason value) {
    reason = Objects.requireNonNull(value);
    reasonSet = true;
  }

  /**
   * 从结构化用例结果复制已测计数、版本及阶段耗时。
   *
   * @param result 含义：用例结果及观测值。取值范围：仅数字与已发布身份。
   */
  public synchronized void measured(MeasuredIncrementalCaptionResult result) {
    var d = result.diagnostics();
    counts.put(StructuredEvent.Count.NEW_RANGES, (long) d.newRanges());
    counts.put(StructuredEvent.Count.CANDIDATES, (long) result.candidateCount());
    counts.put(StructuredEvent.Count.SELECTED, (long) result.result().hints().size());
    counts.put(StructuredEvent.Count.AMBIGUOUS, (long) d.ambiguous());
    counts.put(StructuredEvent.Count.OVERLAP_DROPPED, (long) d.overlapDropped());
    var q = result.queryCounts();
    if (q.queryKeys() > 0) {
      counts.put(StructuredEvent.Count.QUERY_KEYS, (long) q.queryKeys());
      counts.put(StructuredEvent.Count.POSITIVE_HITS, (long) q.positiveHits());
      counts.put(StructuredEvent.Count.NEGATIVE_HITS, (long) q.negativeHits());
      counts.put(StructuredEvent.Count.CACHE_MISSES, (long) q.cacheMisses());
      counts.put(StructuredEvent.Count.DB_BATCHES, (long) q.dbBatches());
      counts.put(StructuredEvent.Count.VERSION_READS, (long) q.versionReads());
      counts.put(StructuredEvent.Count.PREWARM_READS, (long) q.prewarmReads());
    }
    if (d.publishedVersion().isPresent()) lexiconVersion = d.publishedVersion().getAsLong();
    versionConflict = d.versionConflict();
    if (versionConflict) reason(StructuredEvent.Reason.VERSION_CONFLICT);
    else if (d.newRanges() == 0) reason(StructuredEvent.Reason.NO_NEW_SEGMENTS);
    else if (result.result().hints().isEmpty()) reason(StructuredEvent.Reason.NO_HINT);
    putTiming(StructuredEvent.Timing.CANDIDATES, d.candidatesNanos());
    if (q.queryKeys() > 0) putTiming(StructuredEvent.Timing.QUERY, result.queryNanos());
    if (d.newRanges() > 0) putTiming(StructuredEvent.Timing.SELECTION, result.rulesNanos());
  }

  /**
   * 记录同步敏感记录调用时长。
   *
   * @param nanos 含义：Store 调用的单调纳秒时长。取值范围：负值按零处理。
   */
  public synchronized void analysis(long nanos) {
    putTiming(StructuredEvent.Timing.ANALYSIS, nanos);
  }

  /**
   * 返回本请求是否检测到发布版本冲突。
   *
   * @return 仅当不同查询/候选/映射版本不一致时为 true。
   */
  public synchronized boolean versionConflict() {
    return versionConflict;
  }

  /**
   * 生成唯一终态；只能由请求过滤器调用一次。
   *
   * @param finishedNanos 含义：请求出口的单调时钟读数。取值范围：与入口时钟同源。
   * @return 仅由固定原因、数字计数和阶段耗时组成的终态事件。
   */
  public synchronized StructuredEvent terminal(long finishedNanos) {
    return terminal(finishedNanos, 200);
  }

  /**
   * 生成唯一终态；若控制器未设置原因，则按最终 HTTP 状态确定固定兜底原因。
   *
   * @param finishedNanos 含义：请求出口的单调时钟读数。取值范围：与入口时钟同源。
   * @param status 含义：最终响应状态码。取值范围：Servlet HTTP 状态码。
   * @return 仅由固定原因、数字计数和阶段耗时组成的终态事件。
   */
  public synchronized StructuredEvent terminal(long finishedNanos, int status) {
    if (completed) throw new IllegalStateException("terminal already emitted");
    completed = true;
    if (!reasonSet) {
      reason =
          status >= 500
              ? StructuredEvent.Reason.INTERNAL_ERROR
              : status >= 400 ? StructuredEvent.Reason.INVALID_REQUEST : StructuredEvent.Reason.OK;
    }
    timings.put(StructuredEvent.Timing.API, millis(Math.max(0, finishedNanos - startedNanos)));
    return new StructuredEvent(
        StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
        reason,
        millis(Math.max(0, finishedNanos - startedNanos)),
        Map.copyOf(counts),
        lexiconVersion,
        requestId,
        Map.copyOf(timings),
        null,
        null,
        Map.of());
  }

  private void putTiming(StructuredEvent.Timing key, long nanos) {
    timings.put(key, millis(nanos));
  }

  private static long millis(long nanos) {
    return TimeUnit.NANOSECONDS.toMillis(Math.max(0, nanos));
  }
}
