package io.lexiflow.api.release;

import io.lexiflow.lexicon.platform.persistence.DatasetPackageCodec;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetExport;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetPackage;
import io.lexiflow.lexicon.platform.persistence.SqlitePersistence;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
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
                SqliteDatasetExport.export(
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
          require(options, "--package", "--expected-sha256", "--expected-file");
          Path databaseFile = Path.of(options.get("--expected-file")).toAbsolutePath().normalize();
          String url = System.getenv("LEXIFLOW_RELEASE_JDBC_URL");
          if (url == null || !url.equals("jdbc:sqlite:" + databaseFile))
            throw new IllegalArgumentException();
          if (!databaseFile.toString().equals(options.get("--expected-file")))
            throw new IllegalArgumentException();
          rejectSymlinkPath(databaseFile);
          Path packagePath = Path.of(options.get("--package"));
          Object identity = null;
          try (var prepared =
              SqliteDatasetPackage.prepare(
                  packagePath, options.get("--expected-sha256"), trustedSql)) {
            if (!Files.exists(databaseFile, LinkOption.NOFOLLOW_LINKS)) {
              identity = createExpectedFile(databaseFile);
            }
            try (var connection = SqlitePersistence.connect(url)) {
              prepared.initialize(connection, databaseFile.toString());
            }
          } catch (Exception failure) {
            if (identity != null && identity.equals(fileKey(databaseFile)))
              Files.deleteIfExists(databaseFile);
            throw failure;
          }
          System.out.println("{\"result\":\"PASS\"}");
        }
        default -> throw new IllegalArgumentException();
      }
      return 0;
    } catch (Exception ignored) {
      System.out.println("{\"result\":\"FAIL\"}");
      return 1;
    }
  }

  /** 以 CREATE_NEW 原子占有预期文件；目标已被创建时绝不打开或覆盖。 */
  static Object createExpectedFile(Path path) throws IOException {
    Files.createFile(path);
    return fileKey(path);
  }

  private static Object fileKey(Path path) throws IOException {
    Object key =
        Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS).fileKey();
    if (key == null) throw new IOException("database file identity unavailable");
    return key;
  }

  private static void rejectSymlinkPath(Path path) throws IOException {
    if (!path.isAbsolute()) throw new IOException();
    Path current = path.getRoot();
    for (Path part : path) {
      current = current.resolve(part);
      if (Files.isSymbolicLink(current)) throw new IOException();
    }
  }

  private static java.sql.Connection databaseConnection() throws Exception {
    String url = System.getenv("LEXIFLOW_RELEASE_JDBC_URL");
    if (url == null || url.isBlank()) throw new IllegalArgumentException();
    return SqlitePersistence.connect(url);
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
