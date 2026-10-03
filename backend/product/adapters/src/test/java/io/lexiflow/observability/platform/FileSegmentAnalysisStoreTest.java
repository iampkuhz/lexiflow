package io.lexiflow.observability.platform;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class FileSegmentAnalysisStoreTest {
  @TempDir Path dir;

  @Test
  void emptyDisabledAndRestartDeduplicate() throws Exception {
    var path = dir.resolve("private/log.jsonl");
    var rec = new SegmentAnalysisRecord("a".repeat(64), "a\nb", List.of());
    var store = new FileSegmentAnalysisStore(path, null);
    store.append(List.of());
    assertFalse(Files.exists(path));
    store.append(List.of(rec));
    new FileSegmentAnalysisStore(path, null).append(List.of(rec));
    assertEquals(1, Files.readAllLines(path).size());
    SegmentAnalysisStore.disabled().append(List.of(rec));
  }

  @Test
  void rejectsInvalidJsonAndIncompleteTail() throws Exception {
    var path = dir.resolve("bad.jsonl");
    Files.writeString(path, "{not-json}\n");
    assertThrows(
        java.io.IOException.class,
        () ->
            new FileSegmentAnalysisStore(path, null)
                .append(List.of(new SegmentAnalysisRecord("b".repeat(64), "x", List.of()))));
  }

  @Test
  void rejectsOversizedExistingLedgerBeforeParsingOrAppending() throws Exception {
    var path = dir.resolve("oversized.jsonl");
    try (var channel =
        java.nio.channels.FileChannel.open(
            path,
            java.nio.file.StandardOpenOption.CREATE_NEW,
            java.nio.file.StandardOpenOption.WRITE)) {
      channel.position(16L * 1024 * 1024);
      channel.write(java.nio.ByteBuffer.wrap(new byte[] {'\n'}));
    }
    long before = Files.size(path);
    assertThrows(
        java.io.IOException.class,
        () ->
            new FileSegmentAnalysisStore(path, null)
                .append(List.of(new SegmentAnalysisRecord("a".repeat(64), "x", List.of()))));
    assertEquals(before, Files.size(path));
  }

  @Test
  void initializedStoreRepeatedlyRejectsExternalBadTailWithoutAppending() throws Exception {
    var path = dir.resolve("reload.jsonl");
    var store = new FileSegmentAnalysisStore(path, null);
    var initial = new SegmentAnalysisRecord("a".repeat(64), "x", List.of());
    var next = new SegmentAnalysisRecord("b".repeat(64), "y", List.of());
    store.append(List.of(initial));
    Files.writeString(path, "{broken}\n", java.nio.file.StandardOpenOption.APPEND);
    var bytes = Files.readAllBytes(path);
    for (int i = 0; i < 2; i++) {
      assertThrows(java.io.IOException.class, () -> store.append(List.of(next)));
      assertEquals(
          java.util.Arrays.toString(bytes), java.util.Arrays.toString(Files.readAllBytes(path)));
    }
  }

  @Test
  void consoleFailureDoesNotUndoDurableWriteAndConsoleRunsOnlyAfterSuccess() throws Exception {
    var path = dir.resolve("console.jsonl");
    var store =
        new FileSegmentAnalysisStore(
            path,
            line -> {
              throw new IllegalStateException("private");
            });
    store.append(List.of(new SegmentAnalysisRecord("c".repeat(64), "x", List.of())));
    assertEquals(1, Files.readAllLines(path).size());
  }

  @Test
  void reloadsExternalValidAppendBeforeDeduplicating() throws Exception {
    var path = dir.resolve("external.jsonl");
    var first = new SegmentAnalysisRecord("d".repeat(64), "x", List.of());
    var second = new SegmentAnalysisRecord("e".repeat(64), "y", List.of());
    var own = new FileSegmentAnalysisStore(path, null);
    own.append(List.of(first));
    new FileSegmentAnalysisStore(path, null).append(List.of(second));
    own.append(List.of(second));
    assertEquals(2, Files.readAllLines(path).size());
  }

  @Test
  void initializedStoreRepeatedlyRejectsExternalDuplicateId() throws Exception {
    var path = dir.resolve("duplicate.jsonl");
    var own = new FileSegmentAnalysisStore(path, null);
    var existing = new SegmentAnalysisRecord("f".repeat(64), "x", List.of());
    own.append(List.of(existing));
    var original = Files.readString(path);
    Files.writeString(path, original, java.nio.file.StandardOpenOption.APPEND);
    byte[] bytes = Files.readAllBytes(path);
    var next = new SegmentAnalysisRecord("1".repeat(64), "y", List.of());
    for (int i = 0; i < 2; i++) {
      assertThrows(java.io.IOException.class, () -> own.append(List.of(next)));
      org.junit.jupiter.api.Assertions.assertArrayEquals(bytes, Files.readAllBytes(path));
    }
  }

  @Test
  void rejectsSymlinkLogPath() throws Exception {
    var target = dir.resolve("target.jsonl");
    Files.writeString(target, "");
    var link = dir.resolve("link.jsonl");
    Files.createSymbolicLink(link, target.getFileName());
    assertThrows(
        java.io.IOException.class,
        () ->
            new FileSegmentAnalysisStore(link, null)
                .append(List.of(new SegmentAnalysisRecord("2".repeat(64), "x", List.of()))));
  }

  @Test
  void rejectsActuallyIncompleteTailAndTwoJsonValuesOnOneLine() throws Exception {
    var record = new SegmentAnalysisRecord("3".repeat(64), "x", List.of());
    for (var invalid :
        List.of(
            "{\"segmentId\":\"" + "4".repeat(64) + "\"}",
            "{\"segmentId\":\""
                + "4".repeat(64)
                + "\"}"
                + "{\"segmentId\":\""
                + "5".repeat(64)
                + "\"}\n",
            "{\"segmentId\":\"bad\"}\n",
            "{\"segmentId\":\""
                + "4".repeat(64)
                + "\",\"segmentId\":\""
                + "5".repeat(64)
                + "\"}\n")) {
      var path = dir.resolve("invalid-" + Math.abs(invalid.hashCode()) + ".jsonl");
      Files.writeString(path, invalid);
      byte[] before = Files.readAllBytes(path);
      var store = new FileSegmentAnalysisStore(path, null);
      for (int i = 0; i < 2; i++) {
        assertThrows(java.io.IOException.class, () -> store.append(List.of(record)));
        assertArrayEquals(before, Files.readAllBytes(path));
      }
    }
  }

  @Test
  void successfulForcePrecedesConsoleAndSpecialCharactersStayOneJsonLine() throws Exception {
    var path = dir.resolve("private/success.jsonl");
    var consoleLines = new java.util.ArrayList<String>();
    var store =
        new FileSegmentAnalysisStore(
            path,
            line -> {
              assertTrue(Files.exists(path));
              try {
                assertEquals(
                    line.substring("[LexiFlow segment] ".length()), Files.readString(path));
              } catch (java.io.IOException ex) {
                throw new AssertionError(ex);
              }
              consoleLines.add(line);
            });
    var rec =
        new SegmentAnalysisRecord(
            "6".repeat(64),
            "x\n\"\\😀",
            List.of(
                new SegmentAnalysisRecord.TranslatedRange(0, 1, "7".repeat(64), "词\n\"", 1, true)));
    store.append(List.of(rec));
    assertEquals(1, consoleLines.size());
    assertEquals(1, Files.readAllLines(path).size());
    var saved = Files.readString(path);
    assertTrue(saved.contains("\\n"));
    assertTrue(saved.contains("\\\""));
    store.append(List.of(rec));
    assertEquals(1, consoleLines.size());
    assertEquals(saved, Files.readString(path));
    Files.writeString(path, "{broken}\n", java.nio.file.StandardOpenOption.APPEND);
    assertThrows(
        java.io.IOException.class,
        () -> store.append(List.of(new SegmentAnalysisRecord("8".repeat(64), "x", List.of()))));
    assertEquals(1, consoleLines.size());
  }

  @Test
  void createsPrivatePosixDirectoryAndFileWhenSupported() throws Exception {
    var path = dir.resolve("new-private/log.jsonl");
    var store = new FileSegmentAnalysisStore(path, null);
    store.append(List.of(new SegmentAnalysisRecord("9".repeat(64), "x", List.of())));
    if (Files.getFileStore(path).supportsFileAttributeView("posix")) {
      assertEquals(
          "rwx------",
          java.nio.file.attribute.PosixFilePermissions.toString(
              Files.getPosixFilePermissions(path.getParent())));
      assertEquals(
          "rw-------",
          java.nio.file.attribute.PosixFilePermissions.toString(
              Files.getPosixFilePermissions(path)));
    }
  }
}
