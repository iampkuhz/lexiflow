package io.lexiflow.lexicon.platform.importer;

import java.io.PrintStream;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/** 将单个 Gradle 重建任务拆成可见阶段，并为耗时或等待输入的阶段定期报告状态。 */
final class RebuildProgress implements AutoCloseable {

  private final PrintStream output;
  private final long intervalMillis;
  private final ScheduledExecutorService scheduler =
      Executors.newSingleThreadScheduledExecutor(
          runnable -> {
            var thread = new Thread(runnable, "lexicon-rebuild-progress");
            thread.setDaemon(true);
            return thread;
          });
  private ScheduledFuture<?> heartbeat;
  private int stageNumber;
  private String stageName;
  private String detail;
  private long startedNanos;

  RebuildProgress(PrintStream output, long intervalMillis) {
    if (intervalMillis < 1) throw new IllegalArgumentException("interval must be positive");
    this.output = output;
    this.intervalMillis = intervalMillis;
  }

  synchronized void start(int number, String name, String firstDetail) {
    if (stageNumber != 0) throw new IllegalStateException("previous stage is incomplete");
    stageNumber = number;
    stageName = name;
    detail = firstDetail;
    startedNanos = System.nanoTime();
    print("开始；" + detail);
    heartbeat =
        scheduler.scheduleAtFixedRate(
            this::tick, intervalMillis, intervalMillis, TimeUnit.MILLISECONDS);
  }

  synchronized void detail(String value) {
    if (stageNumber == 0) throw new IllegalStateException("no active stage");
    detail = value;
    print("内部步骤：" + detail + "；本阶段已用时 " + elapsed());
  }

  synchronized void complete() {
    if (stageNumber == 0) throw new IllegalStateException("no active stage");
    heartbeat.cancel(false);
    print("完成；本阶段用时 " + elapsed());
    stageNumber = 0;
  }

  private synchronized void tick() {
    if (stageNumber != 0) {
      print("状态更新：仍在执行或等待；当前内部步骤「" + detail + "」；本阶段已用时 " + elapsed());
    }
  }

  private String elapsed() {
    long seconds = TimeUnit.NANOSECONDS.toSeconds(System.nanoTime() - startedNanos);
    return (seconds / 60) + " 分 " + (seconds % 60) + " 秒";
  }

  private void print(String message) {
    output.printf("[lexiconRebuild 阶段 %d/4 %s] %s%n", stageNumber, stageName, message);
    output.flush();
  }

  @Override
  public synchronized void close() {
    if (stageNumber != 0) {
      heartbeat.cancel(false);
      print("未完成；本阶段已用时 " + elapsed());
      stageNumber = 0;
    }
    scheduler.shutdownNow();
  }
}
