package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.application.importing.policy.HintPreparation;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.DigestInputStream;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;
import tools.jackson.core.JsonGenerator;
import tools.jackson.core.JsonParser;
import tools.jackson.core.JsonToken;
import tools.jackson.core.ObjectReadContext;
import tools.jackson.core.ObjectWriteContext;
import tools.jackson.core.StreamReadFeature;
import tools.jackson.core.json.JsonFactory;

/** 固定 ZIP 与 NDJSON 的流式验证和编解码。 */
public final class DatasetPackageCodec {
  public static final long MAX_PACKAGE_BYTES = 3L * 1024 * 1024 * 1024;
  public static final long MAX_EXPANDED_BYTES = 8L * 1024 * 1024 * 1024;
  public static final int MAX_LINE_BYTES = 1024 * 1024;
  public static final int MAX_METADATA_BYTES = 1024 * 1024;
  public static final List<String> ENTRY_NAMES =
      List.of(
          "manifest.json", "approvals.json", "dataset.ndjson", "entries.ndjson", "forms.ndjson");
  private static final JsonFactory FACTORY =
      JsonFactory.builder().enable(StreamReadFeature.STRICT_DUPLICATE_DETECTION).build();
  private static final String BAD = "invalid package";

  private DatasetPackageCodec() {}

  /**
   * 校验后的一次性私有解包目录；始终从此副本恢复而非重开外部路径。
   *
   * @param directory 含义：本次私有临时目录。取值范围：由校验器创建。
   * @param manifest 含义：已严格校验的资料包清单。取值范围：封闭元数据。
   * @param sha256 含义：外部核对通过的完整 ZIP 摘要。取值范围：64 位小写十六进制。
   */
  record Verified(Path directory, DatasetPackageManifest manifest, String sha256)
      implements AutoCloseable {
    /** 返回本次私有副本中的固定条目路径。 */
    Path file(String name) {
      return directory.resolve(name);
    }

    @Override
    public void close() throws IOException {
      for (String name : ENTRY_NAMES) Files.deleteIfExists(file(name));
      Files.deleteIfExists(directory);
    }
  }

  /** 创建 SHA-256 摘要器。 */
  static MessageDigest digest() {
    try {
      return MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 unavailable", exception);
    }
  }

  /** 计算内存小值的 SHA-256。 */
  static String sha256(byte[] bytes) {
    return HexFormat.of().formatHex(digest().digest(bytes));
  }

  /** 流式计算普通文件 SHA-256。 */
  static String sha256(Path file) throws IOException {
    rejectSymlinkPath(file);
    try (InputStream input = Files.newInputStream(file)) {
      var hash = digest();
      byte[] buffer = new byte[65536];
      int n;
      while ((n = input.read(buffer)) != -1) hash.update(buffer, 0, n);
      return HexFormat.of().formatHex(hash.digest());
    }
  }

  /** 固定插入顺序的 JSON 编码；整数和十进制保留精度。 */
  static byte[] jsonBytes(Object value) throws IOException {
    var out = new ByteArrayOutputStream();
    try (JsonGenerator generator = FACTORY.createGenerator(ObjectWriteContext.empty(), out)) {
      writeJson(generator, value);
    }
    return out.toByteArray();
  }

  private static void writeJson(JsonGenerator generator, Object value) throws IOException {
    if (value == null) generator.writeNull();
    else if (value instanceof Map<?, ?> map) {
      generator.writeStartObject();
      for (var entry : map.entrySet()) {
        generator.writeName((String) entry.getKey());
        writeJson(generator, entry.getValue());
      }
      generator.writeEndObject();
    } else if (value instanceof List<?> list) {
      generator.writeStartArray();
      for (Object item : list) writeJson(generator, item);
      generator.writeEndArray();
    } else if (value instanceof String text) generator.writeString(text);
    else if (value instanceof Boolean flag) generator.writeBoolean(flag);
    else if (value instanceof BigDecimal number) generator.writeNumber(number);
    else if (value instanceof Number number) generator.writeNumber(number.toString());
    else throw new IOException(BAD);
  }

