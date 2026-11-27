package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.domain.model.LexiconBrowseEntry;
import io.lexiflow.lexicon.domain.model.LexiconBrowsePage;
import io.lexiflow.lexicon.domain.model.PublishedHintState;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class PreferencesControllerTest {
  private static final String ORIGIN = "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk";

  @Test
  void hiddenRequestAndBrowseShareExactIdentityAndDisplayRank() {
    var service = Optional.of(new LocalPreferences(new Store()));
    var runtime = org.mockito.Mockito.mock(LexiconRuntime.class);
    var repository = org.mockito.Mockito.mock(LexiconReadRepository.class);
    long requestId = LexiconIdentity.entryId("request");
    String id = Long.toString(requestId);
    var request =
        new LexiconBrowseEntry(
            requestId, "request", "请求", 1821L, 1234L, PublishedHintState.ELIGIBLE);
    var requester =
        new LexiconBrowseEntry(
            LexiconIdentity.entryId("requester"),
            "requester",
            "请求者",
            null,
            null,
            PublishedHintState.ELIGIBLE);
    org.mockito.Mockito.when(runtime.displayableByIds(List.of(requestId)))
        .thenReturn(List.of(request));
    org.mockito.Mockito.when(repository.browse("request", true, 1, 20))
        .thenReturn(new LexiconBrowsePage(List.of(request, requester), 2, 1, 20));
    var preferences = new PreferencesController(service, Optional.of(runtime));
    var browse = new LexiconBrowseController(Optional.of(repository), service);
    var hidden =
        preferences
            .post(ORIGIN, Map.of("action", "hide", "entryId", id))
            .getBody()
            .hidden()
            .getFirst();
    assertEquals(id, hidden.entryId());
    assertEquals(1234L, hidden.frequencyRank());
    var page = browse.browse(ORIGIN, "request", "frequency", 1, 20).getBody();
    assertEquals(hidden.entryId(), page.items().getFirst().entryId());
    assertEquals(hidden.frequencyRank(), page.items().getFirst().frequencyRank());
    assertEquals("hidden", page.items().getFirst().hintState());
    assertEquals("eligible", page.items().getLast().hintState());
    preferences.post(ORIGIN, Map.of("action", "restore", "entryId", id));
    assertEquals(
        "eligible",
        browse
            .browse(ORIGIN, "request", "frequency", 1, 20)
            .getBody()
            .items()
            .getFirst()
            .hintState());
  }

  @Test
  void exactOriginAndExplicitActionsOnly() {
    var runtime = org.mockito.Mockito.mock(LexiconRuntime.class);
    var id = Long.toString(io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId("cat"));
    org.mockito.Mockito.when(runtime.displayableByIds(List.of(Long.parseLong(id))))
        .thenReturn(
            List.of(
                new LexiconBrowseEntry(
                    Long.parseLong(id), "cat", "猫", null, null, PublishedHintState.ELIGIBLE)));
    var controller =
        new PreferencesController(
            java.util.Optional.of(new LocalPreferences(new Store())),
            java.util.Optional.of(runtime));
    assertEquals(
        1500, controller.post(ORIGIN, Map.of("action", "read")).getBody().commonWordCutoff());
    assertThrows(
        org.springframework.web.server.ResponseStatusException.class,
        () -> controller.post(null, Map.of("action", "read")));
    assertThrows(
        org.springframework.web.server.ResponseStatusException.class,
        () -> controller.post("chrome-extension://wrong", Map.of("action", "read")));
    assertThrows(
        org.springframework.web.server.ResponseStatusException.class,
        () -> controller.post(ORIGIN, Map.of("action", "read", "unexpected", true)));
    assertThrows(
        org.springframework.web.server.ResponseStatusException.class,
        () ->
            controller.post(
                ORIGIN, Map.of("action", "hide", "entryId", "1", "lemma", "cat", "gloss", "猫")));
    assertEquals(
        "cat",
        controller
            .post(ORIGIN, Map.of("action", "hide", "entryId", id))
            .getBody()
            .hidden()
            .getFirst()
            .lemma());
  }

  @Test
  void invalidEntryIdsKeepTheirCauseAndReturnTheSameBadRequest() {
    var runtime = org.mockito.Mockito.mock(LexiconRuntime.class);
    var store = new Store();
    var controller =
        new PreferencesController(Optional.of(new LocalPreferences(store)), Optional.of(runtime));
    for (String id : List.of("not-a-number", "0", "01", "9223372036854775808")) {
      var failure =
          assertThrows(
              org.springframework.web.server.ResponseStatusException.class,
              () -> controller.post(ORIGIN, Map.of("action", "hide", "entryId", id)));
      assertEquals(400, failure.getStatusCode().value());
      assertEquals("entryId must be a positive decimal integer", failure.getReason());
      assertTrue(failure.getCause() instanceof IllegalArgumentException);
      assertTrue(failure.getCause().getCause() instanceof NumberFormatException);
    }
    assertTrue(store.hidden.isEmpty());
    org.mockito.Mockito.verifyNoInteractions(runtime);
  }

  @Test
  void guardedOriginMatchesThePublicChromeManifestKey() throws Exception {
    String root = System.getProperty("lexiflow.repository.root");
    String manifest =
        java.nio.file.Files.readString(java.nio.file.Path.of(root, "extension/manifest.json"));
    var matcher =
        java.util.regex.Pattern.compile("\\\"key\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"").matcher(manifest);
    assertTrue(matcher.find());
    byte[] hash =
        java.security.MessageDigest.getInstance("SHA-256")
            .digest(java.util.Base64.getDecoder().decode(matcher.group(1)));
    StringBuilder id = new StringBuilder();
    for (int i = 0; i < 16; i++) {
      id.append((char) ('a' + ((hash[i] >>> 4) & 15)));
      id.append((char) ('a' + (hash[i] & 15)));
    }
    assertEquals(ORIGIN, "chrome-extension://" + id);
  }

  static class Store implements LocalPreferences.Store {
    int cutoff = 1500;
    Map<String, LocalPreferences.Hidden> hidden = new HashMap<>();

    public LocalPreferences.State read() {
      return new LocalPreferences.State(cutoff, List.copyOf(hidden.values()));
    }

    public LocalPreferences.State setCutoff(int c) {
      cutoff = c;
      return read();
    }

    public LocalPreferences.State hide(String id, String lemma, String gloss) {
      hidden.put(id, new LocalPreferences.Hidden(id, lemma, gloss));
      return read();
    }

    public LocalPreferences.State restore(String id) {
      hidden.remove(id);
      return read();
    }
  }
}
