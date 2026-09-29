package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.LexiconImportObservation;
import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.LexiconImportPreparation;
import io.lexiflow.lexicon.application.importing.LexiconImportService;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRowSource;
import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistence;
import io.lexiflow.observability.platform.LexiconEventObserver;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.function.Consumer;
import org.springframework.dao.DataAccessException;

/** 离线词库导入命令；只解析本机受控输入并调用应用层导入服务。 */
public final class LexiconImportMain {

  private LexiconImportMain() {}

  /**
   * 执行 `validate`、`basic-report`、`prewarm-report` 或显式 `publish`。
   *
   * @param args 含义：受控命令行参数。取值范围：由方法调用前置条件限定。
   */
  public static void main(String[] args) {
    try {
      execute(args, ignored -> {});
    } catch (Exception exception) {
      System.err.println("FAIL " + exception.getMessage());
      System.exit(1);
    }
  }

  // 重建入口传入阶段内进度回调；普通导入命令保持原有输出合同。
  static void execute(String[] args, Consumer<String> progress) throws Exception {
    execute(args, progress, null);
  }

  /** 测试与组合调用可注入观察端口；产品入口使用默认结构化适配器。 */
  static void execute(String[] args, Consumer<String> progress, LexiconImportObserver observerPort)
      throws Exception {
    final Arguments command;
    try {
      command = Arguments.parse(args);
    } catch (IllegalArgumentException invalidArguments) {
      if (args != null && args.length > 0 && "publish".equals(args[0])) {
        var invalidObservation =
            new LexiconImportObservation(
                observerPort == null
                    ? new LexiconEventObserver(new StructuredEventLogger())
                    : observerPort);
        invalidObservation.terminal(
            LexiconImportObserver.Reason.SOURCE_INVALID,
            java.util.Map.of(),
            java.util.Map.of(),
            null);
      }
      throw invalidArguments;
    }
    var observation =
        command.action().equals("publish")
            ? new LexiconImportObservation(
                observerPort == null
                    ? new LexiconEventObserver(new StructuredEventLogger())
                    : observerPort)
            : null;
    if (observation != null) observation.start(LexiconImportObserver.Step.SOURCE_CHECK);
    try {
      progress.accept("计算来源 SHA-256");
      var digest = sha256(command.input(), observation);
      var stardict = new StardictCsvReader();
      if (stardict.matches(command.input())) {
        executeStardict(command, digest, stardict, progress, observation);
      } else {
        if (observation != null)
          observation.complete(
              LexiconImportObserver.Step.SOURCE_CHECK,
              LexiconImportObserver.Reason.OK,
              java.util.Map.of(),
              java.util.Map.of());
        executeCanonical(command, digest, observation);
      }
    } catch (Exception failure) {
      if (observation != null) {
        var reason =
            failure
                    instanceof
                    io.lexiflow.lexicon.application.importing.LexiconSourceChangedException
                ? LexiconImportObserver.Reason.SOURCE_CHANGED
                : failure instanceof java.util.concurrent.CancellationException
                        || hasCause(failure, java.io.InterruptedIOException.class)
                    ? LexiconImportObserver.Reason.CANCELLED
                    : failure instanceof DataAccessException
                        ? LexiconImportObserver.Reason.DEPENDENCY_UNAVAILABLE
                        : failure instanceof IllegalArgumentException
                            ? LexiconImportObserver.Reason.SOURCE_INVALID
                            : hasCause(failure, IOException.class)
                                ? LexiconImportObserver.Reason.SOURCE_INVALID
                                : LexiconImportObserver.Reason.INTERNAL_ERROR;
        observation.terminal(reason, java.util.Map.of(), java.util.Map.of(), null);
      }
      throw failure;
    }
  }

  private static boolean hasCause(Throwable failure, Class<? extends Throwable> type) {
    for (var cause = failure; cause != null; cause = cause.getCause())
      if (type.isInstance(cause)) return true;
    return false;
  }