  /** 单个有界 JSON 值；拒绝重复字段、非标准数字与尾随内容。 */
  static Object parseJson(byte[] bytes) throws IOException {
    try (JsonParser parser = FACTORY.createParser(ObjectReadContext.empty(), bytes)) {
      Object value = parseValue(parser, parser.nextToken());
      if (parser.nextToken() != null) throw new IOException(BAD);
      return value;
    } catch (RuntimeException exception) {
      throw new IOException(BAD, exception);
    }
  }

  /** 解析一行有界 JSON。 */
  static Object parseJson(String text) throws IOException {
    return parseJson(text.getBytes(StandardCharsets.UTF_8));
  }

  private static Object parseValue(JsonParser parser, JsonToken token) throws IOException {
    if (token == JsonToken.START_OBJECT) {
      Map<String, Object> result = new LinkedHashMap<>();
      while ((token = parser.nextToken()) != JsonToken.END_OBJECT) {
        if (token != JsonToken.PROPERTY_NAME) throw new IOException(BAD);
        String key = parser.currentName();
        if (result.containsKey(key)) throw new IOException(BAD);
        result.put(key, parseValue(parser, parser.nextToken()));
      }
      return result;
    }
    if (token == JsonToken.START_ARRAY) {
      List<Object> result = new ArrayList<>();
      while ((token = parser.nextToken()) != JsonToken.END_ARRAY)
        result.add(parseValue(parser, token));
      return result;
    }
    if (token == JsonToken.VALUE_STRING) return parser.getString();
    if (token == JsonToken.VALUE_NUMBER_INT) return parser.getDecimalValue();
    if (token == JsonToken.VALUE_NUMBER_FLOAT) return parser.getDecimalValue();
    if (token == JsonToken.VALUE_TRUE) return true;
    if (token == JsonToken.VALUE_FALSE) return false;
    if (token == JsonToken.VALUE_NULL) return null;
    throw new IOException(BAD);
  }

  /** 核对严格对象字段集合。 */
  @SuppressWarnings("unchecked")
  static Map<String, Object> object(Object value, Set<String> expected) throws IOException {
    if (!(value instanceof Map<?, ?> map) || !map.keySet().equals(expected))
      throw new IOException(BAD);
    return (Map<String, Object>) value;
  }

  /** 核对 JSON 数组。 */
  @SuppressWarnings("unchecked")
  static List<Object> array(Object value) throws IOException {
    if (!(value instanceof List<?> list)) throw new IOException(BAD);
    return (List<Object>) list;
  }

  /** 核对非空 JSON 字符串。 */
  static String string(Object value) throws IOException {
    if (!(value instanceof String text) || text.isBlank()) throw new IOException(BAD);
    return text;
  }

  /** 核对未丢精度的整数。 */
  static long integer(Object value, long min) throws IOException {
    if (!(value instanceof Number number) || value instanceof Float || value instanceof Double)
      throw new IOException(BAD);
    try {
      long result = new BigDecimal(number.toString()).longValueExact();
      if (result < min) throw new IOException(BAD);
      return result;
    } catch (ArithmeticException exception) {
      throw new IOException(BAD, exception);
    }
  }

  /** 核对小写 SHA-256 字符串。 */
  static String digestValue(Object value) throws IOException {
    String text = string(value);
    if (!text.matches("[0-9a-f]{64}")) throw new IOException(BAD);
    return text;
  }

