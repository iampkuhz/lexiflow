package io.lexiflow.lexicon.application.importing;

import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import io.lexiflow.lexicon.application.port.LexiconImportObserver.Count;
import java.util.Map;
import java.util.Objects;
import java.util.function.LongSupplier;

/** 单次发布生命周期状态；观察器失败不会逸出到产品操作。 */
public final class LexiconImportObservation {
  private static final long HEARTBEAT_NANOS = 180_000_000_000L;
  private final LexiconImportObserver observer;
  private final LongSupplier clock;
  private final long[] started = new long[LexiconImportObserver.Step.values().length];
  private final boolean[] active = new boolean[started.length];
  private final boolean[] completed = new boolean[started.length];
  private long lastHeartbeat;
  private boolean terminal;
  private LexiconImportStatistics preparationStatistics;
  private long runStarted;
  private boolean runStartedSet;

  /** 使用系统单调时钟构造本次导入观察。 */
  public LexiconImportObservation(LexiconImportObserver observer) {
    this(observer, System::nanoTime);
  }

  /** 使用可控单调时钟构造本次导入观察。 */
  public LexiconImportObservation(LexiconImportObserver observer, LongSupplier clock) {
    this.observer = Objects.requireNonNull(observer);
    this.clock = Objects.requireNonNull(clock);
    this.runStarted = clock.getAsLong();
  }

  /**
   * 开始一次固定步骤；同一步不可重复启动。 重复调用不会重复写入阶段开始事件。
   *
   * @param step 含义：当前实际执行的导入步骤。取值范围：Step 枚举。
   */
  public synchronized void start(LexiconImportObserver.Step step) {
    int i = step.ordinal();
    if (terminal || active[i] || completed[i]) return;
    started[i] = clock.getAsLong();
    if (!runStartedSet) {
      runStarted = started[i];
      runStartedSet = true;
    }
    active[i] = true;
    lastHeartbeat = started[i];
    emit(
        step,
        LexiconImportObserver.Phase.STARTED,
        LexiconImportObserver.Reason.STARTED,
        Map.of(),
        Map.of(),
        null,
        false);
  }

  /**
   * 在真实工作回调中请求有界心跳。 只有活动步骤且距离上次心跳至少三分钟才会输出。
   *
   * @param step 含义：当前正在执行的导入步骤。取值范围：Step 枚举。
   */
  public synchronized void heartbeat(LexiconImportObserver.Step step) {
    long now = clock.getAsLong();
    if (terminal || !active[step.ordinal()] || now - lastHeartbeat < HEARTBEAT_NANOS) return;
    lastHeartbeat = now;
    emit(
        step,
        LexiconImportObserver.Phase.HEARTBEAT,
        LexiconImportObserver.Reason.OK,
        Map.of(),
        Map.of(),
        null,
        false);
  }

  /**
   * 完成当前步骤至多一次。 完成事件仅包含已测计数和固定规则键。
   *
   * @param step 含义：当前完成的导入步骤。取值范围：Step 枚举。
   * @param reason 含义：固定完成原因。取值范围：Reason 枚举。
   * @param counts 含义：允许的非负固定计数。取值范围：Count 键及非负值。
   * @param rules 含义：完整扫描所得规则计数。取值范围：固定规则键与非负值。
   */
  public synchronized void complete(
      LexiconImportObserver.Step step,
      LexiconImportObserver.Reason reason,
      Map<Count, Long> counts,
      Map<String, Long> rules) {
    int i = step.ordinal();
    if (terminal || !active[i] || completed[i]) return;
    completed[i] = true;
    active[i] = false;
    emit(step, LexiconImportObserver.Phase.COMPLETED, reason, counts, rules, null, false);
  }

