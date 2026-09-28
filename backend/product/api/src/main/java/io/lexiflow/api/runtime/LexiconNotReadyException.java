package io.lexiflow.api.runtime;

/** 固定的词库不可用信号，不携带数据库或请求内容。 */
public final class LexiconNotReadyException extends RuntimeException {
  private static final long serialVersionUID = 1L;
  private final LexiconRuntime.Reason reason;

  /**
   * 建立固定、无原始 cause 的不可用异常。
   *
   * @param reason 抛出时不可变的 Lexicon 运行原因。
   */
  public LexiconNotReadyException(LexiconRuntime.Reason reason) {
    super("lexicon not ready");
    this.reason = java.util.Objects.requireNonNull(reason);
  }

  /**
   * 返回抛出瞬间复制的固定状态原因。
   *
   * @return 不依赖后续共享状态变化的运行原因。
   */
  public LexiconRuntime.Reason reason() {
    return reason;
  }
}
