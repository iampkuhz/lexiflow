package io.lexiflow.lexicon.application.importing.model;

import java.io.IOException;
import java.util.function.Consumer;

/** 在完整预检之后重读已冻结来源，向同一发布事务逐行提供输入。 */
@FunctionalInterface
public interface LexiconImportRowSource {
  /**
   * 重读来源并交付每条可导入记录；读取或格式变化须使发布事务回滚。
   *
   * @param consumer 含义：接收已解析来源行的事务内消费者。取值范围：非 null，不得静默丢弃已解析记录。
   * @return 本次完整重读的实际来源摘要和原始行数；不含应用层推测值。
   * @throws IOException 来源在重读期间不可用或格式损坏。
   */
  ReadReceipt read(Consumer<LexiconImportRow> consumer) throws IOException;

  /**
   * 描述一次完整来源重读的身份和原始记录数。
   *
   * @param sourceDigest 含义：适配器对实际读取来源计算的摘要。取值范围：非空白文本。
   * @param sourceRowsTotal 含义：本次重读观察到的原始来源行数。取值范围：非负整数。
   */
  record ReadReceipt(String sourceDigest, long sourceRowsTotal) {
    /**
     * 校验本次来源重读凭据。
     *
     * @param sourceDigest 含义：适配器对实际读取来源计算的摘要。取值范围：非 null、非空白。
     * @param sourceRowsTotal 含义：本次重读观察到的原始来源行数。取值范围：非负整数。
     */
    public ReadReceipt {
      if (sourceDigest == null || sourceDigest.isBlank() || sourceRowsTotal < 0) {
        throw new IllegalArgumentException("invalid source read receipt");
      }
    }
  }
}
