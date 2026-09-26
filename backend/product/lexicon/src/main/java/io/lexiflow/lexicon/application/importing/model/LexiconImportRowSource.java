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
   * @throws IOException 来源在重读期间不可用或格式损坏。
   */
  void read(Consumer<LexiconImportRow> consumer) throws IOException;
}
