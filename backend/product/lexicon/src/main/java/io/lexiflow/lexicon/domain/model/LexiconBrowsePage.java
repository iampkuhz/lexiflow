package io.lexiflow.lexicon.domain.model;

import java.util.List;

/**
 * 已发布词库的一页稳定浏览结果。
 *
 * @param items 含义：当前页的词项。取值范围：不可变列表，数量不超过 pageSize。
 * @param total 含义：符合查询条件的总词项数。取值范围：非负 long。
 * @param page 含义：当前页码。取值范围：从 1 开始的正整数。
 * @param pageSize 含义：每页词项数上限。取值范围：正整数。
 */
public record LexiconBrowsePage(
    List<LexiconBrowseEntry> items, long total, int page, int pageSize) {
  /** 冻结结果并校验总数、页码和页大小。 */
  public LexiconBrowsePage {
    items = List.copyOf(items);
    if (total < 0 || page < 1 || pageSize < 1 || items.size() > pageSize)
      throw new IllegalArgumentException("browse page is invalid");
  }
}
