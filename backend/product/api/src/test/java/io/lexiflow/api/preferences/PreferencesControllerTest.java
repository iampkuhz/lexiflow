package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.application.preferences.LocalPreferences;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class PreferencesControllerTest {
  private static final String ORIGIN = "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk";

  @Test
  void exactOriginAndExplicitActionsOnly() {
    var controller = new PreferencesController(new LocalPreferences(new Store()));
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
    var id = Long.toString(io.lexiflow.lexicon.domain.port.LexiconIdentity.entryId("cat"));
    assertEquals(
        "cat",
        controller
            .post(ORIGIN, Map.of("action", "hide", "entryId", id, "lemma", "cat", "gloss", "猫"))
            .getBody()
            .hidden()
            .getFirst()
            .lemma());
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
