package io.lexiflow.observability.platform;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Instant;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;
import java.util.regex.Pattern;
import tools.jackson.core.JsonToken;
import tools.jackson.core.io.JsonStringEncoder;
import tools.jackson.core.json.JsonFactory;

/** 同步写入本机敏感 JSONL 台账的技术适配器。 */
public final class FileSegmentAnalysisStore implements SegmentAnalysisStore {
  private static final Pattern ID = Pattern.compile("[0-9a-f]{64}");

  /** 显式分析台账在重启后仍受单文件大小上限约束。 */
  private static final long MAX_FILE_BYTES = 16L * 1024 * 1024;

  private final Path path;
  private final Consumer<String> console;
  private final Set<String> recorded = new HashSet<>();
  private long knownSize;
  private boolean initialized;

  /**
   * 创建文件适配器。
   *
   * @param path 含义：本机私有台账路径。取值范围：非 null。
   * @param console 含义：成功写入后的专用输出。取值范围：null 表示不输出。
   */
  public FileSegmentAnalysisStore(Path path, Consumer<String> console) {
    this.path = path.toAbsolutePath().normalize();
    this.console = console;
  }

  @Override
  public synchronized void append(List<SegmentAnalysisRecord> records) throws IOException {
    if (records.isEmpty()) return;
    initialize();
    for (var record : records) appendOne(record);
  }

  private void initialize() throws IOException {
    if (initialized) return;
    var parent = path.getParent();
    boolean existed = Files.exists(parent);
    Files.createDirectories(parent);
    if (!existed) setPermissions(parent, "rwx------");
    if (Files.isSymbolicLink(path)) throw new IOException("analysis log must not be a symlink");
    if (Files.notExists(path, LinkOption.NOFOLLOW_LINKS))
      Files.createFile(
          path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
    if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
      throw new IOException("analysis log must be a regular file");
    setPermissions(path, "rw-------");
    try (var channel = FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        var lock = channel.lock(0L, Long.MAX_VALUE, true)) {
      if (!lock.isValid()) throw new IOException("analysis log lock unavailable");
      reload();
    }
    initialized = true;
  }

  private void appendOne(SegmentAnalysisRecord record) throws IOException {
    try (var channel =
            FileChannel.open(
                path,
                StandardOpenOption.READ,
                StandardOpenOption.WRITE,
                LinkOption.NOFOLLOW_LINKS);
        var lock = channel.lock()) {
      if (!lock.isValid()) throw new IOException("analysis log lock unavailable");
      if (channel.size() != knownSize) reload();
      if (recorded.contains(record.segmentId())) return;
      var line = jsonLine(record) + "\n";
      var encoded = line.getBytes(StandardCharsets.UTF_8);
      if (channel.size() + encoded.length > MAX_FILE_BYTES) return;
      var bytes = ByteBuffer.wrap(encoded);
      channel.position(channel.size());
      while (bytes.hasRemaining()) channel.write(bytes);
      channel.force(true);
      recorded.add(record.segmentId());
      knownSize = channel.size();
      if (console != null) {
        try {
          console.accept("[LexiFlow segment] " + line);
        } catch (RuntimeException ignored) {
          // 专用控制台仅为辅助输出，不得撤销已经持久化的成功。
        }
      }
    }
  }

  private void reload() throws IOException {
    var candidate = new HashSet<String>();
    long candidateSize = Files.size(path);
    if (candidateSize > MAX_FILE_BYTES)
      throw new IOException("analysis log exceeds retention limit");
    byte[] contents;
    try (var input =
        Files.newInputStream(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
      contents = input.readNBytes(Math.toIntExact(MAX_FILE_BYTES + 1));
    }
    if (contents.length > MAX_FILE_BYTES || contents.length != candidateSize)
      throw new IOException("analysis log changed or exceeds retention limit");
    if (candidateSize > 0) {
      if (contents[contents.length - 1] != '\n')
        throw new IOException("analysis log has incomplete final line");
    }
    var decoder = StandardCharsets.UTF_8.newDecoder();
    try (BufferedReader reader =
        new BufferedReader(new InputStreamReader(new ByteArrayInputStream(contents), decoder))) {
      String line;
      while ((line = reader.readLine()) != null) {
        var id = validatedId(line);
        if (!candidate.add(id)) throw new IOException("analysis log has duplicate record");
      }
    }
    recorded.clear();
    recorded.addAll(candidate);
    knownSize = candidateSize;
  }

  private static String validatedId(String line) throws IOException {
    try (var parser =
        new JsonFactory().createParser(tools.jackson.core.ObjectReadContext.empty(), line)) {
      if (parser.nextToken() != JsonToken.START_OBJECT) throw new IOException("invalid record");
      String id = null;
      boolean segmentId = false;
      while (parser.nextToken() != JsonToken.END_OBJECT) {
        if (parser.currentToken() != JsonToken.PROPERTY_NAME)
          throw new IOException("invalid record");
        String name = parser.currentName();
        JsonToken value = parser.nextToken();
        if (name.equals("segmentId")) {
          if (value != JsonToken.VALUE_STRING || segmentId) throw new IOException("invalid id");
          id = parser.getString();
          segmentId = true;
        } else parser.skipChildren();
      }
      if (parser.nextToken() != null || id == null || !ID.matcher(id).matches())
        throw new IOException("invalid record");
      return id;
    } catch (RuntimeException exception) {
      throw new IOException("invalid record", exception);
    }
  }

  private static String jsonLine(SegmentAnalysisRecord r) {
    var json =
        new StringBuilder("{\"segmentId\":")
            .append(quote(r.segmentId()))
            .append(",\"schemaVersion\":1,\"recordedAt\":")
            .append(quote(Instant.now().toString()))
            .append(",\"english\":")
            .append(quote(r.english()))
            .append(",\"status\":\"")
            .append(r.translatedRanges().isEmpty() ? "NO_HINT" : "HINTED")
            .append("\",\"translatedRanges\":[");
    var cursor = 0;
    var untranslated = new StringBuilder();
    for (int i = 0; i < r.translatedRanges().size(); i++) {
      var x = r.translatedRanges().get(i);
      if (i > 0) json.append(',');
      json.append("{\"start\":")
          .append(x.start())
          .append(",\"end\":")
          .append(x.end())
          .append(",\"hintId\":")
          .append(quote(x.hintId()))
          .append(",\"gloss\":")
          .append(quote(x.gloss()))
          .append(",\"lexiconVersion\":")
          .append(x.lexiconVersion())
          .append(",\"anchor\":")
          .append(x.anchor())
          .append('}');
      if (x.start() > cursor) appendRange(untranslated, cursor, x.start());
      cursor = x.end();
    }
    if (cursor < r.english().length()) appendRange(untranslated, cursor, r.english().length());
    return json.append("],\"untranslatedRanges\":[").append(untranslated).append("]}").toString();
  }

  private static void appendRange(StringBuilder b, int s, int e) {
    if (!b.isEmpty()) b.append(',');
    b.append("{\"start\":").append(s).append(",\"end\":").append(e).append('}');
  }

  private static String quote(String s) {
    return '"' + new String(JsonStringEncoder.getInstance().quoteAsCharArray(s)) + '"';
  }

  private static void setPermissions(Path p, String mode) throws IOException {
    try {
      Files.setPosixFilePermissions(p, PosixFilePermissions.fromString(mode));
    } catch (UnsupportedOperationException ignored) {
    }
  }
}
