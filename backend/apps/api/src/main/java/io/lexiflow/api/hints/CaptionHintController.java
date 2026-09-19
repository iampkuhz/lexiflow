package io.lexiflow.api.hints;

import io.lexiflow.enrichment.domain.CaptionContext;
import io.lexiflow.workflow.application.EnrichCaptionUseCase;
import java.util.Objects;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

/** 为已定位字幕提供不阻塞、词段级中文提示的 HTTP 入口。 */
@RestController
@RequestMapping("/api/v1/caption-hints")
public final class CaptionHintController {
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
  public CaptionHintResponse hint(@RequestBody CaptionHintRequest request) {
    try {
      var input = Objects.requireNonNull(request, "request");
      var result =
          useCase.enrich(
              new CaptionContext(
                  input.contentId(),
                  input.contentRevision(),
                  input.segmentId(),
                  input.caption(),
                  input.startOffset(),
                  input.endOffset()));
      return new CaptionHintResponse(
          result.caption(),
          result.state().name(),
          result.hints().stream()
              .map(
                  hint ->
                      new AnnotationHintResponse(
                          hint.startOffset(),
                          hint.endOffset(),
                          hint.lexiconEntryId(),
                          hint.lexiconVersion(),
                          hint.chineseGloss()))
              .toList());
    } catch (IllegalArgumentException | NullPointerException exception) {
      throw new ResponseStatusException(
          HttpStatus.BAD_REQUEST, "invalid caption request", exception);
    }
  }
}
