package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import tools.jackson.core.JsonToken;
import tools.jackson.core.json.JsonFactory;

class StructuredEventLoggerTest {
  @Test
  void parsesSingleLineJsonAndDistinguishesKnownZeroFromUnknown() throws Exception {
    var line = new AtomicReference<String>();
    var level = new AtomicReference<StructuredEvent.Level>();
    var logger =
        new StructuredEventLogger(
            (l, json) -> {
              level.set(l);
              line.set(json);
            });
    var id = UUID.randomUUID();
    var e =
        new StructuredEvent(
            StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
            StructuredEvent.Reason.NO_HINT,
            0,
            Map.of(StructuredEvent.Count.SELECTED, 0L),
            0L,
            id,
            Map.of(StructuredEvent.Timing.API, 0L),
            null,
            null,
            Map.of());
    assertTrue(logger.tryEmit(() -> e));
    assertFalse(line.get().contains("\n"));
    assertFalse(line.get().contains("\r"));
    var fields = parse(line.get());
    assertEquals("lexiflow.event.v1", fields.get("schema"));
    assertEquals("caption.request.completed", fields.get("event"));
    assertEquals("request", fields.get("stage"));
    assertEquals("PASS", fields.get("result"));
    assertEquals("NO_HINT", fields.get("reason"));
    assertEquals(0L, fields.get("duration_ms"));
    assertEquals(0L, fields.get("lexicon_version"));
    assertEquals(id.toString(), fields.get("request_id"));
    assertEquals(Map.of("selected", 0L), fields.get("counts"));
    assertEquals(Map.of("api", 0L), fields.get("timings_ms"));
    assertNull(fields.get("step"));
    assertNull(fields.get("reason_counts"));
    assertEquals(StructuredEvent.Level.INFO, level.get());

    var unknown =
        new StructuredEvent(
            StructuredEvent.EventType.RUNTIME_DEPENDENCY_CHANGED,
            StructuredEvent.Reason.RECOVERED,
            1,
            Map.of(),
            null,
            null,
            Map.of(),
            null,
            null,
            Map.of());
    assertTrue(logger.tryEmit(() -> unknown));
    var absent = parse(line.get());
    assertFalse(absent.containsKey("lexicon_version"));
    assertFalse(absent.containsKey("counts"));
    assertFalse(absent.containsKey("request_id"));
    assertFalse(absent.containsKey("timings_ms"));
    assertEquals(StructuredEvent.Level.INFO, level.get());
  }

  @Test
  void parsesImportFieldsAndFixedRuleCounts() throws Exception {
    var line = new AtomicReference<String>();
    var logger = new StructuredEventLogger((level, json) -> line.set(json));
    var stage =
        new StructuredEvent(
            StructuredEvent.EventType.LEXICON_IMPORT_STAGE,
            StructuredEvent.Reason.OK,
            3,
            Map.of(StructuredEvent.Count.HINT_ROWS, 2L),
            null,
            null,
            Map.of(),
            StructuredEvent.ImportStep.PREPARE,
            StructuredEvent.ImportPhase.COMPLETED,
            Map.of());
    assertTrue(logger.tryEmit(() -> stage));
    var fields = parse(line.get());
    assertEquals("prepare", fields.get("step"));
    assertEquals("completed", fields.get("phase"));
    assertEquals(Map.of("hint_rows", 2L), fields.get("counts"));
    assertFalse(fields.containsKey("reason_counts"));

    var completed =
        new StructuredEvent(
            StructuredEvent.EventType.LEXICON_IMPORT_COMPLETED,
            StructuredEvent.Reason.OK,
            4,
            Map.of(),
            null,
            null,
            Map.of(),
            null,
            null,
            Map.of(StructuredEvent.ReasonCount.NON_ASCII_LEMMA, 0L));
    assertTrue(logger.tryEmit(() -> completed));
    fields = parse(line.get());
    assertEquals(Map.of("non_ascii_lemma", 0L), fields.get("reason_counts"));
    assertFalse(fields.containsKey("step"));
    assertFalse(fields.containsKey("phase"));
  }

  @Test
  void passesFixedLevelToSinkAndDoesNotLeakThrownText() {
    var level = new AtomicReference<StructuredEvent.Level>();
    var line = new AtomicReference<String>();
    var logger =
        new StructuredEventLogger(
            (l, json) -> {
              level.set(l);
              line.set(json);
            });
    for (var row :
        new Object[][] {
          {
            StructuredEvent.EventType.RUNTIME_DEPENDENCY_CHANGED,
            StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE,
            StructuredEvent.Level.WARN
          },
          {
            StructuredEvent.EventType.RUNTIME_DEPENDENCY_CHANGED,
            StructuredEvent.Reason.RECOVERED,
            StructuredEvent.Level.INFO
          },
          {
            StructuredEvent.EventType.LEXICON_IMPORT_COMPLETED,
            StructuredEvent.Reason.SOURCE_INVALID,
            StructuredEvent.Level.ERROR
          },
          {
            StructuredEvent.EventType.LEXICON_IMPORT_COMPLETED,
            StructuredEvent.Reason.CANCELLED,
            StructuredEvent.Level.WARN
          }
        }) {
      var e =
          new StructuredEvent(
              (StructuredEvent.EventType) row[0],
              (StructuredEvent.Reason) row[1],
              0,
              Map.of(),
              null,
              null,
              Map.of(),
              null,
              null,
              Map.of());
      assertTrue(logger.tryEmit(() -> e));
      assertEquals(row[2], level.get());
    }
    String privateText = "synthetic caption\nhttps://private.invalid/watch?token=credential";
    var before = line.get();
    assertFalse(
        logger.tryEmit(
            () -> {
              throw new IllegalStateException(privateText);
            }));
    assertEquals(before, line.get());
    assertFalse(
        new StructuredEventLogger(
                (l, json) -> {
                  throw new IllegalStateException(privateText);
                })
            .tryEmit(
                () ->
                    new StructuredEvent(
                        StructuredEvent.EventType.RUNTIME_DEPENDENCY_CHANGED,
                        StructuredEvent.Reason.RECOVERED,
                        0,
                        Map.of(),
                        null,
                        null,
                        Map.of(),
                        null,
                        null,
                        Map.of())));
    assertFalse(line.get().contains(privateText));
  }

  private static Map<String, Object> parse(String json) throws Exception {
    var fields = new HashMap<String, Object>();
    try (var parser =
        new JsonFactory().createParser(tools.jackson.core.ObjectReadContext.empty(), json)) {
      assertEquals(JsonToken.START_OBJECT, parser.nextToken());
      while (parser.nextToken() != JsonToken.END_OBJECT) {
        String key = parser.currentName();
        var token = parser.nextToken();
        if (token == JsonToken.START_OBJECT) {
          var nested = new HashMap<String, Long>();
          while (parser.nextToken() != JsonToken.END_OBJECT) {
            String nestedKey = parser.currentName();
            parser.nextToken();
            nested.put(nestedKey, parser.getLongValue());
          }
          fields.put(key, nested);
        } else if (token == JsonToken.VALUE_STRING) fields.put(key, parser.getString());
        else if (token == JsonToken.VALUE_NUMBER_INT) fields.put(key, parser.getLongValue());
        else throw new AssertionError("unexpected JSON token " + token);
      }
      assertNull(parser.nextToken());
    }
    return fields;
  }
}
