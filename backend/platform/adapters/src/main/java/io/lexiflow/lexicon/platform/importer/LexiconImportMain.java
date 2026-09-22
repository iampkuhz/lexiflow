package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.LexiconImportMetadata;
import io.lexiflow.lexicon.application.LexiconImportPlan;
import io.lexiflow.lexicon.application.LexiconImportRequest;
import io.lexiflow.lexicon.application.LexiconImportService;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistence;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.PriorityQueue;

/** 离线词库导入命令；只解析本机受控输入并调用应用层导入服务。 */
public final class LexiconImportMain {
  private static final int CHUNK_SIZE = 500;

  private LexiconImportMain() {}

  /**
   * 执行 `validate`、`prewarm-report` 或显式 `publish`。
   *
   * @param args 含义：受控命令行参数。取值范围：由方法调用前置条件限定。
   */
  public static void main(String[] args) {
    try {
      var command = Arguments.parse(args);
      var digest = sha256(command.input());
      var stardict = new StardictCsvReader();
      if (stardict.matches(command.input())) {
        executeStardict(command, digest, stardict);
      } else {
        executeCanonical(command, digest);
      }
    } catch (Exception exception) {
      System.err.println("FAIL " + exception.getMessage());
      System.exit(1);
    }
  }

  private static void executeCanonical(Arguments command, String digest) throws IOException {
    var rows = new LexiconCsvReader().read(command.input());
    var metadata = metadata(command, digest);
    var plan = LexiconImportPlan.prepare(rows, 1, digest, command.acquiredAt());
    if (command.action().equals("validate")) {
      System.out.printf(
          "PASS format=lexiflow-lexicon-v1 entries=%d source_sha256=%s%n", plan.size(), digest);
      return;
    }
    if (command.action().equals("prewarm-report")) {
      printPrewarm(plan, command.limit());
      return;
    }
    try (var persistence = PostgresPersistence.open(command.databaseUrl())) {
      var version =
          new LexiconImportService(persistence.repository())
              .publish(new LexiconImportRequest(rows, metadata));
      System.out.printf(
          "PASS format=lexiflow-lexicon-v1 published_version=%d entries=%d source_sha256=%s%n",
          version, plan.size(), digest);
    }
  }

  private static void executeStardict(Arguments command, String digest, StardictCsvReader reader)
      throws IOException {
    if (command.action().equals("validate")) {
      var canonicalSurfaces = LexiconImportPlan.canonicalSurfaceValidator();
      var scan =
          reader.read(
              command.input(),
              source ->
                  LexiconImportPlan.prepareNext(
                      source.row(), 1, digest, command.acquiredAt(), canonicalSurfaces));
      printStardictScan("PASS", scan, digest);
      return;
    }
    if (command.action().equals("prewarm-report")) {
      var top = topStardictEntries(reader, command.input(), command.limit());
      printPrewarm(
          top.stream()
              .map(value -> LexiconImportPlan.fromRow(value.row(), 1, digest, command.acquiredAt()))
              .toList(),
          command.limit());
      return;
    }
    var metadata = metadata(command, digest);
    try (var persistence = PostgresPersistence.open(command.databaseUrl())) {
      var service = new LexiconImportService(persistence.repository());
      var batch = service.openOrResume(metadata);
      var buffer = new ArrayList<StardictCsvReader.SourceRecord>(CHUNK_SIZE);
      var scan =
          reader.read(
              command.input(),
              source -> {
                if (source.sourceRow() <= batch.sourceRowsProcessed()) {
                  return;
                }
                buffer.add(source);
                if (buffer.size() == CHUNK_SIZE) {
                  stage(service, batch, buffer, source.sourceRow(), metadata);
                  buffer.clear();
                }
              });
      if (!buffer.isEmpty()) {
        stage(service, batch, buffer, buffer.getLast().sourceRow(), metadata);
      }
      if (scan.importableRows() == 0) {
        throw new IllegalArgumentException("StarDict source contains no importable entries");
      }
      service.publish(batch, scan.sourceRows());
      System.out.printf(
          "PASS format=ecdict-stardict published_version=%d source_rows=%d entries=%d source_sha256=%s%n",
          batch.version(), scan.sourceRows(), scan.importableRows(), digest);
    }
  }

  private static void stage(
      LexiconImportService service,
      io.lexiflow.lexicon.application.StagedLexiconImport batch,
      List<StardictCsvReader.SourceRecord> records,
      long processedThrough,
      LexiconImportMetadata metadata) {
    service.stage(
        batch,
        records.stream().map(StardictCsvReader.SourceRecord::row).toList(),
        processedThrough,
        metadata);
  }

  private static LexiconImportMetadata metadata(Arguments command, String digest) {
    return new LexiconImportMetadata(
        digest, command.batchSourceId(), command.batchLicenseId(), command.acquiredAt());
  }

  private static List<StardictCsvReader.SourceRecord> topStardictEntries(
      StardictCsvReader reader, Path input, int limit) throws IOException {
    var lowestFirst =
        Comparator.comparingInt(
                (StardictCsvReader.SourceRecord value) -> value.row().priority().memoryPriority())
            .thenComparing(value -> value.row().lemma(), Comparator.reverseOrder());
    var top = new PriorityQueue<StardictCsvReader.SourceRecord>(limit, lowestFirst);
    reader.read(
        input,
        value -> {
          if (!value.row().prewarmEligible() || value.row().priority().memoryPriority() == 0)
            return;
          if (top.size() < limit) top.add(value);
          else if (lowestFirst.compare(value, top.peek()) > 0) {
            top.remove();
            top.add(value);
          }
        });
    return top.stream()
        .sorted(
            Comparator.comparingInt(
                    (StardictCsvReader.SourceRecord value) ->
                        value.row().priority().memoryPriority())
                .reversed()
                .thenComparing(value -> value.row().lemma()))
        .toList();
  }

  private static void printStardictScan(
      String status, StardictCsvReader.ScanResult scan, String digest) {
    System.out.printf(
        "%s format=ecdict-stardict source_rows=%d entries=%d derived_merged=%d omitted_no_gloss=%d omitted_unsupported_surface=%d source_sha256=%s%n",
        status,
        scan.sourceRows(),
        scan.importableRows(),
        scan.derivedRows(),
        scan.noGlossRows(),
        scan.unsupportedSurfaceRows(),
        digest);
  }

  private static void printPrewarm(List<LexiconImportPlan.PlannedEntry> plan, int limit) {
    plan.stream()
        .filter(value -> value.row().prewarmEligible())
        .sorted(
            Comparator.comparingInt(
                    (LexiconImportPlan.PlannedEntry value) ->
                        value.entry().priority().memoryPriority())
                .reversed()
                .thenComparing(value -> value.entry().lemma()))
        .limit(limit)
        .forEach(
            value ->
                System.out.printf(
                    "%d\t%.2f\t%d\t%s%n",
                    value.entry().priority().memoryPriority(),
                    value.entry().priority().frequencyZipf(),
                    value.entry().priority().complexListCount(),
                    value.entry().lemma()));
  }

  private static String sha256(Path input) throws IOException, NoSuchAlgorithmException {
    var digest = MessageDigest.getInstance("SHA-256");
    try (InputStream stream = Files.newInputStream(input)) {
      var buffer = new byte[8192];
      for (int read; (read = stream.read(buffer)) != -1; ) digest.update(buffer, 0, read);
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
            "usage: <validate|prewarm-report|publish> --input <path> [options]");
      var action = args[0];
      if (!List.of("validate", "prewarm-report", "publish").contains(action))
        throw new IllegalArgumentException("action must be validate, prewarm-report or publish");
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
