package io.lexiflow.api.release;

import io.lexiflow.lexicon.platform.persistence.DatasetPackageCodec;
import io.lexiflow.lexicon.platform.persistence.PostgresDatasetExport;
import io.lexiflow.lexicon.platform.persistence.PostgresDatasetPackage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.HashMap;
import java.util.Map;

/** API JAR 的一次性资料包管理命令，不装配 HTTP 或后台任务。 */
public final class ReleaseDatasetCommand {
  private ReleaseDatasetCommand() {}

  /**
   * 固定输出，不泄露路径、连接地址、来源或异常文本。
   *
   * @param args 含义：固定管理模式及显式键值参数。取值范围：命令行原始参数。
   * @return 成功时 0，任何失败时 1。
   */
  public static int run(String[] args) {
    try {
      if (args.length < 2 || !"--release-dataset".equals(args[0]))
        throw new IllegalArgumentException();
      String action = args[1];
      Map<String, String> options = parse(args, 2);
      byte[] trustedSql = trustedSql();
      switch (action) {
        case "verify" -> {
          require(options, "--package", "--expected-sha256");
          DatasetPackageCodec.verify(
              Path.of(options.get("--package")), options.get("--expected-sha256"), trustedSql);
          System.out.println("{\"result\":\"PASS\"}");
        }
        case "export" -> {
          require(options, "--output", "--approval-file");
          try (var connection = databaseConnection()) {
            var result =
                PostgresDatasetExport.export(
                    connection,
                    Path.of(options.get("--output")),
                    Path.of(options.get("--approval-file")),
                    trustedSql);
            System.out.println(
                "{\"result\":\"PASS\",\"sha256\":\""
                    + result.sha256()
                    + "\",\"datasetVersion\":"
                    + result.datasetVersion()
                    + "}");
          }
        }
        case "initialize" -> {
          require(
              options,
              "--package",
              "--expected-sha256",
              "--expected-database",
              "--expected-schema");
          try (var connection = databaseConnection()) {
            PostgresDatasetPackage.initialize(
                connection,
                Path.of(options.get("--package")),
                options.get("--expected-sha256"),
                options.get("--expected-database"),
                options.get("--expected-schema"),
                trustedSql);
            System.out.println("{\"result\":\"PASS\"}");
          }
        }
        default -> throw new IllegalArgumentException();
      }
      return 0;
    } catch (Exception ignored) {
      System.out.println("{\"result\":\"FAIL\"}");
      return 1;
    }
  }

  private static java.sql.Connection databaseConnection() throws Exception {
    String url = System.getenv("LEXIFLOW_RELEASE_JDBC_URL");
    if (url == null || url.isBlank()) throw new IllegalArgumentException();
    return DriverManager.getConnection(url);
  }

  private static byte[] trustedSql() throws IOException {
    try (InputStream input =
        ReleaseDatasetCommand.class.getResourceAsStream("/META-INF/lexiflow-schema.sql")) {
      if (input == null) throw new IOException("unavailable");
      return input.readAllBytes();
    }
  }

  private static Map<String, String> parse(String[] args, int start) {
    var values = new HashMap<String, String>();
    for (int i = start; i < args.length; i += 2) {
      if (i + 1 >= args.length
          || !args[i].startsWith("--")
          || args[i + 1].isBlank()
          || args[i + 1].startsWith("--")
          || values.putIfAbsent(args[i], args[i + 1]) != null) throw new IllegalArgumentException();
    }
    return values;
  }

  private static void require(Map<String, String> options, String... required) {
    if (options.size() != required.length) throw new IllegalArgumentException();
    for (String key : required) if (!options.containsKey(key)) throw new IllegalArgumentException();
  }
}
