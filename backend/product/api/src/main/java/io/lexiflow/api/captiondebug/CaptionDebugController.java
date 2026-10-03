package io.lexiflow.api.captiondebug;

import io.lexiflow.observability.platform.StructuredEventLogger;
import java.util.Objects;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 接收扩展确认已显示的事件；正文只进入有界本机控制台流。 */
@RestController
@RequestMapping("/api/v1/caption-debug")
public final class CaptionDebugController {
  private static final java.util.regex.Pattern VIDEO_ID =
      java.util.regex.Pattern.compile("[A-Za-z0-9_-]{11}");
  private static final java.util.regex.Pattern EXTENSION_ORIGIN =
      java.util.regex.Pattern.compile("chrome-extension://[a-p]{32}");
  private final boolean enabled;
  private final StructuredEventLogger events;
  private final java.util.LinkedHashMap<UUID, Boolean> emitted = new java.util.LinkedHashMap<>();

  /**
   * 创建默认开启且可显式关闭的调试端点。
   *
   * @param consoleEnabled 本机可读日志开关。
   * @param events 固定格式观测输出。
   */
  public CaptionDebugController(
      @Value("${lexiflow.caption-debug.enabled:true}") boolean consoleEnabled,
      StructuredEventLogger events) {
    this.enabled = consoleEnabled;
    this.events = Objects.requireNonNull(events);
  }

  /**
   * 返回无敏感信息且不可缓存的本机调试能力。
   *
   * @param origin 含义：请求来源。取值范围：扩展来源或本机无 Origin 请求。
   * @return 不含敏感信息的能力响应，未知来源返回拒绝访问。
   */
  @GetMapping
  public ResponseEntity<Capability> capability(
      @RequestHeader(value = "Origin", required = false) String origin) {
    if (!trustedOrigin(origin))
      return ResponseEntity.status(HttpStatus.FORBIDDEN)
          .header("Cache-Control", "no-store")
          .body(new Capability(false));
    return ResponseEntity.ok().header("Cache-Control", "no-store").body(new Capability(enabled));
  }

  /**
   * 接收扩展确认已显示的事件；禁用或非本机请求均不输出。
   *
   * @param origin 含义：请求来源。取值范围：受信扩展来源或本机无 Origin 请求。
   * @param request 含义：待验证显示事件。取值范围：符合固定协议与长度限制的请求。
   * @return 不缓存的空响应；非法、禁用或来源不符分别返回错误状态。
   */
  @PostMapping
  public ResponseEntity<Void> append(
      @RequestHeader(value = "Origin", required = false) String origin,
      @RequestBody CaptionDebugRequest request) {
    if (!trustedOrigin(origin))
      return ResponseEntity.status(HttpStatus.FORBIDDEN)
          .header("Cache-Control", "no-store")
          .build();
    if (!enabled) return disabled();
    var parsed = parse(request);
    if (parsed == null) return badRequest();
    synchronized (emitted) {
      if (emitted.putIfAbsent(parsed.eventId(), Boolean.TRUE) != null)
        return ResponseEntity.noContent().header("Cache-Control", "no-store").build();
      while (emitted.size() > 2048) emitted.remove(emitted.keySet().iterator().next());
    }
    var location = new java.util.TreeMap<String, String>();
    location.put("topic", parsed.topicKey());
    location.put("video", parsed.videoId());
    location.put("subtitle", parsed.subtitleKey());
    location.put(
        "position_ms", parsed.positionMs() == null ? null : parsed.positionMs().toString());
    if (parsed.event() == CaptionDebugEvent.VIDEO_START && parsed.videoId() != null)
      location.put("url", "https://www.youtube.com/watch?v=" + parsed.videoId());
    events.tryEmitReadableInfo(
        parsed.event().wireValue(), parsed.eventId(), location, parsed.text());
    return ResponseEntity.noContent().header("Cache-Control", "no-store").build();
  }

  private static Validated parse(CaptionDebugRequest request) {
    if (request == null
        || request.eventId() == null
        || request.event() == null
        || request.topicKey() == null
        || request.text() == null
        || request.videoId() == null
        || request.subtitleKey() == null
        || request.positionMs() == null
        || request.topicKey().isBlank()
        || request.topicKey().length() > 128
        || hasControl(request.topicKey())
        || request.text().length() > 16_384
        || !VIDEO_ID.matcher(request.videoId()).matches()
        || request.subtitleKey().isBlank()
        || request.subtitleKey().length() > 128
        || hasControl(request.subtitleKey())
        || request.positionMs() < 0) return null;
    UUID id;
    CaptionDebugEvent event;
    try {
      id = UUID.fromString(request.eventId());
      if (!id.toString().equals(request.eventId())) return null;
      event = CaptionDebugEvent.fromWire(request.event());
    } catch (IllegalArgumentException invalid) {
      return null;
    }
    return new Validated(
        id,
        event,
        request.topicKey(),
        request.videoId(),
        request.subtitleKey(),
        request.positionMs(),
        request.text());
  }

  private static boolean hasControl(String value) {
    return value.chars().anyMatch(Character::isISOControl);
  }

  private static boolean trustedOrigin(String origin) {
    return origin == null || EXTENSION_ORIGIN.matcher(origin).matches();
  }

  private static ResponseEntity<Void> disabled() {
    return ResponseEntity.status(HttpStatus.NOT_FOUND).header("Cache-Control", "no-store").build();
  }

  private static ResponseEntity<Void> badRequest() {
    return ResponseEntity.badRequest().header("Cache-Control", "no-store").build();
  }

  /**
   * 能力探测响应。
   *
   * @param enabled 本机可读日志是否启用；独立于事后分析台账。
   */
  public record Capability(boolean enabled) {}

  /**
   * 通过来源及字段边界校验的显示事件，内部身份不受控制台精简影响。
   *
   * @param eventId 真实事件身份，用于去重。
   * @param event 白名单事件类型。
   * @param topicKey 真实主题键。
   * @param videoId 真实视频身份。
   * @param subtitleKey 真实字幕键。
   * @param positionMs 非负播放毫秒数。
   * @param text 已确认显示的正文。
   */
  private record Validated(
      UUID eventId,
      CaptionDebugEvent event,
      String topicKey,
      String videoId,
      String subtitleKey,
      Long positionMs,
      String text) {}

  /** 专用字幕调试事件的固定白名单。 */
  private enum CaptionDebugEvent {
    VIDEO_START("video-start"),
    INCREMENTAL("incremental"),
    FINAL("final"),
    INTERRUPTED("interrupted");

    private final String wireValue;

    CaptionDebugEvent(String wireValue) {
      this.wireValue = wireValue;
    }

    private String wireValue() {
      return wireValue;
    }

    private static CaptionDebugEvent fromWire(String value) {
      for (var event : values()) if (event.wireValue.equals(value)) return event;
      throw new IllegalArgumentException("unknown event");
    }
  }
}
