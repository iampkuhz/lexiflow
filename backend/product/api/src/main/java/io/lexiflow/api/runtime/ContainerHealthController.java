package io.lexiflow.api.runtime;

import jakarta.servlet.http.HttpServletRequest;
import java.nio.charset.StandardCharsets;
import java.util.Objects;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 供容器本机探针使用的最小、loopback-only 健康合同。 */
@RestController
public final class ContainerHealthController {
  private static final byte[] EMPTY = new byte[0];
  private final ApplicationAvailability availability;
  private final LexiconRuntime runtime;
  private final SoftwareIdentity softwareIdentity;
  private final RuntimeInitialization initialization;

  /**
   * 装配容器探针所需的进程状态、词库状态与软件身份。
   *
   * @param availability 含义：进程接收流量状态。取值范围：非 null。
   * @param runtime 含义：唯一词库状态 owner。取值范围：非 null。
   * @param softwareIdentity 含义：已校验的构建身份。取值范围：非 null。
   * @param initialization 含义：当次启动与资料身份证明核验器。取值范围：非 null。
   */
  public ContainerHealthController(
      ApplicationAvailability availability,
      LexiconRuntime runtime,
      SoftwareIdentity softwareIdentity,
      RuntimeInitialization initialization) {
    this.availability = Objects.requireNonNull(availability);
    this.runtime = Objects.requireNonNull(runtime);
    this.softwareIdentity = Objects.requireNonNull(softwareIdentity);
    this.initialization = Objects.requireNonNull(initialization);
  }

  /**
   * 对本机请求返回同次探测的安全摘要，其他状态失败关闭。
   *
   * @param request 含义：读取实际连接 remoteAddr 的 Servlet 请求，不消费转发头。取值范围：非 null。
   * @return 就绪时为固定 JSON；未就绪或非本机请求为空正文。
   */
  @GetMapping("/internal/container-health")
  public ResponseEntity<byte[]> health(HttpServletRequest request) {
    if (!isLoopback(request.getRemoteAddr())) return response(HttpStatus.NOT_FOUND, EMPTY, false);
    try {
      var status = initialization.evaluate(runtime.probe(), softwareIdentity.version());
      var version = status.datasetVersion();
      var identity = SoftwareIdentity.parseVersion(softwareIdentity.version());
      boolean ready =
          availability.getReadinessState() == ReadinessState.ACCEPTING_TRAFFIC
              && "formal".equals(status.mode())
              && status.ready()
              && (!status.releaseImage() || status.publishedDataset() != null)
              && (status.reason() == LexiconRuntime.Reason.OK
                  || status.reason() == LexiconRuntime.Reason.PREWARM_DEGRADED)
              && version != null
              && version > 0;
      if (!ready) return response(HttpStatus.SERVICE_UNAVAILABLE, EMPTY, false);
      String body =
          "{\"softwareVersion\":\""
              + identity
              + "\",\"apiContract\":\""
              + RuntimeStatusController.API_CONTRACT
              + "\",\"mode\":\"formal\",\"ready\":true,\"reason\":\""
              + status.reason().name()
              + "\",\"datasetVersion\":"
              + version
              + ",\"publishedDataset\":"
              + (status.publishedDataset() == null
                  ? "null"
                  : "{\"releaseId\":\""
                      + status.publishedDataset().releaseId()
                      + "\",\"preparationId\":\""
                      + status.publishedDataset().preparationId()
                      + "\",\"ruleId\":\""
                      + status.publishedDataset().ruleId()
                      + "\",\"sqlVersion\":\""
                      + status.publishedDataset().sqlVersion()
                      + "\",\"sha256\":\""
                      + status.publishedDataset().sha256()
                      + "\"}")
              + "}";
      return response(HttpStatus.OK, body.getBytes(StandardCharsets.UTF_8), true);
    } catch (RuntimeException ignored) {
      return response(HttpStatus.SERVICE_UNAVAILABLE, EMPTY, false);
    }
  }

  private static boolean isLoopback(String address) {
    return "127.0.0.1".equals(address)
        || "::1".equals(address)
        || "0:0:0:0:0:0:0:1".equals(address);
  }

  private static ResponseEntity<byte[]> response(HttpStatus status, byte[] body, boolean success) {
    var builder = ResponseEntity.status(status).cacheControl(CacheControl.noStore());
    if (success) {
      builder.contentType(MediaType.APPLICATION_JSON);
      builder.header(HttpHeaders.CONTENT_LENGTH, Integer.toString(body.length));
    }
    return builder.body(body);
  }
}
