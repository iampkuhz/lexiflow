package io.lexiflow.api.hints;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import tools.jackson.core.io.JsonStringEncoder;

/** 本机敏感 JSONL 台账；每个不可增长的片段 key 只写一行最终处理结果。 */
public final class SegmentAnalysisLog {
  private static final Pattern ID_LINE =
      Pattern.compile("^\\{\"segmentId\":\"([0-9a-f]{64})\",.*}$");
  private final Path path;
  private final Set<String> recorded = new HashSet<>();
  private long knownSize;
  private boolean initialized;

  /** 绑定专用路径；首次记录时初始化，文件故障不能阻止 API 启动。 */
  public SegmentAnalysisLog(Path path) {
    this.path = path.toAbsolutePath().normalize();
  }

  private void initialize() throws IOException {
    if (initialized) return;
    var parent = this.path.getParent();
    var parentExisted = Files.exists(parent);
    Files.createDirectories(parent);
    if (!parentExisted) {
      try {
        Files.setPosixFilePermissions(parent, PosixFilePermissions.fromString("rwx------"));
      } catch (UnsupportedOperationException ignored) {
        // 非 POSIX 文件系统仍使用宿主 ACL；绝不修改用户已有目录权限。
      }
    }
    if (Files.isSymbolicLink(this.path))
      throw new IOException("analysis log must not be a symlink");
    if (Files.notExists(this.path, LinkOption.NOFOLLOW_LINKS)) {
      Files.createFile(
          this.path,
          PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
    }
    if (!Files.isRegularFile(this.path, LinkOption.NOFOLLOW_LINKS)) {
      throw new IOException("analysis log must be a regular file");
    }
    try {
      Files.setPosixFilePermissions(this.path, PosixFilePermissions.fromString("rw-------"));
    } catch (UnsupportedOperationException ignored) {
      // 非 POSIX 文件系统依赖宿主 ACL。
    }
    reload();
    initialized = true;
  }

  /** 默认写入仓库 ignored tmp；显式配置仅供本机选择另一私有路径。 */
  public static Path configuredPath() {
    var configured = System.getenv("LEXIFLOW_SEGMENT_LOG_PATH");
    if (configured != null && !configured.isBlank()) return Path.of(configured);
    for (var current = Path.of("").toAbsolutePath().normalize();
        current != null;
        current = current.getParent()) {
      if (Files.isRegularFile(current.resolve("harness/manifest.yaml"))) {
        return current.resolve("tmp/analysis/caption-segments.jsonl");
      }
    }
    throw new IllegalStateException("LexiFlow repository root is required for analysis log");
  }

  /** 只记录本次处理覆盖；旧上下文与无新增请求均不写入。 */
  public synchronized void record(CaptionIncrementalRequest request, IncrementalHintResult result)
      throws IOException {
    if (result.processedKeys().isEmpty()) return;
    initialize();
    var segments = new HashMap<String, CaptionIncrementalRequest.Segment>();
    var translated = new HashMap<String, List<TranslatedRange>>();
    for (var group : request.current().captions()) {
      for (var segment : group.segments()) segments.put(segment.key(), segment);
    }
    for (var hint : result.hints()) {
      var hintId =
          digest(
              hint.startKey()
                  + ":"
                  + hint.startOffset()
                  + ":"
                  + hint.endKey()
                  + ":"
                  + hint.endOffset());
      var found = false;
      for (var group : request.current().captions()) {
        var members = group.segments();
        var first = indexOf(members, hint.startKey());
        var last = indexOf(members, hint.endKey());
        if (first < 0 || last < first) continue;
        for (var index = first; index <= last; index++) {
          var segment = members.get(index);
          var start = index == first ? hint.startOffset() : 0;
          var end = index == last ? hint.endOffset() : segment.text().length();
          translated
              .computeIfAbsent(segment.key(), ignored -> new ArrayList<>())
              .add(
                  new TranslatedRange(
                      start,
                      end,
                      hintId,
                      hint.chineseGloss(),
                      hint.lexiconVersion(),
                      index == first));
        }
        found = true;
        break;
      }
      if (!found) throw new IOException("hint is outside processed segments");
    }
    for (var key : result.processedKeys()) {
      var segment = segments.get(key);
      if (segment == null || !segment.append())
        throw new IOException("processed segment is missing");
      var segmentId = digest(key);
      if (recorded.contains(segmentId)) continue;
      var ranges = translated.getOrDefault(key, List.of());
      append(segmentId, segment.text(), ranges);
    }
  }

  private void append(String id, String english, List<TranslatedRange> ranges) throws IOException {
    try (var channel =
            FileChannel.open(
                path,
                StandardOpenOption.READ,
                StandardOpenOption.WRITE,
                LinkOption.NOFOLLOW_LINKS);
        var lock = channel.lock()) {
      if (!lock.isValid()) throw new IOException("analysis log lock is unavailable");
      if (channel.size() != knownSize) reload();
      if (recorded.contains(id)) return;
      var line = jsonLine(id, english, ranges) + "\n";
      var bytes = ByteBuffer.wrap(line.getBytes(StandardCharsets.UTF_8));
      channel.position(channel.size());
      while (bytes.hasRemaining()) channel.write(bytes);
      channel.force(true);
      recorded.add(id);
      knownSize = channel.size();
    }
  }

  private void reload() throws IOException {
    recorded.clear();
    knownSize = Files.size(path);
    if (knownSize > 0) {
      try (var channel = FileChannel.open(path, StandardOpenOption.READ)) {
        var last = ByteBuffer.allocate(1);
        channel.read(last, knownSize - 1);
        if (last.get(0) != '\n') throw new IOException("analysis log has an incomplete final line");
      }
    }
    try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
      String line;
      while ((line = reader.readLine()) != null) {
        var match = ID_LINE.matcher(line);
        if (!match.matches() || !recorded.add(match.group(1))) {
          throw new IOException("analysis log has an invalid or duplicate record");
        }
      }
    }
  }

