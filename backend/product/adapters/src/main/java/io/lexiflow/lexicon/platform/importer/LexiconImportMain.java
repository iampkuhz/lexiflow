package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.LexiconImportPlan;
import io.lexiflow.lexicon.application.importing.LexiconImportService;
import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRequest;
import io.lexiflow.lexicon.platform.persistence.PostgresPersistence;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.PriorityQueue;

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
    if (command.action().equals("basic-report"))
      throw new IllegalArgumentException("basic-report requires StarDict source evidence");
    var rows = new LexiconCsvReader().read(command.input());
    var metadata = metadata(command, digest, "fixed-function-words-and-identical-gloss-v1");
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
    var selection = reader.selectBasicVocabulary(command.input());
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
    if (command.action().equals("validate")) {
      var scan = validateStardict(reader, command, digest, selection);
      printStardictScan("PASS", scan, digest);
      return;
    }
    if (command.action().equals("prewarm-report")) {
      var top = topStardictEntries(reader, command.input(), command.limit(), selection);
      printPrewarm(
          top.stream()
              .map(value -> LexiconImportPlan.fromRow(value.row(), 1, digest, command.acquiredAt()))
              .toList(),
          command.limit());
      return;
    }
    var metadata =
        metadata(command, digest, StardictCsvReader.PREPARATION_POLICY + ":" + selection.digest());
    var preflight = validateStardict(reader, command, digest, selection);
    if (preflight.importableRows() == 0) {
      throw new IllegalArgumentException("StarDict source contains no importable entries");
    }
    if (!digest.equals(sourceDigest(command.input()))) {
      throw new IllegalStateException("source changed during preflight");
    }
    try (var persistence = PostgresPersistence.open(command.databaseUrl())) {
      var version =
          new LexiconImportService(persistence.repository())
              .publishStreaming(
                  metadata,
                  preflight.sourceRows(),
                  preflight.importableRows(),
                  consumer -> {
                    var second =
                        reader.read(
                            command.input(), record -> consumer.accept(record.row()), selection);
                    if (second.sourceRows() != preflight.sourceRows()
                        || second.importableRows() != preflight.importableRows()
                        || !digest.equals(sourceDigest(command.input()))) {
                      throw new IllegalStateException("source changed during publication");
                    }
                  });
      System.out.printf(
          "PASS format=ecdict-stardict published_version=%d source_rows=%d entries=%d source_sha256=%s%n",
          version, preflight.sourceRows(), preflight.importableRows(), digest);
    }
  }

  private static StardictCsvReader.ScanResult validateStardict(
      StardictCsvReader reader,
      Arguments command,
      String digest,
      StardictCsvReader.BasicSelection selection)
      throws IOException {
    var canonicalSurfaces = LexiconImportPlan.canonicalSurfaceValidator();
    return reader.read(
        command.input(),
        source ->
            LexiconImportPlan.prepareNext(
                source.row(), 1, digest, command.acquiredAt(), canonicalSurfaces),
        selection);
  }

  private static LexiconImportMetadata metadata(
      Arguments command, String digest, String preparation) {
    return new LexiconImportMetadata(
        digest,
        command.batchSourceId() + ":" + preparation,
        command.batchLicenseId(),
        command.acquiredAt());
  }

  private static List<StardictCsvReader.SourceRecord> topStardictEntries(
      StardictCsvReader reader, Path input, int limit, StardictCsvReader.BasicSelection selection)
      throws IOException {
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
        },
        selection);
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

  private static String sourceDigest(Path input) throws IOException {
    try {
      return sha256(input);
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException(exception);
    }
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
