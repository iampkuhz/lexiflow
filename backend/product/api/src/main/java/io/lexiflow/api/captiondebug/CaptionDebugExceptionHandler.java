package io.lexiflow.api.captiondebug;

import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 将调试请求解析错误映射为不回显输入的固定 400。 */
@RestControllerAdvice(assignableTypes = CaptionDebugController.class)
public final class CaptionDebugExceptionHandler {
  /**
   * 返回无正文且不可缓存的固定拒绝。
   *
   * @param ignored 含义：请求解析异常，仅用于匹配处理器。取值范围：框架传入的异常，不读取其正文。
   * @return 固定的无缓存 400 响应。
   */
  @ExceptionHandler(HttpMessageNotReadableException.class)
  public ResponseEntity<Void> unreadable(HttpMessageNotReadableException ignored) {
    return ResponseEntity.badRequest().header("Cache-Control", "no-store").build();
  }
}