  /** 解析封闭资料包清单。 */
  static DatasetPackageManifest parseManifest(byte[] bytes) throws IOException {
    if (bytes.length > MAX_METADATA_BYTES) throw new IOException(BAD);
    Map<String, Object> node =
        object(
            parseJson(bytes),
            Set.of(
                "schemaVersion",
                "schemaSha256",
                "approvalSha256",
                "datasetVersion",
                "preparationPolicy",
                "files"));
    if (integer(node.get("schemaVersion"), 2) != 2) throw new IOException(BAD);
    String schema = digestValue(node.get("schemaSha256"));
    String approval = digestValue(node.get("approvalSha256"));
    long version = integer(node.get("datasetVersion"), 1);
    String policy = string(node.get("preparationPolicy"));
    if (!HintPreparation.POLICY_ID.equals(policy)) throw new IOException(BAD);
    List<Object> files = array(node.get("files"));
    if (files.size() != 3) throw new IOException(BAD);
    var records = new ArrayList<DatasetPackageManifest.FileRecord>();
    for (int i = 0; i < 3; i++) {
      var record = object(files.get(i), Set.of("name", "bytes", "sha256", "rows"));
      String name = string(record.get("name"));
      if (!ENTRY_NAMES.get(i + 2).equals(name)) throw new IOException(BAD);
      long size = integer(record.get("bytes"), 1);
      String hash = digestValue(record.get("sha256"));
      long rows = integer(record.get("rows"), 1);
      if (i == 0 && rows != 1) throw new IOException(BAD);
      records.add(new DatasetPackageManifest.FileRecord(name, size, hash, rows));
    }
    return new DatasetPackageManifest(2, schema, approval, version, policy, List.copyOf(records));
  }

  /** 审批仅核对显式记录与来源一致，不推断实际法律许可。 */
  static Map<String, Object> parseApprovals(byte[] bytes) throws IOException {
    if (bytes.length > MAX_METADATA_BYTES) throw new IOException(BAD);
    var root = object(parseJson(bytes), Set.of("schemaVersion", "sources"));
    if (integer(root.get("schemaVersion"), 1) != 1) throw new IOException(BAD);
    List<Object> sources = array(root.get("sources"));
    if (sources.isEmpty()) throw new IOException(BAD);
    Set<String> ids = new HashSet<>();
    for (Object item : sources) {
      var source =
          object(
              item,
              Set.of(
                  "sourceId",
                  "sourceDigest",
                  "licenseId",
                  "redistributionApproved",
                  "evidenceUrl"));
      if (!ids.add(string(source.get("sourceId")))) throw new IOException(BAD);
      digestValue(source.get("sourceDigest"));
      string(source.get("licenseId"));
      if (!Boolean.TRUE.equals(source.get("redistributionApproved"))) throw new IOException(BAD);
      String url = string(source.get("evidenceUrl"));
      try {
        var uri = java.net.URI.create(url);
        if (!"https".equals(uri.getScheme())
            || uri.getHost() == null
            || uri.getUserInfo() != null
            || uri.getFragment() != null) throw new IOException(BAD);
      } catch (IllegalArgumentException exception) {
        throw new IOException(BAD, exception);
      }
    }
    return root;
  }

  /** 严格匹配数据库来源与显式审批材料。 */
  static void validateSources(Object sourceManifest, Map<String, Object> approvals)
      throws IOException {
    var manifest = array(sourceManifest);
    if (manifest.isEmpty()) throw new IOException(BAD);
    var expected = new HashMap<String, String>();
    for (Object item : array(approvals.get("sources"))) {
      var source =
          object(
              item,
              Set.of(
                  "sourceId",
                  "sourceDigest",
                  "licenseId",
                  "redistributionApproved",
                  "evidenceUrl"));
      expected.put(
          string(source.get("sourceId")),
          string(source.get("sourceDigest")) + "\u0000" + string(source.get("licenseId")));
    }
    if (manifest.size() != expected.size()) throw new IOException(BAD);
    var seen = new HashSet<String>();
    for (Object item : manifest) {
      var source = object(item, Set.of("source_id", "source_digest", "license_id", "acquired_at"));
      String id = string(source.get("source_id"));
      String value =
          digestValue(source.get("source_digest")) + "\u0000" + string(source.get("license_id"));
      if (!seen.add(id) || !value.equals(expected.get(id))) throw new IOException(BAD);
      try {
        Instant.parse(string(source.get("acquired_at")));
      } catch (RuntimeException exception) {
        throw new IOException(BAD, exception);
      }
    }
  }

