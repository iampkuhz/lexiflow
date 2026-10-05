package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.FutureTask;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import tools.jackson.core.JsonToken;
import tools.jackson.core.json.JsonFactory;

class StructuredEventLoggerTest {
  @Test
  void successfulRequestStatisticsAreDebugOnlyButFailuresRetainFullIds() {
    var slf4j =
        (ch.qos.logback.classic.Logger)
            org.slf4j.LoggerFactory.getLogger(StructuredEventLogger.class);
    var originalLevel = slf4j.getLevel();
    var appender =
        new ch.qos.logback.core.read.ListAppender<ch.qos.logback.classic.spi.ILoggingEvent>();
    appender.start();
    slf4j.addAppender(appender);
    var logger = new StructuredEventLogger();
    var id = UUID.randomUUID();
    try {
      slf4j.setLevel(ch.qos.logback.classic.Level.INFO);
      for (var reason :
          java.util.List.of(
              StructuredEvent.Reason.OK,
              StructuredEvent.Reason.NO_HINT,
              StructuredEvent.Reason.NO_NEW_SEGMENTS)) {
        assertTrue(logger.tryEmit(() -> requestEvent(reason, id)));
      }
      assertTrue(appender.list.isEmpty());
      for (var reason :
          java.util.List.of(
              StructuredEvent.Reason.INVALID_REQUEST, StructuredEvent.Reason.INTERNAL_ERROR)) {
        assertTrue(logger.tryEmit(() -> requestEvent(reason, id)));
      }
      assertEquals(2, appender.list.size());
      assertEquals(ch.qos.logback.classic.Level.WARN, appender.list.get(0).getLevel());
      assertEquals(ch.qos.logback.classic.Level.ERROR, appender.list.get(1).getLevel());
      assertTrue(
          appender.list.stream()
              .allMatch(entry -> entry.getFormattedMessage().contains(id.toString())));
      appender.list.clear();
      slf4j.setLevel(ch.qos.logback.classic.Level.DEBUG);
      assertTrue(logger.tryEmit(() -> requestEvent(StructuredEvent.Reason.NO_HINT, id)));
      assertEquals(1, appender.list.size());
      assertEquals(ch.qos.logback.classic.Level.DEBUG, appender.list.getFirst().getLevel());
      assertTrue(appender.list.getFirst().getFormattedMessage().contains("|DEBUG|" + id + "|"));
      assertTrue(appender.list.getFirst().getFormattedMessage().contains("reason=NO_HINT"));
      appender.list.clear();
      assertTrue(logger.tryEmit(() -> requestEvent(StructuredEvent.Reason.NO_HINT, id, 7L)));
      assertTrue(appender.list.getFirst().getFormattedMessage().contains("lexicon_version=7"));
    } finally {
      slf4j.setLevel(originalLevel);
      slf4j.detachAppender(appender);
      appender.stop();
    }
  }

  private static StructuredEvent requestEvent(StructuredEvent.Reason reason, UUID id) {
    return requestEvent(reason, id, null);
  }

  private static StructuredEvent requestEvent(
      StructuredEvent.Reason reason, UUID id, Long lexiconVersion) {
    return new StructuredEvent(
        StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED,
        reason,
        2,
        Map.of(),
        lexiconVersion,
        id,
        Map.of(StructuredEvent.Timing.API, 2L),
        null,
        null,
        Map.of());
  }

  @Test
  void saturatedReadableQueueDropsInsteadOfBlockingCaller() throws Exception {
    var entered = new CountDownLatch(1);
    var release = new CountDownLatch(1);
    try (var executor =
        new ThreadPoolExecutor(
            1,
            1,
            0,
            TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<>(1),
            new ThreadPoolExecutor.DiscardPolicy())) {
      executor.setThreadFactory(
          runnable -> {
            var thread = new Thread(runnable, "readable-log-test");
            thread.setDaemon(true);
            return thread;
          });
      var logger =
          new StructuredEventLogger(
              null,
              executor,
              line -> {
                entered.countDown();
                try {
                  release.await();
                } catch (InterruptedException interrupted) {
                  Thread.currentThread().interrupt();
                }
              });
      try {
        assertTrue(logger.tryEmitReadableInfo("incremental", UUID.randomUUID(), Map.of(), "one"));
        assertTrue(entered.await(1, TimeUnit.SECONDS));
        assertTrue(logger.tryEmitReadableInfo("incremental", UUID.randomUUID(), Map.of(), "two"));
        var caller =
            new FutureTask<>(
                () ->
                    logger.tryEmitReadableInfo(
                        "incremental", UUID.randomUUID(), Map.of(), "three"));
        new Thread(caller, "readable-log-caller-test").start();
        assertTrue(caller.get(1, TimeUnit.SECONDS));
        assertEquals(1, executor.getQueue().size());
      } finally {
        release.countDown();
        executor.shutdownNow();
      }
    }
  }

  @Test
  void readableSinkFailureAfterSubmissionDoesNotEscapeIntoRequestOrPrintSensitiveException() {
    var submitted = new AtomicReference<Runnable>();
    var attempted = new java.util.concurrent.atomic.AtomicBoolean();
    var logger =
        new StructuredEventLogger(
            null,
            submitted::set,
            line -> {
              attempted.set(true);
              throw new IllegalStateException("private sink failure");
            });
    assertTrue(logger.tryEmitReadableInfo("incremental", UUID.randomUUID(), Map.of(), "caption"));
    org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> submitted.get().run());
    assertTrue(attempted.get());
  }

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
