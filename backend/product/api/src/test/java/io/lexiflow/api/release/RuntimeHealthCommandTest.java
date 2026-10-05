package io.lexiflow.api.release;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class RuntimeHealthCommandTest {
  private static final String READY = "{\"status\":\"UP\"}";
  private static final String RUNTIME =
      "{\"softwareVersion\":\"1.2.3\",\"apiContract\":\"caption-hints.v2\","
          + "\"mode\":\"formal\",\"ready\":true,\"reason\":\"OK\",\"datasetVersion\":42}";

  @Test
  void emitsOnlyFixedSafeSummaryForReadyAndPrewarmDegradedStates() throws Exception {
    assertEquals(
        "{\"softwareVersion\":\"1.2.3\",\"apiContract\":\"caption-hints.v2\",\"mode\":\"formal\",\"ready\":true,\"reason\":\"OK\",\"datasetVersion\":42}",
        probe(READY, RUNTIME));
    assertEquals(
        "{\"softwareVersion\":\"1.2.3\",\"apiContract\":\"caption-hints.v2\",\"mode\":\"formal\",\"ready\":true,\"reason\":\"PREWARM_DEGRADED\",\"datasetVersion\":42}",
        probe(READY, RUNTIME.replace("\"OK\"", "\"PREWARM_DEGRADED\"")));
  }

  @Test
  void rejectsReadinessAndRuntimeDisagreementAndAllNonReadyValues() throws Exception {
    assertNull(probe("{\"status\":\"DOWN\"}", RUNTIME));
    assertNull(probe(READY, RUNTIME.replace("\"ready\":true", "\"ready\":false")));
    assertNull(probe(READY, RUNTIME.replace("\"mode\":\"formal\"", "\"mode\":\"demo\"")));
    assertNull(
        probe(READY, RUNTIME.replace("\"reason\":\"OK\"", "\"reason\":\"DATASET_MISSING\"")));
    assertNull(probe(READY, RUNTIME.replace("\"caption-hints.v2\"", "\"other\"")));
  }

  @Test
  void rejectsWrongTypesInvalidVersionAndInvalidDatasetVersion() throws Exception {
    String[] invalid = {
      RUNTIME.replace("\"softwareVersion\":\"1.2.3\"", "\"softwareVersion\":1.2"),
      RUNTIME.replace("\"softwareVersion\":\"1.2.3\"", "\"softwareVersion\":\"01.2.3\""),
      RUNTIME.replace("\"softwareVersion\":\"1.2.3\"", "\"softwareVersion\":\"0.0.0\""),
      RUNTIME.replace("\"apiContract\":\"caption-hints.v2\"", "\"apiContract\":true"),
      RUNTIME.replace("\"mode\":\"formal\"", "\"mode\":null"),
      RUNTIME.replace("\"ready\":true", "\"ready\":1"),
      RUNTIME.replace("\"reason\":\"OK\"", "\"reason\":false"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":true"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":0"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":-1"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":9223372036854775808"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":42.0"),
      RUNTIME.replace("\"datasetVersion\":42", "\"datasetVersion\":null")
    };
    for (String json : invalid) assertNull(probe(READY, json), json);
  }

  @Test
  void rejectsUnknownOrDuplicateRuntimeFieldsAndTrailingJson() throws Exception {
    assertNull(probe(READY, RUNTIME.substring(0, RUNTIME.length() - 1) + ",\"extra\":1}"));
    assertNull(probe(READY, RUNTIME.replace("\"ready\":true", "\"ready\":true,\"ready\":true")));
    assertNull(probe(READY, RUNTIME + " {}"));
    assertNull(probe("{\"status\":\"UP\",\"status\":\"UP\"}", RUNTIME));
    assertNull(probe(READY + " {}", RUNTIME));
  }

  @Test
  void rejectsNon200OversizedAndRedirectResponsesWithoutSummary() throws Exception {
    assertNull(probeResponse(503, READY, 200, RUNTIME, null));
    assertNull(probeResponse(200, READY, 200, "x".repeat(4097), null));
    assertNull(probeResponse(200, READY, 302, RUNTIME, "/elsewhere"));
    assertNull(probeResponse(302, READY, 200, RUNTIME, "/elsewhere"));
  }

  @Test
  void preservesSingleRequestForReadinessFailure() throws Exception {
    var runtimeRequests = new AtomicInteger();
    var transportClosed = new AtomicBoolean();
    var readyServer = server(200, "{\"status\":\"DOWN\"}", null, 0);
    var runtimeServer = server(200, RUNTIME, null, 0, runtimeRequests);
    try {
      assertNull(
          RuntimeHealthCommand.probe(
              uri(readyServer), uri(runtimeServer), () -> transportClosed.set(true)));
      assertEquals(0, runtimeRequests.get());
      assertTrue(transportClosed.get());
    } finally {
      readyServer.stop(0);
      runtimeServer.stop(0);
    }
  }

  @Test
  void appliesOneTwoSecondDeadlineAcrossBothRequests() throws Exception {
    var readyServer = server(200, READY, null, 1250);
    var runtimeServer = server(200, RUNTIME, null, 1250);
    long start = System.nanoTime();
    try {
      assertNull(RuntimeHealthCommand.probe(uri(readyServer), uri(runtimeServer)));
      assertTrue(Duration.ofNanos(System.nanoTime() - start).toMillis() < 2400);
    } finally {
      readyServer.stop(0);
      runtimeServer.stop(0);
    }
  }

  @Test
  void boundsAStalledResponseBody() throws Exception {
    var server = slowBodyServer();
    long start = System.nanoTime();
    try {
      assertNull(RuntimeHealthCommand.probe(uri(server), URI.create("http://127.0.0.1:1/")));
      assertTrue(Duration.ofNanos(System.nanoTime() - start).toSeconds() < 3);
    } finally {
      server.stop(0);
    }
  }

  @Test
  void boundsSlowResponseHeaders() throws Exception {
    var server = server(200, READY, null, 2500);
    long start = System.nanoTime();
    try {
      assertNull(RuntimeHealthCommand.probe(uri(server), URI.create("http://127.0.0.1:1/")));
      assertTrue(Duration.ofNanos(System.nanoTime() - start).toSeconds() < 3);
    } finally {
      server.stop(0);
    }
  }

  @Test
  void rejectsUnreachableEndpoint() throws Exception {
    assertNull(
        RuntimeHealthCommand.probe(
            URI.create("http://127.0.0.1:1/"), URI.create("http://127.0.0.1:1/")));
  }

  private static String probe(String readiness, String runtime) throws Exception {
    return probeResponse(200, readiness, 200, runtime, null);
  }

  private static String probeResponse(
      int readinessStatus, String readiness, int runtimeStatus, String runtime, String location)
      throws Exception {
    var readinessServer = server(readinessStatus, readiness, location, 0);
    var runtimeServer = server(runtimeStatus, runtime, location, 0);
    try {
      return RuntimeHealthCommand.probe(uri(readinessServer), uri(runtimeServer));
    } finally {
      readinessServer.stop(0);
      runtimeServer.stop(0);
    }
  }

  private static HttpServer server(int status, String text, String location, long delayMillis)
      throws Exception {
    return server(status, text, location, delayMillis, new AtomicInteger());
  }

  private static HttpServer server(
      int status, String text, String location, long delayMillis, AtomicInteger requests)
      throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        exchange -> {
          requests.incrementAndGet();
          try {
            if (delayMillis > 0) Thread.sleep(delayMillis);
            if (location != null) exchange.getResponseHeaders().add("Location", location);
            byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
          } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
          } catch (java.io.IOException ignored) {
            // 总时限到期后探针可主动断开连接。
          } finally {
            exchange.close();
          }
        });
    server.start();
    return server;
  }

  private static HttpServer slowBodyServer() throws Exception {
    var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        exchange -> {
          try {
            exchange.sendResponseHeaders(200, 0);
            Thread.sleep(2500);
            exchange.getResponseBody().write(READY.getBytes(StandardCharsets.UTF_8));
          } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
          } catch (java.io.IOException ignored) {
            // 到达总时限后探针可取消响应读取。
          } finally {
            exchange.close();
          }
        });
    server.start();
    return server;
  }

  private static URI uri(HttpServer server) {
    return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
  }
}