  /** 从同一文件句柄复制并核对外部摘要，解包至独有私有目录。 */
  static Verified verifiedPackage(Path packagePath, String expectedSha256, byte[] trustedSchema)
      throws IOException {
    if (expectedSha256 == null || !expectedSha256.matches("[0-9a-f]{64}"))
      throw new IOException(BAD);
    rejectSymlinkPath(packagePath);
    if (!Files.isRegularFile(packagePath, LinkOption.NOFOLLOW_LINKS)
        || Files.size(packagePath) > MAX_PACKAGE_BYTES) throw new IOException(BAD);
    Path dir = Files.createTempDirectory("lexiflow-dataset-").toRealPath();
    Path copy = dir.resolve("source.zip");
    try {
      var hash = digest();
      try (var in = new DigestInputStream(Files.newInputStream(packagePath), hash);
          var out = Files.newOutputStream(copy)) {
        byte[] buffer = new byte[65536];
        long copied = 0;
        int n;
        while ((n = in.read(buffer)) != -1) {
          copied += n;
          if (copied > MAX_PACKAGE_BYTES) throw new IOException(BAD);
          out.write(buffer, 0, n);
        }
      }
      String actual = HexFormat.of().formatHex(hash.digest());
      if (!actual.equals(expectedSha256) || Files.size(copy) > MAX_PACKAGE_BYTES)
        throw new IOException(BAD);
      long expanded = 0;
      try (var zip = new ZipInputStream(new BufferedInputStream(Files.newInputStream(copy)))) {
        for (String name : ENTRY_NAMES) {
          ZipEntry entry = zip.getNextEntry();
          if (entry == null || entry.isDirectory() || !name.equals(entry.getName()))
            throw new IOException(BAD);
          long size = 0;
          try (var out = new BufferedOutputStream(Files.newOutputStream(dir.resolve(name)))) {
            byte[] buffer = new byte[65536];
            int n;
            while ((n = zip.read(buffer)) != -1) {
              size += n;
              expanded += n;
              if (expanded > MAX_EXPANDED_BYTES
                  || (name.endsWith(".json") && size > MAX_METADATA_BYTES))
                throw new IOException(BAD);
              out.write(buffer, 0, n);
            }
          }
          zip.closeEntry();
        }
        if (zip.getNextEntry() != null) throw new IOException(BAD);
      }
      if (!actual.equals(
          canonicalSha256(
              List.of(
                  dir.resolve("manifest.json"),
                  dir.resolve("approvals.json"),
                  dir.resolve("dataset.ndjson"),
                  dir.resolve("entries.ndjson"),
                  dir.resolve("forms.ndjson"))))) throw new IOException(BAD);
      Files.delete(copy);
      var manifest = parseManifest(Files.readAllBytes(dir.resolve("manifest.json")));
      if (!sha256(trustedSchema).equals(manifest.schemaSha256())) throw new IOException(BAD);
      byte[] approvalBytes = Files.readAllBytes(dir.resolve("approvals.json"));
      if (!sha256(approvalBytes).equals(manifest.approvalSha256())) throw new IOException(BAD);
      var approvals = parseApprovals(approvalBytes);
      Map<String, Object> dataset = null;
      for (int i = 0; i < 3; i++) {
        var table = DatasetPackageTables.TABLES.get(i);
        var record = manifest.files().get(i);
        Path file = dir.resolve(record.name());
        if (Files.size(file) != record.bytes() || !sha256(file).equals(record.sha256()))
          throw new IOException(BAD);
        long rows = 0;
        try (var lines = new BoundedLines(file)) {
          String line;
          while ((line = lines.next()) != null) {
            var row = validateRow(parseJson(line), table);
            if (i == 0) dataset = row;
            rows++;
          }
        }
        if (rows != record.rows()) throw new IOException(BAD);
      }
      if (dataset == null
          || integer(dataset.get("dataset_id"), 1) != 1
          || integer(dataset.get("lexicon_version"), 1) != manifest.datasetVersion()
          || !string(dataset.get("preparation_policy")).equals(manifest.preparationPolicy())
          || integer(dataset.get("entry_count"), 1) != manifest.files().get(1).rows()
          || integer(dataset.get("lookup_count"), 1) != manifest.files().get(2).rows()
          || integer(dataset.get("source_row_count"), 1) < manifest.files().get(1).rows())
        throw new IOException(BAD);
      validateSources(dataset.get("source_manifest"), approvals);
      return new Verified(dir, manifest, actual);
    } catch (IOException | RuntimeException exception) {
      for (String name : ENTRY_NAMES) Files.deleteIfExists(dir.resolve(name));
      Files.deleteIfExists(copy);
      Files.deleteIfExists(dir);
      throw new IOException(BAD, exception);
    }
  }

