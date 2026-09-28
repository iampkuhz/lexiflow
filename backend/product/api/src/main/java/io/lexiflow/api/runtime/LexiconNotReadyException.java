package io.lexiflow.api.runtime;

/** 固定的词库不可用信号，不携带数据库或请求内容。 */
public final class LexiconNotReadyException extends RuntimeException {
  private static final long serialVersionUID = 1L;

  /** 建立固定、无原始 cause 的不可用异常。 */
  public LexiconNotReadyException() {
    super("lexicon not ready");
  }
}