  /**
   * 关闭活动步骤并发出唯一终态。 未知版本传入 null，失败终态不得带入预检数字。
   *
   * @param reason 含义：固定终态原因。取值范围：Reason 枚举。
   * @param counts 含义：已实际取得的终态计数。取值范围：Count 键及非负值。
   * @param rules 含义：仅成功发布时可提供的完整规则计数。取值范围：固定规则键与非负值。
   * @param version 含义：已确认提交的版本。取值范围：非负整数或 null。
   */
  public synchronized void terminal(
      LexiconImportObserver.Reason reason,
      Map<Count, Long> counts,
      Map<String, Long> rules,
      Long version) {
    if (terminal) return;
    for (var step : LexiconImportObserver.Step.values())
      if (active[step.ordinal()]) complete(step, reason, Map.of(), Map.of());
    terminal = true;
    started[LexiconImportObserver.Step.PUBLISH.ordinal()] = runStarted;
    emit(
        LexiconImportObserver.Step.PUBLISH,
        LexiconImportObserver.Phase.COMPLETED,
        reason,
        counts,
        rules,
        version,
        true);
  }

  /**
   * 记录仅供成功终态使用的完整预检数字。 失败终态不会输出这些预检数量。
   *
   * @param statistics 含义：完整准备扫描的隐私安全统计。取值范围：非 null。
   */
  public synchronized void prepared(LexiconImportStatistics statistics) {
    if (!terminal) preparationStatistics = Objects.requireNonNull(statistics);
  }

  /**
   * 记录事务内 writer 实际刷新并核对后的持久化阶段数字。 该阶段在事务中完成，不代表事务已提交。
   *
   * @param entries 含义：writer 实际准备词条数。取值范围：非负整数。
   * @param lookups 含义：writer 实际产生 lookup 行数。取值范围：非负整数。
   */
  public synchronized void persisted(long entries, long lookups) {
    var counts = new java.util.EnumMap<Count, Long>(Count.class);
    if (preparationStatistics != null) {
      counts.put(Count.INPUT_ROWS, preparationStatistics.inputRows());
      counts.put(Count.HINT_ROWS, preparationStatistics.hintRows());
      counts.put(Count.BLOCKED_ROWS, preparationStatistics.blockedRows());
    }
    counts.put(Count.PREPARED_ROWS, entries);
    complete(LexiconImportObserver.Step.PERSIST, LexiconImportObserver.Reason.OK, counts, Map.of());
  }

  /**
   * 根据提交确认后的 writer 数量输出唯一成功终态。 未得到 writer 回调的数字保持缺失，不伪造零值。
   *
   * @param entries 含义：已知时的实际提交词条数。取值范围：非负整数或 null。
   * @param lookups 含义：已知时的实际提交 lookup 行数。取值范围：非负整数或 null。
   * @param version 含义：事务成功返回的发布版本。取值范围：非负整数。
   */
  public synchronized void published(Long entries, Long lookups, long version) {
    var counts = new java.util.EnumMap<Count, Long>(Count.class);
    if (preparationStatistics != null) {
      counts.put(Count.INPUT_ROWS, preparationStatistics.inputRows());
      counts.put(Count.BLOCKED_ROWS, preparationStatistics.blockedRows());
    }
    if (entries != null) counts.put(Count.PREPARED_ROWS, entries);
    if (lookups != null) counts.put(Count.LOOKUP_ROWS, lookups);
    terminal(
        LexiconImportObserver.Reason.OK,
        counts,
        preparationStatistics == null ? Map.of() : preparationStatistics.reasonCounts(),
        version);
  }

  private void emit(
      LexiconImportObserver.Step step,
      LexiconImportObserver.Phase phase,
      LexiconImportObserver.Reason reason,
      Map<Count, Long> counts,
      Map<String, Long> rules,
      Long version,
      boolean terminalEvent) {
    try {
      observer.onEvent(
          new LexiconImportObserver.Event(
              step,
              phase,
              reason,
              Math.max(0, clock.getAsLong() - started[step.ordinal()]),
              counts,
              rules,
              version,
              terminalEvent));
    } catch (RuntimeException ignored) {
    }
  }
}
