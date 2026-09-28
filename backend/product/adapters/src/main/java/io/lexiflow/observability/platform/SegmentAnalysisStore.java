package io.lexiflow.observability.platform;

import java.io.IOException;
import java.util.List;

/** 同步接收用户显式授权的本机敏感分析记录。 */
public interface SegmentAnalysisStore {
  /**
   * 追加片段记录；仅在同步持久化成功后视为已记录。
   *
   * @param records 含义：需追加的不可变片段记录。取值范围：非 null 列表。
   * @throws IOException 初始化、读取或持久化失败时抛出
   */
  void append(List<SegmentAnalysisRecord> records) throws IOException;

  /**
   * 创建不执行任何写入的禁用 Store。
   *
   * @return 对输入记录不做持久化的实现。
   */
  static SegmentAnalysisStore disabled() {
    return records -> {};
  }
}
