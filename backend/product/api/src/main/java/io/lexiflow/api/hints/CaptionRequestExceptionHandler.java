package io.lexiflow.api.hints;

import io.lexiflow.api.runtime.LexiconNotReadyException;
import io.lexiflow.observability.platform.StructuredEvent;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/** 将字幕请求故障映射为固定状态码、响应与观测原因，不传递异常载荷。 */
@RestControllerAdvice(assignableTypes = CaptionHintController.class)
public final class CaptionRequestExceptionHandler {
  /** 不携带字幕、原始异常或 cause 的请求输入拒绝信号。 */
  public static final class InvalidCaptionRequestException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    /** 创建不含请求载荷的固定拒绝信号。 */
    public InvalidCaptionRequestException() {
      super("invalid caption request");
    }
  }

  /** 创建固定输入拒绝信号，避免保留原始输入异常。 */
  static InvalidCaptionRequestException invalidInput() {
    return new InvalidCaptionRequestException();
  }

  /**
   * 将 JSON 解析与请求边界输入错误转换为固定 400。
   *
   * @param failure 含义：触发映射的异常类型。取值范围：不读取消息或 cause。
   * @param request 含义：当前 HTTP 请求。取值范围：携带本请求私有观测属性。
   * @return 不含异常数据的固定输入错误响应。
   */
  @ExceptionHandler({
    InvalidCaptionRequestException.class,
    HttpMessageNotReadableException.class,
    MethodArgumentTypeMismatchException.class
  })
  public ResponseEntity<Map<String, String>> invalid(
      Exception failure, HttpServletRequest request) {
    reason(request, StructuredEvent.Reason.INVALID_REQUEST);
    return fixed(HttpStatus.BAD_REQUEST, "INVALID_REQUEST");
  }

  /**
   * 使用异常抛出时冻结的运行原因生成 503 或固定内部错误。
   *
   * @param failure 含义：已绑定抛出时固定原因的不可用信号。取值范围：无原始 cause。
   * @param request 含义：当前 HTTP 请求。取值范围：携带本请求私有观测属性。
   * @return 固定状态与错误码，不读取共享运行状态或原始 cause。
   */
  @ExceptionHandler(LexiconNotReadyException.class)
  public ResponseEntity<Map<String, String>> notReady(
      LexiconNotReadyException failure, HttpServletRequest request) {
    var reason =
        switch (failure.reason()) {
          case NO_PUBLISHED_DATA -> StructuredEvent.Reason.NO_PUBLISHED_DATA;
          case DEPENDENCY_UNAVAILABLE -> StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE;
          case SCHEMA_MISMATCH -> StructuredEvent.Reason.INTERNAL_ERROR;
          default -> StructuredEvent.Reason.INTERNAL_ERROR;
        };
    reason(request, reason);
    if (reason == StructuredEvent.Reason.INTERNAL_ERROR)
      return fixed(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR");
    return fixed(HttpStatus.SERVICE_UNAVAILABLE, "LEXICON_NOT_READY");
  }

  /**
   * 将未分类故障转换为不泄漏原始错误的 500。
   *
   * @param failure 含义：未分类故障。取值范围：不读取消息、类型名或 cause。
   * @param request 含义：当前 HTTP 请求。取值范围：携带本请求私有观测属性。
   * @return 固定内部错误响应。
   */
  @ExceptionHandler(Exception.class)
  public ResponseEntity<Map<String, String>> internal(
      Exception failure, HttpServletRequest request) {
    reason(request, StructuredEvent.Reason.INTERNAL_ERROR);
    return fixed(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR");
  }

  private static void reason(HttpServletRequest request, StructuredEvent.Reason reason) {
    Object value = request.getAttribute(CaptionRequestObservation.ATTRIBUTE);
    if (value instanceof CaptionRequestObservation observation) observation.reason(reason);
  }

  private static ResponseEntity<Map<String, String>> fixed(HttpStatus status, String error) {
    return ResponseEntity.status(status)
        .header("Cache-Control", "no-store")
        .body(Map.of("error", error));
  }
}
