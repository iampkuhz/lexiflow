package io.lexiflow.api.captiondebug;

import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 将调试请求解析错误映射为不回显输入的固定 400。 */
@RestControllerAdvice(assignableTypes = CaptionDebugController.class)
public final class CaptionDebugExceptionHandler {
  /** 返回无正文且不可缓存的固定拒绝。 */
  @ExceptionHandler(HttpMessageNotReadableException.class)
  public ResponseEntity<Void> unreadable(HttpMessageNotReadableException ignored) {
    return ResponseEntity.badRequest().header("Cache-Control", "no-store").build();
  }
}
