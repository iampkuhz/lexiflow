package io.lexiflow.api.runtime;

import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 只输出固定 503 合同，不暴露原始故障。 */
@RestControllerAdvice
public final class RuntimeUnavailableAdvice {
  /**
   * 将不可用状态转换为固定 503 响应。
   *
   * @return 不含原始故障的响应。
   */
  @ExceptionHandler(LexiconNotReadyException.class)
  public ResponseEntity<Map<String, String>> unavailable() {
    return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
        .header("Cache-Control", "no-store")
        .body(Map.of("error", "LEXICON_NOT_READY"));
  }
}
