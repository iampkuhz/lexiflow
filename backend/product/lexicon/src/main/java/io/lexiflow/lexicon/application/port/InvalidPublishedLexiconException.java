package io.lexiflow.lexicon.application.port;

/** 已发布元数据与固定观看合同不匹配，不携带来源内容。 */
public final class InvalidPublishedLexiconException extends RuntimeException {
  private static final long serialVersionUID = 1L;

  /** 建立固定、无底层 cause 的元数据异常。 */
  public InvalidPublishedLexiconException() {
    super("published metadata is incompatible");
  }
}
