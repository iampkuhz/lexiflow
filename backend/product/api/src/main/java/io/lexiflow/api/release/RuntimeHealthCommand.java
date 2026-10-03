package io.lexiflow.api.release;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.core.json.JsonFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 消费本机固定 readiness 与运行状态的短命探针，不暴露响应正文或异常。 */
public final class RuntimeHealthCommand {
  private static final URI READINESS =
      URI.create("http://127.0.0.1:8080/actuator/health/readiness");
  private static final URI RUNTIME_STATUS =
      URI.create("http://127.0.0.1:8080/api/v1/runtime-status");
  private static final int MAX_BODY_BYTES = 4096;
  private static final Duration TOTAL_TIMEOUT = Duration.ofSeconds(2);
  private static final Set<String> RUNTIME_FIELDS =
      Set.of("softwareVersion", "apiContract", "mode", "ready", "reason", "datasetVersion");

  private RuntimeHealthCommand() {}

  /**
   * 执行固定本机运行检查；不接受调用者提供地址或输出服务响应。
   *
   * @param args 含义：命令行参数。取值范围：空数组，非空拒绝。
   */
  public static void main(String[] args) {
    if (args.length != 0) {
      System.exit(1);
      return;
    }
    var summary = probe(READINESS, RUNTIME_STATUS);
    if (summary == null) {
      System.exit(1);
      return;
    }
    System.out.println(summary);
  }

  /** 包内测试 seam；生产 main 固定传入容器自身两个端点。 */
  static String probe(URI readiness, URI runtimeStatus) {
    return probe(readiness, runtimeStatus, () -> {});
  }

  /** 包内测试 seam；关闭回调仅用于确定性确认每条退出路径都释放 transport。 */
  static String probe(URI readiness, URI runtimeStatus, Runnable closeObserver) {
    long deadline = System.nanoTime() + TOTAL_TIMEOUT.toNanos();
    try (var transport = new ProbeTransport(closeObserver)) {
      byte[] readinessBody = request(transport.client, readiness, deadline);
      if (readinessBody == null || !validUp(readinessBody)) return null;
      byte[] runtimeBody = request(transport.client, runtimeStatus, deadline);
      if (runtimeBody == null) return null;
      var status = parseRuntimeStatus(runtimeBody);
      if (status == null) return null;
      return "{\"softwareVersion\":\""
          + status.softwareVersion
          + "\",\"apiContract\":\""
          + status.apiContract
          + "\",\"mode\":\""
          + status.mode
          + "\",\"ready\":true,\"reason\":\""
          + status.reason
          + "\",\"datasetVersion\":"
          + status.datasetVersion
          + "}";
    } catch (RuntimeException ignored) {
      return null;
    }
  }

  /**
   * 拥有本次探针专属 HTTP client 与 executor，并以非阻塞方式取消和关闭它们。
   *
   * <p>{@code close()} 不等待活动 I/O 完成，避免健康探针的清理超过共享 deadline；JDK {@code HttpClient.shutdownNow()}
   * 对活动交换仅提供尽力取消语义。
   */
  private static final class ProbeTransport implements AutoCloseable {
    private final ExecutorService executor;
    private final HttpClient client;
    private final Runnable closeObserver;

    private ProbeTransport(Runnable closeObserver) {
      executor =
          Executors.newSingleThreadExecutor(
              runnable -> {
                Thread thread = new Thread(runnable, "runtime-health-probe");
                thread.setDaemon(true);
                return thread;
              });
      try {
        client =
            HttpClient.newBuilder()
                .connectTimeout(TOTAL_TIMEOUT)
                .followRedirects(HttpClient.Redirect.NEVER)
                .proxy(HttpClient.Builder.NO_PROXY)
                .executor(executor)
                .version(HttpClient.Version.HTTP_1_1)
                .build();
      } catch (RuntimeException | Error failure) {
        executor.shutdownNow();
        throw failure;
      }
      this.closeObserver = closeObserver;
    }

    @Override
    public void close() {
      try {
        client.shutdownNow();
      } finally {
        try {
          executor.shutdownNow();
        } finally {
          closeObserver.run();
        }
      }
    }
  }

  private static byte[] request(HttpClient client, URI endpoint, long deadline) {
    CompletableFuture<HttpResponse<byte[]>> response = null;
    try {
      long remaining = deadline - System.nanoTime();
      if (remaining <= 0) return null;
      HttpRequest request =
          HttpRequest.newBuilder(endpoint).timeout(Duration.ofNanos(remaining)).GET().build();
      response = client.sendAsync(request, ignored -> new BoundedBodySubscriber());
      remaining = deadline - System.nanoTime();
      if (remaining <= 0) {
        response.cancel(true);
        return null;
      }
      HttpResponse<byte[]> result = response.get(remaining, TimeUnit.NANOSECONDS);
      return System.nanoTime() < deadline && result.statusCode() == 200 ? result.body() : null;
    } catch (TimeoutException ignored) {
      if (response != null) response.cancel(true);
      return null;
    } catch (Exception ignored) {
      if (response != null) response.cancel(true);
      return null;
    }
  }

