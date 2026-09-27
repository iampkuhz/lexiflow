package io.lexiflow.api.hints;

import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.api.hints.model.CaptionHintResponse;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import java.io.IOException;
import java.util.Locale;
import java.util.Objects;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** 为当前可见新增字幕提供不阻塞的词段级中文提示。 */
@RestController
@RequestMapping("/api/v1/caption-hints")
public final class CaptionHintController {
  private static final Logger LOG = LoggerFactory.getLogger(CaptionHintController.class);
  private final EnrichCaptionUseCase useCase;
  private final SegmentAnalysisLog analysisLog;

  /** 注入应用用例；控制器不访问词典存储或供应商实现。 */
  public CaptionHintController(EnrichCaptionUseCase useCase, SegmentAnalysisLog analysisLog) {
    this.useCase = Objects.requireNonNull(useCase, "useCase");
    this.analysisLog = Objects.requireNonNull(analysisLog, "analysisLog");
  }

  /**
   * 处理双快照；响应只包含本次已处理的新增片段与提示。
   *
   * @param request 含义：插件提交的双快照增量请求。取值范围：非空且满足领域快照合同。
   * @return 禁止缓存的处理覆盖、词段提示与服务端计时响应。
   */
  @PostMapping
  public ResponseEntity<CaptionHintResponse> hint(@RequestBody CaptionHintRequest request) {
    var started = System.nanoTime();
    try {
      var domainRequest = Objects.requireNonNull(request).toDomain();
      var measured = useCase.enrichIncrementalMeasured(domainRequest);
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
      try {
        analysisLog.record(domainRequest, result);
      } catch (IOException exception) {
        LOG.warn("segment analysis log unavailable; processing result was not recorded");
      }
      var totalMillis = (System.nanoTime() - started) / 1_000_000.0;
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
    } catch (IllegalArgumentException | NullPointerException exception) {
      throw new ResponseStatusException(
          HttpStatus.BAD_REQUEST, "invalid caption request", exception);
    }
  }
}