  private static void executeCanonical(
      Arguments command, String digest, LexiconImportObservation observation) throws IOException {
    if (command.action().equals("basic-report"))
      throw new IllegalArgumentException("basic-report requires StarDict source evidence");
    var metadata = metadata(command, digest, LexiconCsvReader.PREPARATION_POLICY);
    var source = canonicalSource(command.input(), observation, LexiconImportObserver.Step.PREPARE);
    if (observation != null) observation.start(LexiconImportObserver.Step.PREPARE);
    var inspection =
        LexiconImportPreparation.inspect(
            metadata,
            source,
            command.action().equals("prewarm-report") ? command.limit() : 0,
            observation == null
                ? () -> {}
                : () -> observation.heartbeat(LexiconImportObserver.Step.PREPARE));
    if (command.action().equals("validate")) {
      System.out.printf(
          "PASS format=lexiflow-lexicon-v1 entries=%d source_sha256=%s%n",
          inspection.counts().entries(), digest);
      return;
    }
    if (command.action().equals("prewarm-report")) {
      printPrewarm(inspection.prewarmEntries());
      return;
    }
    inspection.requirePublishable();
    if (observation != null) recordPreparation(observation, inspection);
    if (observation != null) observation.start(LexiconImportObserver.Step.PERSIST);
    try (var persistence = PostgresPersistence.open(command.databaseUrl())) {
      var version =
          new LexiconImportService(persistence.repository())
              .publishStreaming(
                  metadata,
                  inspection.counts().sourceRowsTotal(),
                  inspection.counts().entries(),
                  canonicalSource(command.input(), observation, LexiconImportObserver.Step.PERSIST),
                  observation);
      System.out.printf(
          "PASS format=lexiflow-lexicon-v1 published_version=%d entries=%d source_sha256=%s%n",
          version, inspection.counts().entries(), digest);
    }
  }

  private static void executeStardict(
      Arguments command,
      String digest,
      StardictCsvReader reader,
      Consumer<String> progress,
      LexiconImportObservation observation)
      throws IOException {
    progress.accept("选择基础词");
    var selection = reader.selectBasicVocabulary(command.input());
    if (observation != null)
      observation.complete(
          LexiconImportObserver.Step.SOURCE_CHECK,
          LexiconImportObserver.Reason.OK,
          java.util.Map.of(),
          java.util.Map.of());
    if (command.action().equals("basic-report")) {
      var scan = reader.read(command.input(), source -> {}, selection);
      printStardictScan("PASS", scan, digest);
      System.out.printf(
          "policy=%s list_sha256=%s selected=%d fixed_forms=%d%n",
          StardictCsvReader.PREPARATION_POLICY,
          selection.digest(),
          selection.words().size(),
          io.lexiflow.lexicon.application.importing.validation.BasicVocabulary.fixedWords().size());
      selection
          .words()
          .forEach(word -> System.out.printf("BASIC\t%d\t%s%n", word.rank(), word.lemma()));
      io.lexiflow.lexicon.application.importing.validation.BasicVocabulary.fixedWords().stream()
          .filter(word -> !selection.lemmas().contains(word))
          .sorted()
          .forEach(word -> System.out.printf("FIXED\t%s%n", word));
      return;
    }
    var metadata =
        metadata(command, digest, StardictCsvReader.PREPARATION_POLICY + ":" + selection.digest());
    if (observation != null) observation.start(LexiconImportObserver.Step.PREPARE);
    var preflight =
        inspectStardict(
            reader,
            command,
            metadata,
            selection,
            command.action().equals("prewarm-report") ? command.limit() : 0,
            observation);
    var lastScan = preflight.scan();
    if (command.action().equals("validate")) {
      printStardictScan("PASS", lastScan, digest);
      return;
    }
    if (command.action().equals("prewarm-report")) {
      printPrewarm(preflight.inspection().prewarmEntries());
      return;
    }
    preflight.inspection().requirePublishable();
    if (observation != null) recordPreparation(observation, preflight.inspection());
    progress.accept("来源预检完成，等待事务重读与发布");
    if (observation != null) observation.start(LexiconImportObserver.Step.PERSIST);
    try (var persistence = PostgresPersistence.open(command.databaseUrl())) {
      progress.accept("事务内第二遍读取、批量写入与发布");
      var version =
          new LexiconImportService(persistence.repository())
              .publishStreaming(
                  metadata,
                  preflight.inspection().counts().sourceRowsTotal(),
                  preflight.inspection().counts().entries(),
                  stardictSource(
                      reader,
                      command,
                      selection,
                      new StardictCsvReader.ScanResult[1],
                      observation,
                      LexiconImportObserver.Step.PERSIST),
                  observation);
      System.out.printf(
          "PASS format=ecdict-stardict published_version=%d source_rows=%d entries=%d source_sha256=%s%n",
          version,
          preflight.inspection().counts().sourceRowsTotal(),
          preflight.inspection().counts().entries(),
          digest);
    }
  }