  private static int indexOf(List<CaptionIncrementalRequest.Segment> segments, String key) {
    for (var i = 0; i < segments.size(); i++) if (segments.get(i).key().equals(key)) return i;
    return -1;
  }

  private static String jsonLine(String id, String english, List<TranslatedRange> ranges) {
    var json =
        new StringBuilder("{\"segmentId\":\"")
            .append(id)
            .append("\",\"schemaVersion\":1,\"recordedAt\":")
            .append(quoted(Instant.now().toString()))
            .append(",\"english\":")
            .append(quoted(english))
            .append(",\"status\":\"")
            .append(ranges.isEmpty() ? "NO_HINT" : "HINTED")
            .append("\",\"translatedRanges\":[");
    var cursor = 0;
    var untranslated = new StringBuilder();
    for (var index = 0; index < ranges.size(); index++) {
      var range = ranges.get(index);
      if (index > 0) json.append(',');
      json.append("{\"start\":")
          .append(range.start())
          .append(",\"end\":")
          .append(range.end())
          .append(",\"hintId\":")
          .append(quoted(range.hintId()))
          .append(",\"gloss\":")
          .append(quoted(range.gloss()))
          .append(",\"lexiconVersion\":")
          .append(range.lexiconVersion())
          .append(",\"anchor\":")
          .append(range.anchor())
          .append('}');
      if (range.start() > cursor) appendRange(untranslated, cursor, range.start());
      cursor = range.end();
    }
    if (cursor < english.length()) appendRange(untranslated, cursor, english.length());
    return json.append("],\"untranslatedRanges\":[").append(untranslated).append("]}").toString();
  }

  private static void appendRange(StringBuilder target, int start, int end) {
    if (!target.isEmpty()) target.append(',');
    target.append("{\"start\":").append(start).append(",\"end\":").append(end).append('}');
  }

  private static String quoted(String value) {
    return '"' + new String(JsonStringEncoder.getInstance().quoteAsCharArray(value)) + '"';
  }

  private static String digest(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }

  private record TranslatedRange(
      int start, int end, String hintId, String gloss, long lexiconVersion, boolean anchor) {}
}
