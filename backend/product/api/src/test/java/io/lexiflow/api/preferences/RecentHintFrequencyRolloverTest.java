package io.lexiflow.api.preferences;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.lexiflow.enrichment.application.preferences.RecentHintFrequency;
import io.lexiflow.enrichment.platform.preferences.SqliteRecentHintFrequencyStore;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class RecentHintFrequencyRolloverTest {
  @TempDir Path temp;

  @Test
  void readStartedBeforeMidnightCannotDeleteTheNewDaysCounts() throws Exception {
    String jdbc = "jdbc:sqlite:" + temp.resolve("rollover.db");
    PreferencesSchemaCommand.run(new String[] {jdbc});
    var sqlite = new SqliteRecentHintFrequencyStore(jdbc);
    var yesterday = LocalDate.of(2026, 11, 9);
    var now = new AtomicReference<>(Instant.parse("2026-11-09T23:59:59Z"));
    var oldPruneStarted = new CountDownLatch(1);
    var releaseOldPrune = new CountDownLatch(1);
    var store =
        new RecentHintFrequency.Store() {
          @Override
          public void record(LocalDate today, List<Long> ids) {
            sqlite.record(today, ids);
          }

          @Override
          public Map<Long, Long> read(LocalDate today) {
            return sqlite.read(today);
          }

          @Override
          public void prune(LocalDate today) {
            if (today.equals(yesterday)) {
              oldPruneStarted.countDown();
              try {
                assertTrue(releaseOldPrune.await(10, TimeUnit.SECONDS));
              } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new AssertionError(e);
              }
            }
            sqlite.prune(today);
          }
        };
    var clock =
        new Clock() {
          @Override
          public ZoneId getZone() {
            return ZoneOffset.UTC;
          }

          @Override
          public Clock withZone(ZoneId zone) {
            return Clock.fixed(now.get(), zone);
          }

          @Override
          public Instant instant() {
            return now.get();
          }
        };
    var frequency = new RecentHintFrequency(store, clock);
    var oldRead = new FutureTask<>(frequency::read);
    Thread.ofPlatform().daemon().start(oldRead);
    try {
      assertTrue(oldPruneStarted.await(5, TimeUnit.SECONDS));
      now.set(Instant.parse("2026-11-10T00:00:00Z"));
      var newWrite =
          new FutureTask<>(
              () -> {
                frequency.record(List.of(9L));
                return null;
              });
      var writer = Thread.ofPlatform().daemon().start(newWrite);
      // 等待写入操作完成，或确认其正在等待服务监视器。
      long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
      while (!newWrite.isDone() && !waitingFor(writer, frequency)) {
        assertTrue(System.nanoTime() < deadline, "new-day write did not start");
        Thread.sleep(5);
      }
      releaseOldPrune.countDown();
      oldRead.get(5, TimeUnit.SECONDS);
      newWrite.get(5, TimeUnit.SECONDS);
      assertEquals(Map.of(9L, 1L), frequency.read());
    } finally {
      releaseOldPrune.countDown();
    }
  }

  private static boolean waitingFor(Thread thread, RecentHintFrequency frequency) {
    if (thread.getState() != Thread.State.BLOCKED) return false;
    var info =
        java.lang.management.ManagementFactory.getThreadMXBean().getThreadInfo(thread.threadId());
    var lock = info == null ? null : info.getLockInfo();
    return lock != null && lock.getIdentityHashCode() == System.identityHashCode(frequency);
  }
}
