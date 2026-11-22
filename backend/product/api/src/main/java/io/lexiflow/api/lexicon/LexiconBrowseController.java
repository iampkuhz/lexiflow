package io.lexiflow.api.lexicon;

import io.lexiflow.api.contract.WireSemantics;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconBrowsePage;
import java.text.Normalizer;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** 只读查看已发布词库的精确匹配结果或分页结果。 */
@WireSemantics("rank:published-frequency;entry-id:decimal-signed-positive-long")
@RestController
@RequestMapping("/api/v1/lexicon")
public final class LexiconBrowseController {
  private final Optional<LexiconReadRepository> repository;

  /**
   * 创建只读浏览端点；无已配置的词库时仍可启动应用。
   *
   * @param repository 含义：读取发布词库的端口。取值范围：非空 Optional，演示模式可无端口。
   */
  public LexiconBrowseController(Optional<LexiconReadRepository> repository) {
    this.repository = Objects.requireNonNull(repository);
  }

  /**
   * 返回已发布词库的精确匹配或分页结果。
   *
   * @param query 含义：精确查询词形。取值范围：空串表示分页浏览，最多 100 个字符。
   * @param sort 含义：结果排序方式。取值范围：frequency 或 alphabet。
   * @param page 含义：结果页码。取值范围：从 1 开始的正整数。
   * @param pageSize 含义：每页返回条数。取值范围：1 至 100。
   * @return 不含来源标签或跳过原因的词条页。
   */
  @GetMapping
  public Response browse(
      @RequestParam(defaultValue = "") String query,
      @RequestParam(defaultValue = "frequency") String sort,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int pageSize) {
    if (query.length() > 100
        || page < 1
        || pageSize < 1
        || pageSize > 100
        || (long) (page - 1) * pageSize > Integer.MAX_VALUE
        || !(sort.equals("frequency") || sort.equals("alphabet")))
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST);
    String normalized = normalize(query);
    LexiconBrowsePage result =
        repository
            .orElseThrow(() -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE))
            .browse(normalized, sort.equals("frequency"), page, pageSize);
    return new Response(
        result.items().stream()
            .map(
                item ->
                    new Item(
                        Long.toString(item.entryId()),
                        item.lemma(),
                        item.gloss(),
                        item.frequencyRank(),
                        item.hintState().name().toLowerCase(Locale.ROOT)))
            .toList(),
        result.total(),
        result.page(),
        result.pageSize());
  }

  private static String normalize(String value) {
    return Normalizer.normalize(value, Normalizer.Form.NFC)
        .trim()
        .replaceAll("\\s+", " ")
        .toLowerCase(Locale.ROOT);
  }

  /**
   * 表示词库浏览结果中的一条公开词项。
   *
   * @param entryId 含义：稳定词条身份。取值范围：正整数的十进制字符串。
   * @param lemma 含义：规范英文词形。取值范围：非空词或短语。
   * @param gloss 含义：可用于提示的简短中文释义。取值范围：无释义时为空。
   * @param frequencyRank 含义：来源语料词频排名。取值范围：正整数，缺失时为空。
   * @param hintState 含义：词库的系统提示资格。取值范围：eligible 或 blocked。
   */
  public record Item(
      String entryId, String lemma, String gloss, Long frequencyRank, String hintState) {}

  /**
   * 表示一页词库浏览响应及其分页元数据。
   *
   * @param items 含义：当前页词项。取值范围：非空列表，允许没有结果。
   * @param total 含义：命中查询条件的词项总数。取值范围：非负整数。
   * @param page 含义：当前页码。取值范围：从一开始。
   * @param pageSize 含义：本次分页的容量。取值范围：一至一百条。
   */
  public record Response(java.util.List<Item> items, long total, int page, int pageSize) {}
}