  private static PreparationRun inspectStardict(
      StardictCsvReader reader,
      Arguments command,
      LexiconImportMetadata metadata,
      StardictCsvReader.BasicSelection selection,
      int limit,
      LexiconImportObservation observation) {
    var scan = new StardictCsvReader.ScanResult[1];
    var source =
        stardictSource(
            reader, command, selection, scan, observation, LexiconImportObserver.Step.PREPARE);
    var inspection =
        LexiconImportPreparation.inspect(
            metadata,
            source,
            limit,
            observation == null
                ? () -> {}
                : () -> observation.heartbeat(LexiconImportObserver.Step.PREPARE));
    return new PreparationRun(inspection, scan[0]);
  }

  private static void recordPreparation(
      LexiconImportObservation observation, LexiconImportPreparation.Inspection inspection) {
    var stats = inspection.statistics();
    observation.prepared(stats);
    observation.complete(
        LexiconImportObserver.Step.PREPARE,
        LexiconImportObserver.Reason.OK,
        java.util.Map.of(
            LexiconImportObserver.Count.INPUT_ROWS,
            stats.inputRows(),
            LexiconImportObserver.Count.PREPARED_ROWS,
            stats.preparedRows(),
            LexiconImportObserver.Count.HINT_ROWS,
            stats.hintRows(),
            LexiconImportObserver.Count.BLOCKED_ROWS,
            stats.blockedRows()),
        java.util.Map.of());
  }

  private static LexiconImportRowSource stardictSource(
      StardictCsvReader reader,
      Arguments command,
      StardictCsvReader.BasicSelection selection,
      StardictCsvReader.ScanResult[] scan,
      LexiconImportObservation observation,
      LexiconImportObserver.Step heartbeatStep) {
    return consumer -> {
      var actual = reader.read(command.input(), record -> consumer.accept(record.row()), selection);
      if (observation != null) observation.heartbeat(heartbeatStep);
      scan[0] = actual;
      return new LexiconImportRowSource.ReadReceipt(
          sourceDigest(command.input()), actual.sourceRows());
    };
  }

  /**
   * 关联应用预检结果与本次来源格式统计，仅用于 CLI 输出。
   *
   * @param inspection 含义：已完成身份及准备校验的结果。取值范围：非 null。
   * @param scan 含义：来源适配器实际读取的格式统计。取值范围：非 null。
   */
  private record PreparationRun(
      LexiconImportPreparation.Inspection inspection, StardictCsvReader.ScanResult scan) {}

  private static LexiconImportRowSource canonicalSource(
      Path input, LexiconImportObservation observation, LexiconImportObserver.Step heartbeatStep) {
    return consumer -> {
      var rows = new LexiconCsvReader().read(input);
      if (observation != null) observation.heartbeat(heartbeatStep);
      rows.forEach(consumer);
      return new LexiconImportRowSource.ReadReceipt(sourceDigest(input), rows.size());
    };
  }

  private static LexiconImportMetadata metadata(
      Arguments command, String digest, String preparation) {
    return new LexiconImportMetadata(
        digest,
        (command.action().equals("publish")
            ? command.batchSourceId() + ":" + preparation
            : "preview:" + preparation),
        (command.action().equals("publish") ? command.batchLicenseId() : "unasserted-preview-only"),
        command.acquiredAt());
  }

  private static void printStardictScan(
      String status, StardictCsvReader.ScanResult scan, String digest) {
    System.out.printf(
        "导入校验结果：%s%n"
            + "文件格式（format）：ecdict-stardict%n"
            + "来源行数（source_rows）：%d%n"
            + "可导入词条数（entries）：%d%n"
            + "归并派生词数（derived_merged）：%d%n"
            + "缺少释义跳过数（omitted_no_gloss）：%d%n"
            + "不支持词形跳过数（omitted_unsupported_surface）：%d%n"
            + "来源基础词命中数（selected_basic）：%d%n"
            + "合并固定词后的基础词行数（basic_rows）：%d%n"
            + "去重释义行数（deduplicated_gloss_rows）：%d%n"
            + "来源文件SHA-256（source_sha256）：%s%n",
        status,
        scan.sourceRows(),
        scan.importableRows(),
        scan.derivedRows(),
        scan.noGlossRows(),
        scan.unsupportedSurfaceRows(),
        scan.selectedBasicLemmas(),
        scan.basicRows(),
        scan.deduplicatedRows(),
        digest);
  }

