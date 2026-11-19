package io.lexiflow.enrichment.application.preferences;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * 跟踪扩展程序实际展示提示的本机每日次数。
 *
 * <p>日期选择与存储操作共用同一监视器，避免午夜前开始的请求在其他请求写入新日期后，仍应用前一日的保留窗口。
 */
public final class RecentHintFrequency {
  /** 仅负责有界每日次数投影的持久化端口。 */
  public interface Store {
    /**
     * 为列表中的每个元素原子递增每日次数，重复元素也分别计数。
     *
     * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
     * @param entryIds 含义：实际展示的词条稳定身份。取值范围：非空元素为正 long。
     */
    void record(LocalDate today, List<Long> entryIds);

    /**
     * 读取 D-6 至 D 的次数总和，按次数降序排列，相同次数按稳定身份排序。
     *
     * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
     * @return 七个自然日的身份次数映射。
     */
    Map<Long, Long> read(LocalDate today);

    /**
     * 删除保留窗口之外的次数。
     *
     * <p>保留窗口为今日 D-7 至 D，包含两端。
     *
     * @param today 含义：后端本机时区的今日。取值范围：非 null 日期。
     */
    void prune(LocalDate today);
  }

  private final Store store;
  private final java.time.Clock clock;

  /**
   * 创建每日计数服务。
   *
   * @param store 含义：只保存每日次数的持久化端口。取值范围：非 null。
   * @param clock 含义：用于按后端本机时区确定今日的时钟。取值范围：非 null。
   */
  public RecentHintFrequency(Store store, java.time.Clock clock) {
    this.store = java.util.Objects.requireNonNull(store);
    this.clock = java.util.Objects.requireNonNull(clock);
  }

  /**
   * 记录已展示的词条身份，重复 ID 按实际展示次数分别保留。
   *
   * @param ids 含义：一批已校验且可展示的词条 ID。取值范围：最多 128 个正 long。
   */
  public synchronized void record(List<Long> ids) {
    var today = LocalDate.now(clock);
    store.prune(today);
    store.record(today, List.copyOf(ids));
  }

  /**
   * 清理过期日期后读取七日汇总。
   *
   * @return 按次数降序和 ID 升序排列的不可变汇总。
   */
  public synchronized Map<Long, Long> read() {
    var today = LocalDate.now(clock);
    store.prune(today);
    return store.read(today);
  }

  /**
   * 根据配置的时钟清理持久化窗口。
   *
   * <p>保留今日及之前七日的数据。
   */
  public synchronized void prune() {
    store.prune(LocalDate.now(clock));
  }
}
