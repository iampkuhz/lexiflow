package io.lexiflow.api.preferences;

import io.lexiflow.api.contract.WireSemantics;
import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.enrichment.application.preferences.RecentHintFrequency;
import io.lexiflow.lexicon.domain.model.LexiconBrowseEntry;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** 提供仅限本机使用的接口，用于统计实际展示提示的每日次数。 */
@WireSemantics("count:actual-presentation;window:today-plus-six-local-calendar-days")
@RestController
@RequestMapping("/api/v1/recent-hints")
public final class RecentHintsController {
  /**
   * 一个可展示的词条及其七日次数。
   *
   * @param entryId 含义：词条稳定身份。取值范围：规范正 long 十进制字符串。
   * @param lemma 含义：已发布词条规范词形。取值范围：非空字符串。
   * @param gloss 含义：已发布词条短释。取值范围：非空字符串。
   * @param frequencyRank 含义：已发布展示名次，缺失时为空。取值范围：可空正安全整数。
   * @param count 含义：D-6 至 D 的提示次数。取值范围：正 long。
   */
  public record Item(String entryId, String lemma, String gloss, Long frequencyRank, long count) {}

  /**
   * 不可变的读取响应。
   *
   * @param items 含义：按次数降序、同次数稳定排序的词条列表。取值范围：非 null 列表。
   */
  public record Result(List<Item> items) {
    /** 复制响应列表，避免调用方修改已返回的快照。 */
    public Result {
      items = List.copyOf(items);
    }
  }

  private final Optional<RecentHintFrequency> counts;
  private final LexiconRuntime lexicon;

  /**
   * 创建端点，并接收可选的本机计数服务和权威词库运行时。
   *
   * @param frequencyRank 含义：已发布展示名次，缺失时为空。取值范围：可空正安全整数。
   * @param counts 含义：本机每日计数服务。取值范围：已初始化时存在，否则为空。
   * @param lexicon 含义：已发布或显式 demo 词库查询入口。取值范围：非 null。
   */
  public RecentHintsController(Optional<RecentHintFrequency> counts, LexiconRuntime lexicon) {
    this.counts = counts;
    this.lexicon = lexicon;
  }

  /**
   * 在配置了计数服务时，于本机午夜定时清理保留窗口之外的数据。
   *
   * <p>未配置计数服务时不执行清理。
   */
  @org.springframework.scheduling.annotation.Scheduled(cron = "0 0 0 * * *")
  public void pruneDaily() {
    counts.ifPresent(RecentHintFrequency::prune);
  }

  /**
   * 记录已展示的词条身份，或读取七日汇总；响应不会被缓存。
   *
   * @param origin 含义：请求来源。取值范围：必须与固定扩展 Origin 完全相同。
   * @param body 含义：record 或 read 操作对象。取值范围：与操作匹配的精确字段集合。
   * @return record 时为无 body 204，read 时为七日汇总 JSON。
   */
  @PostMapping(consumes = "application/json", produces = "application/json")
  public ResponseEntity<?> post(
      @RequestHeader(value = "Origin", required = false) String origin,
      @RequestBody Map<String, Object> body) {
    if (!ExtensionOrigins.allowed(origin)) throw new ResponseStatusException(HttpStatus.FORBIDDEN);
    try {
      var service =
          counts.orElseThrow(() -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE));
      if ("record".equals(body.get("action"))) {
        if (!body.keySet().equals(Set.of("action", "entryIds"))
            || !(body.get("entryIds") instanceof List<?> values)
            || values.isEmpty()
            || values.size() > 128) throw new IllegalArgumentException("invalid request fields");
        var ids = new ArrayList<Long>();
        for (Object value : values) {
          if (!(value instanceof String id) || !id.matches("[1-9][0-9]{0,18}"))
            throw new IllegalArgumentException("entryIds must be canonical positive long strings");
          long n;
          try {
            n = Long.parseLong(id);
          } catch (NumberFormatException e) {
            throw new IllegalArgumentException("entryIds contains invalid id", e);
          }
          if (!Long.toString(n).equals(id))
            throw new IllegalArgumentException("entryIds contains invalid id");
          ids.add(n);
        }
        var displayable =
            lexicon.displayableByIds(ids).stream()
                .map(LexiconBrowseEntry::entryId)
                .collect(Collectors.toSet());
        service.record(ids.stream().filter(displayable::contains).toList());
        return ResponseEntity.noContent().cacheControl(CacheControl.noStore()).build();
      }
      if (!"read".equals(body.get("action")) || !body.keySet().equals(Set.of("action")))
        throw new IllegalArgumentException("invalid request fields");
      var raw = service.read();
      var entries = lexicon.displayableByIds(raw.keySet());
      var byId = new HashMap<Long, LexiconBrowseEntry>();
      entries.forEach(e -> byId.put(e.entryId(), e));
      var items =
          raw.entrySet().stream()
              .filter(e -> byId.containsKey(e.getKey()))
              .map(
                  e -> {
                    var entry = byId.get(e.getKey());
                    return new Item(
                        Long.toString(e.getKey()), entry.lemma(), entry.gloss(), entry.displayFrequencyRank(), e.getValue());
                  })
              .toList();
      return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(new Result(items));
    } catch (io.lexiflow.api.runtime.LexiconNotReadyException e) {
      throw new ResponseStatusException(
          HttpStatus.SERVICE_UNAVAILABLE, "published lexicon unavailable", e);
    } catch (IllegalArgumentException e) {
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, e.getMessage(), e);
    }
  }
}
