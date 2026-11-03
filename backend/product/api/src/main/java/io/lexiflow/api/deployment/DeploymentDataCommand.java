package io.lexiflow.api.deployment;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.platform.importer.LexiconImportMain;
import io.lexiflow.lexicon.platform.persistence.DatasetPackageCodec;
import io.lexiflow.lexicon.platform.persistence.GenerationIdentityReader;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetPackage;
import io.lexiflow.lexicon.platform.persistence.SqliteDatasetVerifier;
import io.lexiflow.lexicon.platform.persistence.SqlitePersistence;
import io.lexiflow.lexicon.platform.persistence.SqliteSchemaInitializer;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.SQLException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import javax.sql.DataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.core.json.JsonFactory;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 一次性部署数据 inspect/apply 接缝；不装配 HTTP 或后台任务。 */
public final class DeploymentDataCommand {
  private static final ObjectMapper JSON =
      new ObjectMapper(
          JsonFactory.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build());
  private static final Set<String> OWNED_OBJECTS =
      Set.of(
          "lexicon_dataset",
          "lexicon_entry",
          "lexicon_form",
          "lexicon_lookup",
          "lexicon_form_entry_idx",
          "lexicon_entry_prewarm_hint_idx",
          "lexicon_entry_prewarm_block_idx",
          "lexicon_form_canonical_guard",
          "lexicon_form_canonical_update_guard",
          "lexicon_entry_canonical_update_guard");

  private DeploymentDataCommand() {}

  /**
   * 运行受限部署数据动作，仅输出单行固定结构结果。
   *
   * @param args 含义：管理动作及其参数；取值范围：规定的检查或应用动作及必需的请求文件与预期目标
   */
  public static int run(String[] args) {
    return run(args, System.getenv("LEXIFLOW_DEPLOYMENT_JDBC_URL"));
  }

  static int run(String[] args, String configuredJdbcUrl) {
    return run(args, configuredJdbcUrl, null);
  }

  static int run(String[] args, String configuredJdbcUrl, byte[] testSchemaOverride) {
    String status = "FAIL", reason = "REQUEST_INVALID";
    try {
      if (args.length != 6
          || !"--deployment-data".equals(args[0])
          || !("inspect".equals(args[1]) || "apply".equals(args[1]))
          || !"--request".equals(args[2])
          || !"--expected-file".equals(args[4])) throw new IllegalArgumentException();
      Path request = Path.of(args[3]), databaseFile = Path.of(args[5]);
      rejectSymlinkPath(request);
      rejectAbsoluteDatabasePath(databaseFile);
      if (!Files.isRegularFile(request, LinkOption.NOFOLLOW_LINKS)
          || Files.size(request) > 64 * 1024) throw new IllegalArgumentException();
      JsonNode root = JSON.readTree(Files.readAllBytes(request));
      byte[] trustedSql = schemaSql();
      boolean apply = "apply".equals(args[1]);
      validateRequest(root, apply);
      verifyGeneration(root.get("generation"));
      Path input = Path.of(text(root, "inputPath"));
      String inputSha = text(root, "inputSha256");
      if (!input.isAbsolute() || !sha256(input).equals(inputSha))
        throw new IllegalArgumentException();
      if ("csv".equals(text(root, "inputType"))) {
        if (!inputSha.equals(text(root.get("generation"), "inputSha256")))
          throw new IllegalArgumentException();
        LexiconImportMain.validateDeploymentInput(
            input, inputSha, generation(root.get("generation")));
      } else {
        var manifest = DatasetPackageCodec.verifiedManifest(input, inputSha, trustedSql);
        var wanted = generation(root.get("generation"));
        if (!manifest.generationVersion().equals(wanted.generationVersion())
            || !manifest.generationCodeVersion().equals(wanted.codeVersion())
            || !manifest.generationSourceDigest().equals(wanted.sourceDigest())
            || !JSON.readTree(manifest.generationConfigJson())
                .equals(JSON.readTree(wanted.configJson()))
            || !JSON.readTree(manifest.generationInputsJson())
                .equals(JSON.readTree(wanted.inputsJson()))) throw new IllegalArgumentException();
      }
      String jdbcUrl = deploymentUrl(configuredJdbcUrl, databaseFile);
      Snapshot before = inspect(databaseFile, jdbcUrl, trustedSql);
      before = requireGeneration(before, text(root.get("generation"), "generationVersion"));
      if (!apply) {
        System.out.println(inspectResult(before));
        return 0;
      }
      if (!text(root, "expectedFingerprint").equals(before.fingerprint())) {
        System.out.println(result("BLOCKED", "FINGERPRINT_DRIFT"));
        return 3;
      }
      if (!before.needsRebuild()) {
        System.out.println(
            "{\"status\":\"PASS\",\"rebuilt\":false,\"generationVersion\":\""
                + before.generationVersion()
                + "\",\"datasetVersion\":"
                + before.datasetVersion()
                + "}");
        return 0;
      }
      Object createdIdentity = null;
      try {
        if (!Files.exists(databaseFile, LinkOption.NOFOLLOW_LINKS))
          createdIdentity = createEmptyDatabaseFile(databaseFile);
        long version =
            apply(
                jdbcUrl,
                databaseFile,
                root,
                input,
                inputSha,
                before,
                createdIdentity,
                trustedSql,
                testSchemaOverride);
        System.out.println(
            "{\"status\":\"PASS\",\"rebuilt\":true,\"generationVersion\":\""
                + text(root.get("generation"), "generationVersion")
                + "\",\"datasetVersion\":"
                + version
                + "}");
        return 0;
      } catch (Exception failure) {
        if (createdIdentity != null) removeCreatedDatabaseFile(databaseFile, createdIdentity);
        throw failure;
      }
    } catch (Blocked e) {
      status = "BLOCKED";
      reason = e.reason;
    } catch (SQLException e) {
      status = "BLOCKED";
      reason = "DATABASE_UNAVAILABLE";
    } catch (Exception e) {
      status = "FAIL";
      reason = "REQUEST_OR_DATA_INVALID";
    }
    System.out.println(result(status, reason));
    return "BLOCKED".equals(status) ? 3 : 1;
  }

