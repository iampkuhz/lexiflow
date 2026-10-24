package io.lexiflow.api.runtime;

import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 提供不缓存且不泄漏内部细节的运行状态合同。 */
@RestController
public final class RuntimeStatusController {
  static final String API_CONTRACT = "caption-hints.v2";
  private final LexiconRuntime runtime;
  private final SoftwareIdentity softwareIdentity;
  private final RuntimeInitialization initialization;

  /**
   * 建立运行状态 HTTP 入口。
   *
   * @param runtime 含义：唯一词库运行状态 owner。取值范围：非 null。
   * @param softwareIdentity 含义：构建时软件身份读取器。取值范围：非 null。
   * @param initialization 含义：当次启动与资料身份证明核验器。取值范围：非 null。
   */
  public RuntimeStatusController(
      LexiconRuntime runtime,
      SoftwareIdentity softwareIdentity,
      RuntimeInitialization initialization) {
    this.runtime = runtime;
    this.softwareIdentity = softwareIdentity;
    this.initialization = initialization;
  }

  /**
   * 对同一次词库探测结果生成固定响应。
   *
   * @return HTTP 200、no-store 与固定运行状态 DTO。
   */
  @GetMapping("/api/v1/runtime-status")
  public ResponseEntity<RuntimeStatus> status() {
    var status = initialization.evaluate(runtime.probe(), softwareIdentity.version());
    return ResponseEntity.ok()
        .cacheControl(CacheControl.noStore())
        .body(
            new RuntimeStatus(
                softwareIdentity.version(),
                API_CONTRACT,
                status.mode(),
                status.ready(),
                status.reason().name(),
                status.datasetVersion(),
                status.publishedDataset()));
  }
}