  /**
   * 离线核对完整包及可信外部摘要；不连接数据库。
   *
   * @param path 含义：普通 ZIP 文件。取值范围：不存在符号链接的路径。
   * @param expectedSha256 含义：可信发布清单中的完整 ZIP 摘要。取值范围：64 位小写十六进制。
   * @param trustedSchema 含义：内嵌最新 SQL 内容。取值范围：由组合根读取的可信资源。
   */
  public static void verify(Path path, String expectedSha256, byte[] trustedSchema)
      throws IOException {
    try (var verified = verifiedPackage(path, expectedSha256, trustedSchema)) {
      verified.manifest();
    }
  }

  /** 核对固定表行的全列和强类型。 */
  static Map<String, Object> validateRow(Object rowValue, DatasetPackageTables.Table table)
      throws IOException {
    var row = object(rowValue, table.columnSet());
    if (!List.copyOf(row.keySet()).equals(table.columns())) throw new IOException(BAD);
    for (String column : table.columns()) {
      Object value = row.get(column);
      if (value == null) {
        if (!Set.of("gloss").contains(column)) throw new IOException(BAD);
        continue;
      }
      switch (DatasetPackageTables.TYPES.get(column)) {
        case "smallint", "integer", "bigint" -> integer(value, 0);
        case "numeric" -> {
          if (!(value instanceof BigDecimal number) || number.scale() > 2 || number.signum() < 0)
            throw new IOException(BAD);
        }
        case "boolean" -> {
          if (!(value instanceof Boolean)) throw new IOException(BAD);
        }
        case "uuid" -> {
          String text = string(value);
          try {
            if (!UUID.fromString(text).toString().equals(text)) throw new IOException(BAD);
          } catch (IllegalArgumentException exception) {
            throw new IOException(BAD, exception);
          }
        }
        case "timestamptz" -> {
          try {
            Instant.parse(string(value));
          } catch (RuntimeException exception) {
            throw new IOException(BAD, exception);
          }
        }
        case "text[]" -> {
          for (Object element : array(value)) string(element);
        }
        case "jsonb" -> array(value);
        default -> string(value);
      }
    }
    if (table == DatasetPackageTables.ENTRY) {
      if (integer(row.get("entry_id"), 1) < 1
          || string(row.get("lemma")).isBlank()
          || integer(row.get("hint_priority"), 0) > 1000
          || integer(row.get("complex_list_count"), 0) > Short.MAX_VALUE
          || integer(row.get("cache_priority"), 0) > 1000) throw new IOException(BAD);
    } else if (table == DatasetPackageTables.FORM
        && !LexiconSurfacePolicy.withinQueryWindow(string(row.get("normalized_form")))) {
      throw new IOException(BAD);
    }
    return row;
  }

  /** 按字节限制每行，拒绝无末尾换行与空行。 */
  static final class BoundedLines implements AutoCloseable {
    private final InputStream input;

    /** 打开一份已校验的私有 NDJSON 文件。 */
    BoundedLines(Path path) throws IOException {
      input = new BufferedInputStream(Files.newInputStream(path));
    }