  private static void printPrewarm(List<LexiconImportPlan.PlannedEntry> plan) {
    plan.forEach(
        value ->
            System.out.printf(
                "%d\t%.2f\t%d\t%s%n",
                value.entry().priority().memoryPriority(),
                value.entry().priority().frequencyZipf(),
                value.entry().priority().complexListCount(),
                value.entry().lemma()));
  }

  private static String sourceDigest(Path input) throws IOException {
    try {
      return sha256(input);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException(exception);
    }
  }

  private static String sha256(Path input) throws IOException, NoSuchAlgorithmException {
    return sha256(input, null);
  }

  private static String sha256(Path input, LexiconImportObservation observation)
      throws IOException, NoSuchAlgorithmException {
    var digest = MessageDigest.getInstance("SHA-256");
    try (InputStream stream = Files.newInputStream(input)) {
      var buffer = new byte[8192];
      for (int read; (read = stream.read(buffer)) != -1; ) {
        digest.update(buffer, 0, read);
        if (observation != null) observation.heartbeat(LexiconImportObserver.Step.SOURCE_CHECK);
      }
    }
    return HexFormat.of().formatHex(digest.digest());
  }

  /**
   * 命令行解析后的受限参数集合。
   *
   * @param action 含义：要执行的动作。取值范围：由方法调用前置条件限定。
   * @param input 含义：来源 CSV 路径。取值范围：由方法调用前置条件限定。
   * @param databaseUrl 含义：发布使用的 JDBC 地址。取值范围：由方法调用前置条件限定。
   * @param batchSourceId 含义：发布来源标识。取值范围：由方法调用前置条件限定。
   * @param batchLicenseId 含义：发布许可证标识。取值范围：由方法调用前置条件限定。
   * @param acquiredAt 含义：来源获取时刻。取值范围：由方法调用前置条件限定。
   * @param limit 含义：报告或预热候选上限。取值范围：由方法调用前置条件限定。
   */
  private record Arguments(
      String action,
      Path input,
      String databaseUrl,
      String batchSourceId,
      String batchLicenseId,
      Instant acquiredAt,
      int limit) {
    static Arguments parse(String[] args) {
      if (args.length < 2)
        throw new IllegalArgumentException(
            "usage: <validate|basic-report|prewarm-report|publish> --input <path> [options]");
      var action = args[0];
      if (!List.of("validate", "basic-report", "prewarm-report", "publish").contains(action))
        throw new IllegalArgumentException(
            "action must be validate, basic-report, prewarm-report or publish");
      var values = new java.util.HashMap<String, String>();
      for (var index = 1; index < args.length; index += 2) {
        if (!args[index].startsWith("--") || index + 1 >= args.length)
          throw new IllegalArgumentException("options must use --name value");
        if (values.put(args[index], args[index + 1]) != null)
          throw new IllegalArgumentException("option is repeated: " + args[index]);
      }
      var input = Path.of(required(values, "--input"));
      var acquiredAt = Instant.parse(values.getOrDefault("--acquired-at", "1970-01-01T00:00:00Z"));
      var limit = Integer.parseInt(values.getOrDefault("--limit", "2000"));
      if (limit < 1 || limit > 200_000)
        throw new IllegalArgumentException("--limit must be between 1 and 200000");
      if (!action.equals("publish")) {
        rejectUnexpected(values, List.of("--input", "--acquired-at", "--limit"));
        return new Arguments(action, input, "", "", "", acquiredAt, limit);
      }
      rejectUnexpected(
          values,
          List.of(
              "--input",
              "--acquired-at",
              "--limit",
              "--database-url",
              "--batch-source-id",
              "--batch-license-id"));
      return new Arguments(
          action,
          input,
          required(values, "--database-url"),
          required(values, "--batch-source-id"),
          required(values, "--batch-license-id"),
          acquiredAt,
          limit);
    }

    private static String required(java.util.Map<String, String> values, String name) {
      var value = values.get(name);
      if (value == null || value.isBlank())
        throw new IllegalArgumentException(name + " is required");
      return value;
    }

    private static void rejectUnexpected(
        java.util.Map<String, String> values, List<String> allowed) {
      if (!allowed.containsAll(values.keySet()))
        throw new IllegalArgumentException("an unsupported option was supplied");
    }
  }
}
