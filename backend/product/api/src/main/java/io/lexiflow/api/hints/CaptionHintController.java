package io.lexiflow.api.hints;

import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.api.hints.model.CaptionHintResponse;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.observability.platform.StructuredEvent;
import io.lexiflow.observability.platform.StructuredEventLogger;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 为当前可见新增字幕提供不阻塞的词段级中文提示。 */
@RestController
@RequestMapping("/api/v1/caption-hints")
public final class CaptionHintController {
  private final EnrichCaptionUseCase useCase;
  private final SegmentAnalysisLog analysisLog;
  private final StructuredEventLogger events;

  /** 注入应用用例、敏感记录适配器及固定事件发件器。 */
  public CaptionHintController(
      EnrichCaptionUseCase useCase, SegmentAnalysisLog analysisLog, StructuredEventLogger events) {
    this.useCase = Objects.requireNonNull(useCase, "useCase");
    this.analysisLog = Objects.requireNonNull(analysisLog, "analysisLog");
    this.events = Objects.requireNonNull(events, "events");
  }

  /**
   * 处理双快照；响应只包含本次已处理的新增片段与提示。
   *
   * @param request 含义：插件提交的双快照增量请求。取值范围：非空且满足领域快照合同。
   * @param servletRequest 含义：当前 HTTP 请求及过滤器绑定的观测属性。取值范围：非空。
   * @return 禁止缓存的处理覆盖、词段提示与服务端计时响应。
   */
  @PostMapping
  public ResponseEntity<?> hint(
      @RequestBody CaptionHintRequest request, HttpServletRequest servletRequest) {
    var observation = observation(servletRequest);
    long validationStarted = System.nanoTime();
    var conversion = toDomain(request);
    observation.validation(System.nanoTime() - validationStarted);
    if (conversion.isEmpty()) {
      observation.reason(StructuredEvent.Reason.INVALID_REQUEST);
      throw CaptionRequestExceptionHandler.invalidInput();
    }
    var domainRequest = conversion.orElseThrow();

    // 用例故障必须保持原始分类，不能被输入转换的 400 分支捕获。
    var measured = useCase.enrichIncrementalMeasured(domainRequest);
    observation.measured(measured);
    if (observation.versionConflict()) {
      return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
          .header("Cache-Control", "no-store")
          .body(java.util.Map.of("error", "VERSION_CONFLICT"));
    }
    var result = measured.result();
    var body =
        new CaptionHintResponse(
            result.processedKeys(),
            result.hints().stream()
                .map(
                    hint ->
                        new CaptionHintResponse.Hint(
                            hint.startKey(),
                            hint.startOffset(),
                            hint.endKey(),
                            hint.endOffset(),
                            hint.chineseGloss(),
                            hint.lexiconEntryId(),
                            hint.lexiconVersion(),
                            hint.senseId()))
                .toList());
    if (!result.processedKeys().isEmpty()) {
      long analysisStarted = System.nanoTime();
      try {
        analysisLog.record(domainRequest, result);
      } catch (IOException | RuntimeException failure) {
        events.tryEmit(
            () ->
                new StructuredEvent(
                    StructuredEvent.EventType.ANALYSIS_RECORD_FAILED,
                    StructuredEvent.Reason.ANALYSIS_WRITE_FAILED,
                    Math.max(0, (System.nanoTime() - analysisStarted) / 1_000_000),
                    java.util.Map.of(
                        StructuredEvent.Count.ATTEMPTED_SEGMENTS,
                        (long) result.processedKeys().size()),
                    null,
                    observation.requestId(),
                    java.util.Map.of(),
                    null,
                    null,
                    java.util.Map.of()));
      } finally {
        observation.analysis(System.nanoTime() - analysisStarted);
      }
    }
    var totalMillis = (System.nanoTime() - validationStarted) / 1_000_000.0;
    return ResponseEntity.ok()
        .header("Cache-Control", "no-store")
        .header(
            "Server-Timing",
            String.format(
                Locale.ROOT,
                "query;dur=%.3f, rules;dur=%.3f, api;dur=%.3f",
                measured.queryNanos() / 1_000_000.0,
                measured.rulesNanos() / 1_000_000.0,
                totalMillis))
        .body(body);
  }

  private static Optional<io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest> toDomain(
      CaptionHintRequest request) {
    try {
      return Optional.of(Objects.requireNonNull(request).toDomain());
    } catch (IllegalArgumentException | NullPointerException invalid) {
      return Optional.empty();
    }
  }

  private static CaptionRequestObservation observation(HttpServletRequest request) {
    var observation =
        (CaptionRequestObservation)
            Objects.requireNonNull(request).getAttribute(CaptionRequestObservation.ATTRIBUTE);
    if (observation == null) throw new IllegalStateException("request observation missing");
    return observation;
  }
}
