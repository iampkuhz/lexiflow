package io.lexiflow.lexicon.platform.importer;

import java.io.PrintStream;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/** 将单个 Gradle 重建任务拆成可见阶段，仅在实际执行时定期报告状态。 */
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
  private boolean waitingForInput;

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
    scheduleHeartbeat();
  }

  synchronized void detail(String value) {
    if (stageNumber == 0) throw new IllegalStateException("no active stage");
    detail = value;
    print("内部步骤：" + detail + "；本阶段已用时 " + elapsed());
  }

  synchronized void awaitInput(String prompt) {
    if (stageNumber == 0) throw new IllegalStateException("no active stage");
    waitingForInput = true;
    heartbeat.cancel(false);
    // 与 tick 共用锁，防止已入队的心跳在输入提示后覆盖它。
    print("等待你的输入；尚未删除数据；等待期间不执行重建、不输出心跳。\n" + prompt);
  }

  synchronized void resumeExecution(String value) {
    if (!waitingForInput) throw new IllegalStateException("not waiting for input");
    waitingForInput = false;
    detail(value);
    scheduleHeartbeat();
  }

  private void scheduleHeartbeat() {
    heartbeat =
        scheduler.scheduleAtFixedRate(
            this::tick, intervalMillis, intervalMillis, TimeUnit.MILLISECONDS);
  }

  synchronized void complete() {
    if (stageNumber == 0) throw new IllegalStateException("no active stage");
    heartbeat.cancel(false);
    print("完成；本阶段用时 " + elapsed());
    stageNumber = 0;
    waitingForInput = false;
  }

  private synchronized void tick() {
    if (stageNumber != 0 && !waitingForInput) {
      print("状态更新：仍在执行；当前内部步骤「" + detail + "」；本阶段已用时 " + elapsed());
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
