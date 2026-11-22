package io.lexiflow.api.preferences;

import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import java.util.Map;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import io.lexiflow.api.runtime.LexiconRuntime;

/** 暴露仅供指定本机 Chrome 扩展使用的显式偏好接口。 请求必须来自固定扩展 Origin，并携带精确的 JSON action 字段。 */
@RestController
@RequestMapping("/api/v1/preferences")
public final class PreferencesController {
  private final java.util.Optional<LocalPreferences> preferences;
  private final java.util.Optional<LexiconRuntime> lexicon;

  /**
   * 使用已装配的偏好服务创建控制器。
   *
   * @param preferences 含义：本机显式偏好的应用服务。取值范围：非空服务实例。
   */
  public PreferencesController(LocalPreferences preferences) {
    this.preferences = java.util.Optional.of(preferences);
    this.lexicon = java.util.Optional.empty();
  }

  /**
   * 在偏好卷未配置时仍创建控制器，以便请求得到服务不可用响应。
   *
   * @param preferences 含义：可选的本机偏好服务。取值范围：存在时为已装配服务，否则为空。
   */
  @org.springframework.beans.factory.annotation.Autowired
  public PreferencesController(java.util.Optional<LocalPreferences> preferences,
      java.util.Optional<LexiconRuntime> lexicon) {
    this.preferences = preferences;
    this.lexicon = lexicon;
  }

  /**
   * 校验固定 Origin 与请求字段后执行一项偏好操作。 返回结果不缓存；字段错误以 400、来源错误以 403、存储未配置以 503 响应。
   *
   * @param origin 含义：浏览器提供的请求来源。取值范围：必须精确等于配置的 Chrome 扩展 Origin。
   * @param body 含义：单次操作及其参数。取值范围：read、setCutoff、hide 或 restore 对应的精确 JSON 字段集合。
   * @return 操作后的完整偏好状态；响应设置为 no-store。
   */
  @PostMapping(
      consumes = MediaType.APPLICATION_JSON_VALUE,
      produces = MediaType.APPLICATION_JSON_VALUE)
  public ResponseEntity<LocalPreferences.State> post(
      @RequestHeader(value = "Origin", required = false) String origin,
      @RequestBody Map<String, Object> body) {
    if (!ExtensionOrigins.allowed(origin)) throw new ResponseStatusException(HttpStatus.FORBIDDEN);
    try {
      var service =
          preferences.orElseThrow(
              () -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE));
      Object action = body.get("action");
      if (!(action instanceof String a)) throw new IllegalArgumentException("action required");
      LocalPreferences.State result =
          switch (a) {
            case "read" -> {
              requireKeys(body, "action");
              yield service.read();
            }
            case "setCutoff" -> {
              requireKeys(body, "action", "commonWordCutoff");
              Object v = body.get("commonWordCutoff");
              if (!(v instanceof Integer n))
                throw new IllegalArgumentException("commonWordCutoff must be an integer");
              yield service.setCutoff(n);
            }
            case "hide" -> {
              requireKeys(body, "action", "entryId");
              String id = string(body, "entryId");
              long numericId = positiveId(id);
              var entry = lexicon.orElseThrow(
                  () -> new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE))
                  .displayableByIds(java.util.List.of(numericId)).stream().findFirst()
                  .orElseThrow(() -> new IllegalArgumentException("entryId is not displayable"));
              yield service.hide(id, entry.lemma(), entry.gloss());
            }
            case "restore" -> {
              requireKeys(body, "action", "entryId");
              yield service.restore(string(body, "entryId"));
            }
            default -> throw new IllegalArgumentException("unsupported action");
          };
      return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(result);
    } catch (IllegalArgumentException e) {
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, e.getMessage(), e);
    }
  }

  private static String string(Map<String, Object> b, String k) {
    Object v = b.get(k);
    if (!(v instanceof String s)) throw new IllegalArgumentException(k + " must be a string");
    return s;
  }

  private static long positiveId(String value) {
    try {
      long id = Long.parseLong(value);
      if (id < 1 || !Long.toString(id).equals(value)) throw new NumberFormatException();
      return id;
    } catch (NumberFormatException invalid) {
      throw new IllegalArgumentException("entryId must be a positive decimal integer");
    }
  }

  private static void requireKeys(Map<String, Object> b, String... expected) {
    if (b.size() != expected.length || !java.util.Set.of(expected).equals(b.keySet()))
      throw new IllegalArgumentException("invalid request fields");
  }
}
