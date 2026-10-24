package io.lexiflow.api.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.availability.ApplicationAvailability;
import org.springframework.boot.availability.ReadinessState;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.mock.web.MockHttpServletRequest;

class ContainerHealthControllerTest {
  private ApplicationAvailability availability;
  private LexiconRuntime runtime;
  private SoftwareIdentity identity;
  private RuntimeInitialization initialization;
  private ContainerHealthController controller;

  @BeforeEach
  void setUp() {
    availability = mock(ApplicationAvailability.class);
    runtime = mock(LexiconRuntime.class);
    identity = mock(SoftwareIdentity.class);
    initialization = mock(RuntimeInitialization.class);
    controller = new ContainerHealthController(availability, runtime, identity, initialization);
    when(identity.version()).thenReturn("1.2.3");
    when(availability.getReadinessState()).thenReturn(ReadinessState.ACCEPTING_TRAFFIC);
    var dataset =
        new RuntimeInitialization.PublishedDataset("rel", "prep", "rule", "sql", "a".repeat(64));
    when(initialization.evaluate(
            org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.eq("1.2.3")))
        .thenAnswer(
            invocation -> {
              var state = invocation.<LexiconRuntime.State>getArgument(0);
              return new RuntimeInitialization.Evaluation(
                  true,
                  state.mode(),
                  state.reason(),
                  state.version(),
                  state.ready(),
                  state.ready() ? dataset : null);
            });
  }

  @Test
  void emitsExactNoStoreJsonWithLengthForFormalReadyAndPrewarmDegraded() {
    for (var reason :
        new LexiconRuntime.Reason[] {
          LexiconRuntime.Reason.OK, LexiconRuntime.Reason.PREWARM_DEGRADED
        }) {
      when(runtime.probe()).thenReturn(new LexiconRuntime.State("formal", reason, 42L, true));
      var response = controller.health(request("127.0.0.1"));
      var body =
          "{\"softwareVersion\":\"1.2.3\",\"apiContract\":\"caption-hints.v2\",\"mode\":\"formal\",\"ready\":true,\"reason\":\""
              + reason.name()
              + "\",\"datasetVersion\":42,\"publishedDataset\":{\"releaseId\":\"rel\",\"preparationId\":\"prep\",\"ruleId\":\"rule\",\"sqlVersion\":\"sql\",\"sha256\":\""
              + "a".repeat(64)
              + "\"}}";
      assertEquals(HttpStatus.OK, response.getStatusCode());
      assertEquals(body, new String(response.getBody(), java.nio.charset.StandardCharsets.UTF_8));
      assertEquals("application/json", response.getHeaders().getFirst(HttpHeaders.CONTENT_TYPE));
      assertEquals(
          Integer.toString(response.getBody().length),
          response.getHeaders().getFirst(HttpHeaders.CONTENT_LENGTH));
      assertEquals("no-store", response.getHeaders().getFirst(HttpHeaders.CACHE_CONTROL));
    }
    verify(runtime, org.mockito.Mockito.times(2)).probe();
  }

  @Test
  void sourceRuntimeIsReadyWithoutClaimingPublishedDatasetBinding() {
    when(runtime.probe())
        .thenReturn(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 42L, true));
    when(initialization.evaluate(
            org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.eq("1.2.3")))
        .thenReturn(
            new RuntimeInitialization.Evaluation(
                false, "formal", LexiconRuntime.Reason.OK, 42L, true, null));

    var response = controller.health(request("127.0.0.1"));

    assertEquals(HttpStatus.OK, response.getStatusCode());
    assertEquals(
        "{\"softwareVersion\":\"1.2.3\",\"apiContract\":\"caption-hints.v2\",\"mode\":\"formal\",\"ready\":true,\"reason\":\"OK\",\"datasetVersion\":42,\"publishedDataset\":null}",
        new String(response.getBody(), java.nio.charset.StandardCharsets.UTF_8));
  }

  @Test
  void returnsEmpty503ForEveryNonReadyStateAndDoesNotLeakExceptions() {
    when(availability.getReadinessState()).thenReturn(ReadinessState.REFUSING_TRAFFIC);
    assertUnavailable(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 42L, true));
    when(availability.getReadinessState()).thenReturn(ReadinessState.ACCEPTING_TRAFFIC);
    assertUnavailable(
        new LexiconRuntime.State("formal", LexiconRuntime.Reason.NO_PUBLISHED_DATA, 0L, false));
    assertUnavailable(
        new LexiconRuntime.State(
            "formal", LexiconRuntime.Reason.DEPENDENCY_UNAVAILABLE, null, false));
    assertUnavailable(
        new LexiconRuntime.State("formal", LexiconRuntime.Reason.SCHEMA_MISMATCH, null, false));
    assertUnavailable(
        new LexiconRuntime.State("demo", LexiconRuntime.Reason.DEMO_MODE, null, false));
    assertUnavailable(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 0L, true));
    assertUnavailable(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, -1L, true));
    when(runtime.probe())
        .thenReturn(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 8L, true));
    when(identity.version()).thenReturn("invalid-version");
    assertEquals(
        HttpStatus.SERVICE_UNAVAILABLE, controller.health(request("127.0.0.1")).getStatusCode());
    when(runtime.probe()).thenThrow(new IllegalStateException("sensitive jdbc password"));
    var failure = controller.health(request("127.0.0.1"));
    assertEquals(HttpStatus.SERVICE_UNAVAILABLE, failure.getStatusCode());
    assertEquals(0, failure.getBody().length);
    assertNull(failure.getHeaders().getFirst(HttpHeaders.CONTENT_TYPE));
  }

  @Test
  void onlyExactLoopbackRemoteAddressesAreAllowedAndForwardedHeadersAreIgnored() {
    for (var address : new String[] {"127.0.0.1", "::1", "0:0:0:0:0:0:0:1"}) {
      when(runtime.probe())
          .thenReturn(new LexiconRuntime.State("formal", LexiconRuntime.Reason.OK, 7L, true));
      assertEquals(HttpStatus.OK, controller.health(request(address)).getStatusCode());
    }
    var remote = request("192.0.2.1");
    remote.addHeader("X-Forwarded-For", "127.0.0.1");
    remote.addHeader("Forwarded", "for=127.0.0.1;proto=http");
    var response = controller.health(remote);
    assertEquals(HttpStatus.NOT_FOUND, response.getStatusCode());
    assertEquals(0, response.getBody().length);
    assertEquals("no-store", response.getHeaders().getFirst(HttpHeaders.CACHE_CONTROL));
    verify(runtime, org.mockito.Mockito.times(3)).probe();
  }

  private void assertUnavailable(LexiconRuntime.State state) {
    when(runtime.probe()).thenReturn(state);
    var response = controller.health(request("127.0.0.1"));
    assertEquals(HttpStatus.SERVICE_UNAVAILABLE, response.getStatusCode());
    assertTrue(response.getBody().length == 0);
    assertEquals("no-store", response.getHeaders().getFirst(HttpHeaders.CACHE_CONTROL));
  }

  private static MockHttpServletRequest request(String address) {
    var request = new MockHttpServletRequest();
    request.setRemoteAddr(address);
    return request;
  }
}