  private static long apply(
      String url,
      Path file,
      JsonNode root,
      Path input,
      String inputSha,
      Snapshot before,
      Object createdIdentity,
      byte[] trustedSql,
      byte[] schemaOverride)
      throws Exception {
    // 只有显式授权应用后才打开目标；同一事务覆盖本项目对象删除、最新结构重建和资料发布。
    try (Connection connection = SqlitePersistence.connect(url);
        var source = new SingleConnectionDataSource(connection, true)) {
      assertOwnedObjects(connection);
      long floor = Math.max(before.datasetVersion() == null ? 0 : before.datasetVersion(), 0);
      var manager = new DataSourceTransactionManager(source);
      var txTemplate = new TransactionTemplate(manager);
      var repository = SqlitePersistence.repository((DataSource) source, floor);
      Long value =
          txTemplate.execute(
              status -> {
                try {
                  if ("FILE_MISSING".equals(before.reason())) {
                    if (createdIdentity == null || !createdIdentity.equals(fileIdentity(file)))
                      throw new Blocked("FINGERPRINT_DRIFT");
                    assertOwnedObjects(connection);
                    if (scalar(
                            connection,
                            "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
                        != 0) throw new Blocked("FINGERPRINT_DRIFT");
                  } else {
                    Snapshot current = inspect(connection, file, trustedSql);
                    if (!before.fingerprint().equals(current.fingerprint()))
                      throw new Blocked("FINGERPRINT_DRIFT");
                    assertOwnedObjects(connection);
                  }
                  try (var statement = connection.createStatement()) {
                    statement.execute("DROP VIEW IF EXISTS lexicon_lookup");
                    statement.execute("DROP TABLE IF EXISTS lexicon_form");
                    statement.execute("DROP TABLE IF EXISTS lexicon_entry");
                    statement.execute("DROP TABLE IF EXISTS lexicon_dataset");
                  }
                  byte[] sql = schemaOverride == null ? trustedSql : schemaOverride;
                  if ("package".equals(text(root, "inputType"))) {
                    SqliteDatasetPackage.initializeWithinTransaction(
                        connection, input, inputSha, file.toString(), sql);
                    long packageVersion =
                        scalar(
                            connection,
                            "SELECT lexicon_version FROM lexicon_dataset WHERE dataset_id=1");
                    long newVersion =
                        Math.max(packageVersion, Math.max(floor + 1, System.currentTimeMillis()));
                    try (var update =
                        connection.prepareStatement(
                            "UPDATE lexicon_dataset SET lexicon_version=? WHERE dataset_id=1")) {
                      update.setLong(1, newVersion);
                      update.executeUpdate();
                    }
                  } else {
                    SqliteSchemaInitializer.executeTrustedSql(connection, sql);
                    var identity = generation(root.get("generation"));
                    LexiconImportMain.publishOnRepository(
                        input,
                        inputSha,
                        "ecdict-stardict",
                        "MIT",
                        Instant.EPOCH,
                        identity,
                        repository);
                  }
                  var publication = SqliteDatasetVerifier.verify(connection, trustedSql);
                  return publication.datasetVersion();
                } catch (Blocked blocked) {
                  status.setRollbackOnly();
                  throw blocked;
                } catch (Exception failure) {
                  status.setRollbackOnly();
                  throw new IllegalStateException(failure);
                }
              });
      if (value == null) throw new IllegalStateException("no version returned");
      return value;
    }
  }

  static Snapshot inspect(Path file, String url, byte[] trustedSql) throws Exception {
    if (!Files.exists(file, LinkOption.NOFOLLOW_LINKS))
      return new Snapshot(
          file.toAbsolutePath().normalize().toString(),
          fingerprint(file, "missing"),
          true,
          "FILE_MISSING",
          null,
          null);
    rejectAbsoluteDatabasePath(file);
    try (Connection connection = SqlitePersistence.connect(url)) {
      return inspect(connection, file, trustedSql);
    }
  }

  private static Snapshot inspect(Connection connection, Path file, byte[] trustedSql)
      throws Exception {
    assertOwnedObjects(connection);
    try {
      var publication = SqliteDatasetVerifier.verify(connection, trustedSql);
      return new Snapshot(
          file.toAbsolutePath().normalize().toString(),
          fingerprint(file, publication.generationVersion() + ":" + publication.datasetVersion()),
          false,
          "OK",
          publication.generationVersion(),
          publication.datasetVersion());
    } catch (IOException | IllegalStateException invalid) {
      Long version = null;
      if (tableExists(connection, "lexicon_dataset")
          && columnExists(connection, "lexicon_dataset", "lexicon_version"))
        version =
            scalar(connection, "SELECT COALESCE(MAX(lexicon_version),0) FROM lexicon_dataset");
      return new Snapshot(
          file.toAbsolutePath().normalize().toString(),
          fingerprint(file, "invalid:" + version),
          true,
          "DATA_INVALID",
          null,
          version);
    }
  }

  private static Snapshot requireGeneration(Snapshot snapshot, String requested) {
    if (!snapshot.needsRebuild() && !requested.equals(snapshot.generationVersion()))
      return new Snapshot(
          snapshot.databaseFile(),
          snapshot.fingerprint(),
          true,
          "GENERATION_CHANGED",
          snapshot.generationVersion(),
          snapshot.datasetVersion());
    return snapshot;
  }

  private static Object createEmptyDatabaseFile(Path file) throws IOException {
    rejectAbsoluteDatabasePath(file);
    Path parent = file.toAbsolutePath().normalize().getParent();
    if (parent == null || !Files.isDirectory(parent, LinkOption.NOFOLLOW_LINKS))
      throw new IOException("database parent is unavailable");
    Path created = Files.createFile(file.toAbsolutePath().normalize());
    return fileIdentity(created);
  }

  private static Object fileIdentity(Path file) throws IOException {
    var attributes =
        Files.readAttributes(
            file, java.nio.file.attribute.BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
    if (attributes.fileKey() == null)
      throw new IOException("database file identity is unavailable");
    return attributes.fileKey();
  }

  private static void removeCreatedDatabaseFile(Path file, Object identity) {
    try {
      if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(file))
        return;
      if (identity != null && identity.equals(fileIdentity(file))) Files.deleteIfExists(file);
    } catch (IOException ignored) {
      // 清理失败也不扩大删除范围；无法证明文件身份时保留现场。
    }
  }

  private static void assertOwnedObjects(Connection c) throws SQLException {
    try (var s = c.createStatement();
        var r =
            s.executeQuery(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND type IN"
                    + " ('table','view','index','trigger') ORDER BY name")) {
      while (r.next())
        if (!OWNED_OBJECTS.contains(r.getString(1))) throw new Blocked("UNKNOWN_DATABASE_OBJECT");
    }
    boolean temporaryPresent = false;
    try (var s = c.createStatement();
        var r = s.executeQuery("PRAGMA database_list")) {
      while (r.next()) {
        String name = r.getString("name");
        if ("temp".equals(name)) temporaryPresent = true;
        else if (!"main".equals(name)) throw new Blocked("ATTACHED_DATABASE");
      }
    }
    if (temporaryPresent) {
      try (var s = c.createStatement();
          var r =
              s.executeQuery(
                  "SELECT 1 FROM sqlite_temp_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1")) {
        if (r.next()) throw new Blocked("UNKNOWN_DATABASE_OBJECT");
      }
    }
  }

  private static String fingerprint(Path file, String detail) throws Exception {
    var normalized = file.toAbsolutePath().normalize();
    Path anchor = normalized;
    if (!Files.exists(anchor, LinkOption.NOFOLLOW_LINKS)) anchor = anchor.getParent();
    while (anchor != null && !Files.exists(anchor, LinkOption.NOFOLLOW_LINKS))
      anchor = anchor.getParent();
    Object fileKey =
        anchor == null
            ? "root"
            : Files.readAttributes(
                    anchor,
                    java.nio.file.attribute.BasicFileAttributes.class,
                    LinkOption.NOFOLLOW_LINKS)
                .fileKey();
    String identity = normalized + "\n" + String.valueOf(fileKey) + "\n" + detail + "\n";
    if (Files.isRegularFile(normalized, LinkOption.NOFOLLOW_LINKS)) identity += sha256(normalized);
    return hash(identity);
  }

  static String deploymentUrl(String configured, Path file) {
    if (file == null || !file.isAbsolute() || !file.normalize().equals(file))
      throw new IllegalArgumentException();
    String exact = "jdbc:sqlite:" + file;
    if (!exact.equals(configured)) throw new IllegalArgumentException();
    return exact;
  }

  private static void rejectSymlinkPath(Path path) throws IOException {
    if (path == null || !path.isAbsolute()) throw new IOException();
    Path current = path.getRoot();
    for (Path component : path) {
      current = current.resolve(component);
      if (Files.isSymbolicLink(current)) throw new IOException();
    }
  }

  private static void rejectAbsoluteDatabasePath(Path file) throws IOException {
    if (!file.isAbsolute() || !file.normalize().equals(file)) throw new IOException();
    Path normalized = file.toAbsolutePath().normalize();
    Path current = normalized.getRoot();
    for (Path component : normalized) {
      current = current.resolve(component);
      if (Files.isSymbolicLink(current)) throw new IOException();
    }
  }

  private static boolean tableExists(Connection c, String table) throws SQLException {
    try (var p = c.prepareStatement("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?")) {
      p.setString(1, table);
      try (var r = p.executeQuery()) {
        return r.next();
      }
    }
  }

  private static boolean columnExists(Connection c, String table, String column)
      throws SQLException {
    try (var s = c.createStatement();
        var r = s.executeQuery("PRAGMA table_info(\"" + table + "\")")) {
      while (r.next()) if (column.equals(r.getString("name"))) return true;
      return false;
    }
  }

  private static long scalar(Connection c, String sql) throws SQLException {
    try (var s = c.createStatement();
        var r = s.executeQuery(sql)) {
      r.next();
      return r.getLong(1);
    }
  }

  static void validateRequest(JsonNode root, boolean apply) {
    exactFields(
        root,
        apply
            ? Set.of("inputType", "inputPath", "inputSha256", "generation", "expectedFingerprint")
            : Set.of("inputType", "inputPath", "inputSha256", "generation"));
    if (!Set.of("csv", "package").contains(text(root, "inputType"))
        || !Path.of(text(root, "inputPath")).isAbsolute()
        || !text(root, "inputSha256").matches("[0-9a-f]{64}")) throw new IllegalArgumentException();
    if (apply && !text(root, "expectedFingerprint").matches("[0-9a-f]{64}"))
      throw new IllegalArgumentException();
    JsonNode g = root.get("generation");
    exactFields(
        g,
        Set.of(
            "codeVersion",
            "sourceDigest",
            "generationVersion",
            "inputSha256",
            "config",
            "upstream"));
    for (String key : List.of("codeVersion", "sourceDigest", "generationVersion", "inputSha256"))
      if (!text(g, key).matches("[0-9a-f]{64}")) throw new IllegalArgumentException();
    exactFields(g.get("config"), Set.of("sourceId", "licenseId"));
    if (!g.get("upstream").isObject()) throw new IllegalArgumentException();
  }

  private static void exactFields(JsonNode n, Set<String> fields) {
    if (n == null || !n.isObject()) throw new IllegalArgumentException();
    var actual = new java.util.HashSet<String>();
    n.properties().forEach(e -> actual.add(e.getKey()));
    if (!actual.equals(fields)) throw new IllegalArgumentException();
  }

  static void verifyGeneration(JsonNode g) throws Exception {
    if (!GenerationIdentityReader.embeddedCodeVersion().equals(text(g, "codeVersion"))
        || !"MIT".equals(text(g.get("config"), "licenseId"))
        || !"ecdict-stardict".equals(text(g.get("config"), "sourceId")))
      throw new IllegalArgumentException();
    String source =
        hash(
            canonical(
                Map.of(
                    "inputSha256", text(g, "inputSha256"), "upstream", toMap(g.get("upstream")))));
    String version =
        hash(
            canonical(
                Map.of(
                    "codeVersion",
                    text(g, "codeVersion"),
                    "sourceDigest",
                    text(g, "sourceDigest"),
                    "config",
                    toMap(g.get("config")))));
    if (!source.equals(text(g, "sourceDigest")) || !version.equals(text(g, "generationVersion")))
      throw new IllegalArgumentException();
  }

  private static LexiconImportMetadata.GenerationIdentity generation(JsonNode n) throws Exception {
    return new LexiconImportMetadata.GenerationIdentity(
        text(n, "codeVersion"),
        text(n, "sourceDigest"),
        text(n, "generationVersion"),
        text(n, "inputSha256"),
        canonical(toMap(n.get("config"))),
        canonical(
            Map.of("inputSha256", text(n, "inputSha256"), "upstream", toMap(n.get("upstream")))));
  }

  private static Object toMap(JsonNode n) {
    if (n.isObject()) {
      var m = new TreeMap<String, Object>();
      n.properties().forEach(e -> m.put(e.getKey(), toMap(e.getValue())));
      return m;
    }
    if (n.isArray()) {
      var l = new java.util.ArrayList<>();
      n.forEach(v -> l.add(toMap(v)));
      return l;
    }
    if (n.isString()) return n.asString();
    if (n.isBoolean()) return n.asBoolean();
    if (n.isNumber()) return n.numberValue();
    if (n.isNull()) return null;
    throw new IllegalArgumentException();
  }

  private static String canonical(Object value) throws Exception {
    return JSON.writeValueAsString(canonicalValue(value));
  }

  private static Object canonicalValue(Object value) {
    if (value instanceof Map<?, ?> map) {
      var sorted = new TreeMap<String, Object>();
      map.forEach((key, item) -> sorted.put((String) key, canonicalValue(item)));
      return sorted;
    }
    if (value instanceof List<?> list)
      return list.stream().map(DeploymentDataCommand::canonicalValue).toList();
    return value;
  }

  private static String hash(String v) throws Exception {
    return HexFormat.of()
        .formatHex(MessageDigest.getInstance("SHA-256").digest(v.getBytes(StandardCharsets.UTF_8)));
  }

  private static String sha256(Path p) throws Exception {
    if (!Files.isRegularFile(p, LinkOption.NOFOLLOW_LINKS)) throw new IOException();
    try (var in = Files.newInputStream(p)) {
      var md = MessageDigest.getInstance("SHA-256");
      byte[] b = new byte[65536];
      for (int n; (n = in.read(b)) >= 0; ) md.update(b, 0, n);
      return HexFormat.of().formatHex(md.digest());
    }
  }

  private static byte[] schemaSql() throws IOException {
    try (var in =
        DeploymentDataCommand.class.getResourceAsStream("/META-INF/lexiflow-schema.sql")) {
      if (in == null) throw new IOException();
      return in.readAllBytes();
    }
  }

  private static String text(JsonNode n, String key) {
    return text(n == null ? null : n.get(key));
  }

  private static String text(JsonNode n) {
    if (n == null || !n.isString() || n.asString().isBlank()) throw new IllegalArgumentException();
    return n.asString();
  }

  private static String inspectResult(Snapshot s) throws Exception {
    var value = new TreeMap<String, Object>();
    value.put("status", "PASS");
    value.put("databaseFile", s.databaseFile());
    value.put("fingerprint", s.fingerprint());
    value.put("needsRebuild", s.needsRebuild());
    value.put("reason", s.reason());
    value.put("generationVersion", s.generationVersion());
    value.put("datasetVersion", s.datasetVersion());
    return JSON.writeValueAsString(value);
  }

  private static String result(String status, String reason) {
    return "{\"status\":\"" + status + "\",\"reason\":\"" + reason + "\"}";
  }

  /**
   * 部署文件检查快照，不包含私有输入内容。
   *
   * @param databaseFile 规范目标文件路径
   * @param fingerprint 目标文件状态的摘要
   * @param needsRebuild 是否需要显式重建
   * @param reason 固定检查原因
   * @param generationVersion 已发布资料的生成身份，未发布时为空
   * @param datasetVersion 已发布资料版本，未发布时为空
   */
  record Snapshot(
      String databaseFile,
      String fingerprint,
      boolean needsRebuild,
      String reason,
      String generationVersion,
      Long datasetVersion) {}

  /** 固定部署阻断原因，仅供管理 CLI 映射为非敏感响应。 */
  private static final class Blocked extends RuntimeException {
    private static final long serialVersionUID = 1L;
    private final String reason;

    Blocked(String reason) {
      this.reason = reason;
    }
  }
}
