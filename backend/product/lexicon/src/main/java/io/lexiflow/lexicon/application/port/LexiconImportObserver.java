package io.lexiflow.lexicon.application.port;

import java.util.Map;

/** 应用层隐私安全导入生命周期观察端口。 */
public interface LexiconImportObserver {
  /** 导入中的封闭步骤。 */
  enum Step {
    SOURCE_CHECK,
    PREPARE,
    PERSIST,
    PUBLISH
  }

  /** 单步的封闭阶段。 */
  enum Phase {
    STARTED,
    HEARTBEAT,
    COMPLETED
  }

  /** 导入事件的固定原因。 */
  enum Reason {
    STARTED,
    OK,
    SOURCE_INVALID,
    SOURCE_CHANGED,
    CANCELLED,
    DEPENDENCY_UNAVAILABLE,
    INTERNAL_ERROR,
    PUBLISH_ROLLED_BACK
  }

  /** 导入事件允许携带的固定计数。 */
  enum Count {
    INPUT_ROWS,
    PREPARED_ROWS,
    HINT_ROWS,
    BLOCKED_ROWS,
    LOOKUP_ROWS
  }

  /**
   * 固定事件值；缺失的版本表示事务尚未确认提交。 计数和规则键均为不可变低基数值，不含词条正文。
   *
   * @param step 事件所属固定步骤。
   * @param phase 事件生命周期阶段。
   * @param reason 固定原因值。
   * @param durationNanos 单调时钟耗时。
   * @param counts 已测非负数字计数。
   * @param reasonCounts 固定规则键计数。
   * @param lexiconVersion 已知发布版本，未知时为空。
   * @param terminal 是否为本次发布唯一终态。
   */
  record Event(
      Step step,
      Phase phase,
      Reason reason,
      long durationNanos,
      Map<Count, Long> counts,
      Map<String, Long> reasonCounts,
      Long lexiconVersion,
      boolean terminal) {
    /** 防御性复制事件映射。 */
    public Event {
      counts = Map.copyOf(counts);
      reasonCounts = Map.copyOf(reasonCounts);
    }
  }

  /**
   * 接收一次固定生命周期事件。
   *
   * <p>观察端口异常由调用方隔离，不应改变产品流程。
   *
   * @param event 含义：不包含词条正文的不可变事件值。取值范围：非 null。
   */
  void onEvent(Event event);

  /** 不执行输出的显式观察端口。 */
  LexiconImportObserver NONE = event -> {};
}
