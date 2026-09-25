package io.lexiflow.api.hints;

import io.lexiflow.api.hints.model.AnnotationHintResponse;
import io.lexiflow.api.hints.model.CaptionHintRequest;
import io.lexiflow.api.hints.model.CaptionHintResponse;
import io.lexiflow.enrichment.application.caption.EnrichCaptionUseCase;
import io.lexiflow.enrichment.domain.model.CaptionContext;
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

/** 为已定位字幕提供不阻塞、词段级中文提示的 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/caption-hints")
public final class CaptionHintController {
  private static final Logger LOG = LoggerFactory.getLogger(CaptionHintController.class);
  private final EnrichCaptionUseCase useCase;

  /**
   * 注入应用用例；控制器不访问词典存储或供应商实现。
   *
   * @param useCase 已组装的字幕提示用例。
   */
  public CaptionHintController(EnrichCaptionUseCase useCase) {
    this.useCase = Objects.requireNonNull(useCase, "useCase");
  }

  /**
   * 生成当前字幕的快速提示；不创建异步工作。
   *
   * @param request 含义：受长度和范围约束的字幕请求。取值范围：非空，且必须通过请求值校验。
   * @return 保留英文字幕的确定性提示响应。
   */
  @PostMapping
  public ResponseEntity<CaptionHintResponse> hint(@RequestBody CaptionHintRequest request) {
    var started = System.nanoTime();
    try {
      var input = Objects.requireNonNull(request, "request");
      var measured =
          useCase.enrichMeasured(
              new CaptionContext(
                  input.contentId(),
                  input.contentRevision(),
                  input.segmentId(),
                  input.caption(),
                  input.startOffset(),
                  input.endOffset()));
      var result = measured.result();
      var body =
          new CaptionHintResponse(
              result.caption(),
              result.state().name(),
              result.hints().stream()
                  .map(
                      hint ->
                          new AnnotationHintResponse(
                              hint.startOffset(),
                              hint.endOffset(),
                              hint.lexiconEntryId(),
                              hint.senseId(),
                              hint.lexiconVersion(),
                              hint.chineseGloss()))
                  .toList());
      var totalMillis = (System.nanoTime() - started) / 1_000_000.0;
      var queryMillis = measured.queryNanos() / 1_000_000.0;
      var rulesMillis = measured.rulesNanos() / 1_000_000.0;
      LOG.info(
          "hint_request state={} candidates={} hints={} queryMs={} rulesMs={} apiMs={}",
          result.state(),
          measured.candidateCount(),
          result.hints().size(),
          queryMillis,
          rulesMillis,
          totalMillis);
      return ResponseEntity.ok()
          .header("Cache-Control", "no-store")
          .header(
              "Server-Timing",
              String.format(
                  Locale.ROOT,
                  "query;dur=%.3f, rules;dur=%.3f, api;dur=%.3f",
                  queryMillis,
                  rulesMillis,
                  totalMillis))
          .body(body);
    } catch (IllegalArgumentException | NullPointerException exception) {
      throw new ResponseStatusException(
          HttpStatus.BAD_REQUEST, "invalid caption request", exception);
    }
  }
}