  /** 限制 HTTP 响应体在复制进探针内存前不超过固定上限。 */
  private static final class BoundedBodySubscriber implements HttpResponse.BodySubscriber<byte[]> {
    private final CompletableFuture<byte[]> body = new CompletableFuture<>();
    private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    private Flow.Subscription subscription;
    private int size;

    @Override
    public java.util.concurrent.CompletionStage<byte[]> getBody() {
      return body;
    }

    @Override
    public void onSubscribe(Flow.Subscription value) {
      subscription = value;
      value.request(Long.MAX_VALUE);
    }

    @Override
    public void onNext(List<java.nio.ByteBuffer> buffers) {
      for (var buffer : buffers) {
        int remaining = buffer.remaining();
        if (remaining > MAX_BODY_BYTES - size) {
          subscription.cancel();
          body.completeExceptionally(new IllegalStateException("response body exceeds limit"));
          return;
        }
        byte[] chunk = new byte[remaining];
        buffer.get(chunk);
        bytes.write(chunk, 0, chunk.length);
        size += remaining;
      }
    }

    @Override
    public void onError(Throwable failure) {
      body.completeExceptionally(failure);
    }

    @Override
    public void onComplete() {
      body.complete(bytes.toByteArray());
    }
  }

  private static boolean validUp(byte[] bytes) {
    try {
      var factory = jsonFactory();
      var mapper = new ObjectMapper(factory);
      try (var parser = factory.createParser(tools.jackson.core.ObjectReadContext.empty(), bytes)) {
        JsonNode root = mapper.readTree(parser);
        return root != null
            && root.isObject()
            && root.path("status").isString()
            && "UP".equals(root.path("status").asString())
            && parser.nextToken() == null;
      }
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  private static RuntimeSummary parseRuntimeStatus(byte[] bytes) {
    try {
      var factory = jsonFactory();
      var mapper = new ObjectMapper(factory);
      try (var parser = factory.createParser(tools.jackson.core.ObjectReadContext.empty(), bytes)) {
        JsonNode root = mapper.readTree(parser);
        if (root == null || !root.isObject() || parser.nextToken() != null || root.size() != 6)
          return null;
        for (String field : RUNTIME_FIELDS) if (!root.has(field)) return null;
        JsonNode versionNode = root.get("softwareVersion");
        JsonNode contractNode = root.get("apiContract");
        JsonNode modeNode = root.get("mode");
        JsonNode readyNode = root.get("ready");
        JsonNode reasonNode = root.get("reason");
        JsonNode datasetNode = root.get("datasetVersion");
        if (!versionNode.isString()
            || !contractNode.isString()
            || !modeNode.isString()
            || !readyNode.isBoolean()
            || !reasonNode.isString()
            || !datasetNode.isIntegralNumber()
            || !datasetNode.canConvertToLong()) return null;
        String version = versionNode.asString();
        // 版本响应沿用软件身份的标准语法，不扩展运行时公开接口。
        if (!validSoftwareVersion(version)) return null;
        long datasetVersion = datasetNode.longValue();
        String reason = reasonNode.asString();
        if (!"caption-hints.v1".equals(contractNode.asString())
            || !"formal".equals(modeNode.asString())
            || !readyNode.asBoolean()
            || !("OK".equals(reason) || "PREWARM_DEGRADED".equals(reason))
            || datasetVersion <= 0) return null;
        return new RuntimeSummary(
            version, contractNode.asString(), modeNode.asString(), reason, datasetVersion);
      }
    } catch (RuntimeException ignored) {
      return null;
    }
  }

  private static boolean validSoftwareVersion(String value) {
    try {
      return value.equals(io.lexiflow.api.runtime.SoftwareIdentity.parseVersion(value));
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  private static JsonFactory jsonFactory() {
    return JsonFactory.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build();
  }

  /**
   * 已校验且可安全输出的运行状态摘要。
   *
   * @param softwareVersion 含义：软件版本。取值范围：有效三段式版本。
   * @param apiContract 含义：接口合同。取值范围：固定 caption-hints.v1。
   * @param mode 含义：运行模式。取值范围：formal。
   * @param reason 含义：就绪原因。取值范围：OK 或 PREWARM_DEGRADED。
   * @param datasetVersion 含义：发布资料版本。取值范围：正整数。
   */
  private record RuntimeSummary(
      String softwareVersion,
      String apiContract,
      String mode,
      String reason,
      long datasetVersion) {}
}
