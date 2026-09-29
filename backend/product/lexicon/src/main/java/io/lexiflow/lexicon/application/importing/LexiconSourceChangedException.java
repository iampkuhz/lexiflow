package io.lexiflow.lexicon.application.importing;

/** 表示发布重读来源不再匹配预检凭据。 异常文本固定，不包含来源路径或用户数据。 */
public final class LexiconSourceChangedException extends IllegalStateException {
  private static final long serialVersionUID = 1L;

  /** 创建固定文本的来源身份变化异常。 */
  public LexiconSourceChangedException() {
    super("source changed after preflight");
  }
}
