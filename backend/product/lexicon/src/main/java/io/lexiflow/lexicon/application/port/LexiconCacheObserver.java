package io.lexiflow.lexicon.application.port;

/** 报告不可变缓存失效测量值，不暴露缓存键。 观察器仅用于结构化计数，不改变查询行为。 */
@FunctionalInterface
public interface LexiconCacheObserver {
  /**
   * 在既有版本缓存清理并重新绑定后、预热前回调。
   *
   * @param oldVersion 含义：清理前绑定版本。取值范围：非负整数。
   * @param newVersion 含义：清理后绑定版本。取值范围：非负整数。
   * @param positiveKeys 含义：清理前正向键数。取值范围：非负整数。
   * @param negativeKeys 含义：清理前负向键数。取值范围：非负整数。
   * @param durationNanos 含义：缓存清理耗时，不含预热。取值范围：非负纳秒数。
   */
  void versionChanged(
      long oldVersion, long newVersion, int positiveKeys, int negativeKeys, long durationNanos);

  LexiconCacheObserver NONE =
      (oldVersion, newVersion, positiveKeys, negativeKeys, durationNanos) -> {};
}
