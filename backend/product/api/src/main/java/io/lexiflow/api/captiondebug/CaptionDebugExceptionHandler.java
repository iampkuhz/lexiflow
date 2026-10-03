package io.lexiflow.api.captiondebug;

import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 将调试请求解析错误映射为不回显输入的固定 400。 */
@RestControllerAdvice(assignableTypes = CaptionDebugController.class)
public final class CaptionDebugExceptionHandler {
  /**
   * 对非法 JSON 返回固定错误，不回显请求正文或解析异常。
   *
   * @param ignored 含义：框架解析错误。取值范围：不可读取请求异常。
   * @return 无缓存的固定非法请求响应。
   */
  @ExceptionHandler(HttpMessageNotReadableException.class)
  public ResponseEntity<Void> unreadable(HttpMessageNotReadableException ignored) {
    return ResponseEntity.badRequest().header("Cache-Control", "no-store").build();
  }
}
