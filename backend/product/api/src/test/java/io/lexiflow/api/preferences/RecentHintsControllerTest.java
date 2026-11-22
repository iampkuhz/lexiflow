package io.lexiflow.api.preferences;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import io.lexiflow.api.runtime.LexiconRuntime;
import io.lexiflow.enrichment.application.preferences.RecentHintFrequency;
import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import io.lexiflow.lexicon.domain.model.LexiconBrowseEntry;
import io.lexiflow.lexicon.domain.model.PublishedHintState;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class RecentHintsControllerTest {
  private static final String ORIGIN = "chrome-extension://cgbdoaoalppcklmcbmkdogoagppcimkk";

  @Test
  void recordAndReadAreExactOriginNoStoreAndUseDemoLexiconMetadata() throws Exception {
    var store = new MemoryStore();
    var counts =
        new RecentHintFrequency(
            store, Clock.fixed(Instant.parse("2026-11-10T12:00:00Z"), ZoneId.of("UTC")));
    var runtime = new LexiconRuntime("demo", null, new StructuredEventLogger());
    var mvc = mvc(counts, runtime);
    String id = Long.toString(LexiconIdentity.entryId("deliver"));

    mvc.perform(
            post("/api/v1/recent-hints")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"read\"}"))
        .andExpect(status().isForbidden())
        .andExpect(header().string("Cache-Control", "no-store"));
    mvc.perform(
            post("/api/v1/recent-hints")
                .header("Origin", ORIGIN)
                .contentType(MediaType.APPLICATION_JSON)
                .content(
                    "{\"action\":\"record\",\"entryIds\":[\"" + id + "\"],\"lemma\":\"ignored\"}"))
        .andExpect(status().isBadRequest());
    mvc.perform(
            post("/api/v1/recent-hints")
                .header("Origin", ORIGIN)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"record\",\"entryIds\":[\"" + id + "\",\"" + id + "\"]}"))
        .andExpect(status().isNoContent())
        .andExpect(content().string(""))
        .andExpect(header().string("Cache-Control", "no-store"));
    mvc.perform(
            post("/api/v1/recent-hints")
                .header("Origin", ORIGIN)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"read\"}"))
        .andExpect(status().isOk())
        .andExpect(header().string("Cache-Control", "no-store"))
        .andExpect(
            content()
                .json(
                    "{\"items\":[{\"entryId\":\""
                        + id
                        + "\",\"lemma\":\"deliver\",\"gloss\":\"交付；传达\",\"frequencyRank\":null,\"count\":2}]}"));
  }

  @Test
  void readsPublishedDisplayRankWithoutChangingCountOrReturningRawBnc() throws Exception {
    var counts = new RecentHintFrequency(
        new MemoryStore(), Clock.fixed(Instant.parse("2026-11-10T12:00:00Z"), ZoneId.of("UTC")));
    counts.record(List.of(9L, 9L, 7L));
    var runtime = mock(LexiconRuntime.class);
    when(runtime.displayableByIds(org.mockito.ArgumentMatchers.anyCollection()))
        .thenReturn(List.of(
            new LexiconBrowseEntry(9L, "program", "程序", 1660L, 187L, PublishedHintState.ELIGIBLE),
            new LexiconBrowseEntry(7L, "word", "词", null, null, PublishedHintState.ELIGIBLE)));
    mvc(counts, runtime).perform(
            post("/api/v1/recent-hints").header("Origin", ORIGIN)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"action\":\"read\"}"))
        .andExpect(status().isOk())
        .andExpect(content().json("""
            {"items":[
              {"entryId":"9","lemma":"program","gloss":"程序","frequencyRank":187,"count":2},
              {"entryId":"7","lemma":"word","gloss":"词","frequencyRank":null,"count":1}
            ]}
            """, org.springframework.test.json.JsonCompareMode.STRICT));
  }

  private static MockMvc mvc(RecentHintFrequency counts, LexiconRuntime runtime) {
    return MockMvcBuilders.standaloneSetup(
            new RecentHintsController(java.util.Optional.of(counts), runtime))
        .addFilters(new PreferencesNoStoreFilter())
        .build();
  }

  private static final class MemoryStore implements RecentHintFrequency.Store {
    private final Map<Long, Long> values = new HashMap<>();

    @Override
    public void record(java.time.LocalDate today, List<Long> ids) {
      ids.forEach(id -> values.merge(id, 1L, Long::sum));
    }

    @Override
    public Map<Long, Long> read(java.time.LocalDate today) {
      var ordered = new java.util.LinkedHashMap<Long, Long>();
      values.entrySet().stream()
          .sorted(Map.Entry.<Long, Long>comparingByValue().reversed()
              .thenComparing(Map.Entry.comparingByKey()))
          .forEach(entry -> ordered.put(entry.getKey(), entry.getValue()));
      return java.util.Collections.unmodifiableMap(ordered);
    }

    @Override
    public void prune(java.time.LocalDate today) {}
  }
}
