package io.lexiflow.api.preferences;

import io.lexiflow.api.contract.WireSemantics;
import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconBrowsePage;
import java.text.Normalizer;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** API 管理视图：只读浏览发布词库，并通过本机偏好合同叠加显式隐藏状态。 */
@WireSemantics(
    "rank:published-display;entry-id:decimal-signed-positive-long;query:literal-contains;hint-state:eligible-blocked-hidden")
@RestController
@RequestMapping("/api/v1/lexicon")
public final class LexiconBrowseController {
  private final Optional<LexiconReadRepository> repository;
  private final Optional<LocalPreferences> preferences;

  /**
   * 创建只读浏览端点；无已配置的词库时仍可启动应用。
   *
   * @param repository 含义：读取发布词库的端口。取值范围：非空 Optional，演示模式可无端口。
   * @param preferences 含义：本机显式偏好服务。取值范围：非空 Optional，服务未配置时查询不可用。
   */
  public LexiconBrowseController(
      Optional<LexiconReadRepository> repository, Optional<LocalPreferences> preferences) {
    this.repository = Objects.requireNonNull(repository);
    this.preferences = Objects.requireNonNull(preferences);
  }

  /**
   * 按规范词形和别名的子串匹配返回已发布词库的分页结果。
   *
   * @param origin 含义：浏览器请求来源。取值范围：正式或测试扩展 Origin。
   * @param query 含义：词形中应当包含的字面字符串。取值范围：空串表示分页浏览，最多 100 个字符。
   * @param sort 含义：结果排序方式。取值范围：frequency 或 alphabet。
   * @param page 含义：结果页码。取值范围：从 1 开始的正整数。
   * @param pageSize 含义：每页返回条数。取值范围：1 至 100。
   * @return 不含来源标签或跳过原因的词条页。
   */
  @PostMapping
  public ResponseEntity<Response> browse(
      @RequestHeader(value = "Origin", required = false) String origin,
      @RequestParam(defaultValue = "") String query,
      @RequestParam(defaultValue = "frequency") String sort,
      @RequestParam(defaultValue = "1") int page,
      @RequestParam(defaultValue = "20") int pageSize) {
    if (!ExtensionOrigins.allowed(origin)) throw new ResponseStatusException(HttpStatus.FORBIDDEN);
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
    var hiddenIds =
        preferences
            .orElseThrow(() -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE))
            .read()
            .hidden()
            .stream()
            .map(LocalPreferences.Hidden::entryId)
            .collect(Collectors.toSet());
    var response =
        new Response(
            result.items().stream()
                .map(
                    item -> {
                      String id = Long.toString(item.entryId());
                      return new Item(
                          id,
                          item.lemma(),
                          item.gloss(),
                          item.displayFrequencyRank(),
                          hiddenIds.contains(id)
                              ? "hidden"
                              : item.hintState().name().toLowerCase(Locale.ROOT));
                    })
                .toList(),
            result.total(),
            result.page(),
            result.pageSize());
    return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(response);
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
   * @param frequencyRank 含义：管理页发布展示名次。取值范围：正整数，缺失时为空。
   * @param hintState 含义：叠加显式偏好后的提示状态。取值范围：eligible、blocked 或 hidden。
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