    /** 读取下一行，结尾返回 null。 */
    String next() throws IOException {
      var out = new ByteArrayOutputStream();
      int b;
      while ((b = input.read()) != -1) {
        if (b == '\n') {
          if (out.size() == 0) throw new IOException(BAD);
          try {
            return StandardCharsets.UTF_8
                .newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(out.toByteArray()))
                .toString();
          } catch (CharacterCodingException exception) {
            throw new IOException(BAD, exception);
          }
        }
        if (out.size() >= MAX_LINE_BYTES || b == '\r') throw new IOException(BAD);
        out.write(b);
      }
      if (out.size() != 0) throw new IOException(BAD);
      return null;
    }

    @Override
    public void close() throws IOException {
      input.close();
    }
  }

  /** 固定顺序与 DOS 时间写入，同内容已有目标可复用。 */
  static void write(Path output, List<Path> sourceFiles) throws IOException {
    if (sourceFiles.size() != ENTRY_NAMES.size()) throw new IOException(BAD);
    rejectSymlinkPath(output);
    Path parent = output.toAbsolutePath().normalize().getParent();
    if (parent == null || !Files.isDirectory(parent)) throw new IOException(BAD);
    Path temporary = Files.createTempFile(parent, ".lexiflow-package-", ".tmp");
    try {
      try (OutputStream raw = new BufferedOutputStream(Files.newOutputStream(temporary))) {
        writeArchive(raw, sourceFiles);
      }
      if (Files.size(temporary) > MAX_PACKAGE_BYTES) throw new IOException(BAD);
      try {
        // 同目录硬链接创建具备排他语义；ATOMIC_MOVE 对已有目标可能实现为替换。
        Files.createLink(output, temporary);
      } catch (java.nio.file.FileAlreadyExistsException exception) {
        if (!Files.isRegularFile(output, LinkOption.NOFOLLOW_LINKS)
            || !sha256(output).equals(sha256(temporary))) throw new IOException(BAD, exception);
      }
    } finally {
      Files.deleteIfExists(temporary);
    }
  }

  private static void writeArchive(OutputStream raw, List<Path> sourceFiles) throws IOException {
    try (ZipOutputStream zip = new ZipOutputStream(raw)) {
      for (int i = 0; i < sourceFiles.size(); i++) {
        rejectSymlinkPath(sourceFiles.get(i));
        ZipEntry entry = new ZipEntry(ENTRY_NAMES.get(i));
        entry.setTime(
            java.time.LocalDateTime.of(2000, 1, 1, 0, 0)
                .atZone(java.time.ZoneId.systemDefault())
                .toInstant()
                .toEpochMilli());
        zip.putNextEntry(entry);
        try (InputStream input = Files.newInputStream(sourceFiles.get(i))) {
          input.transferTo(zip);
        }
        zip.closeEntry();
      }
    }
  }

  private static String canonicalSha256(List<Path> sourceFiles) throws IOException {
    var hash = digest();
    try (var sink = new DigestOutputStream(OutputStream.nullOutputStream(), hash)) {
      writeArchive(sink, sourceFiles);
    }
    return HexFormat.of().formatHex(hash.digest());
  }

  /** 在完整验证后的同一文件系统内排他发布，不覆盖已有不同内容。 */
  static void publishVerified(Path candidate, Path output) throws IOException {
    rejectSymlinkPath(candidate);
    rejectSymlinkPath(output);
    if (!Files.isRegularFile(candidate, LinkOption.NOFOLLOW_LINKS)) throw new IOException(BAD);
    try {
      Files.createLink(output, candidate);
    } catch (java.nio.file.FileAlreadyExistsException exception) {
      if (!Files.isRegularFile(output, LinkOption.NOFOLLOW_LINKS)
          || !sha256(output).equals(sha256(candidate))) throw new IOException(BAD, exception);
    }
  }

  /** 拒绝输入或输出路径任何级别的符号链接。 */
  static void rejectSymlinkPath(Path path) throws IOException {
    Path current = path.toAbsolutePath().normalize().getRoot();
    for (Path part : path.toAbsolutePath().normalize()) {
      current = current.resolve(part);
      if (Files.isSymbolicLink(current)) throw new IOException(BAD);
    }
  }
}
