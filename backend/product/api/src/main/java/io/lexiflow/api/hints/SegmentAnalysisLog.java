package io.lexiflow.api.hints;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.observability.platform.SegmentAnalysisRecord;
import io.lexiflow.observability.platform.SegmentAnalysisStore;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;

/** 将 API 已验证结果映射至中立敏感分析记录；不拥有文件格式或存储。 */
public final class SegmentAnalysisLog {
  private final SegmentAnalysisStore store;

  /**
   * 注入敏感分析 Store。
   *
   * @param store 含义：同步接收敏感分析记录的适配器。取值范围：非 null。
   */
  public SegmentAnalysisLog(SegmentAnalysisStore store) {
    this.store = Objects.requireNonNull(store);
  }

  /**
   * 只记录本次处理覆盖；旧上下文与无新增请求均不写入。
   *
   * @param request 含义：本次增量字幕请求。取值范围：已通过领域校验的非空请求。
   * @param result 含义：本次处理结果及实际覆盖的字幕键。取值范围：非空处理结果。
   * @throws IOException Store 初始化、读取或写入失败时抛出。
   */
  public synchronized void record(CaptionIncrementalRequest request, IncrementalHintResult result)
      throws IOException {
    if (result.processedKeys().isEmpty()) return;
    var segments = new HashMap<String, CaptionIncrementalRequest.Segment>();
    var translated = new HashMap<String, List<SegmentAnalysisRecord.TranslatedRange>>();
    for (var group : request.current().captions())
      for (var segment : group.segments()) segments.put(segment.key(), segment);
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
      boolean found = false;
      for (var group : request.current().captions()) {
        var members = group.segments();
        var first = indexOf(members, hint.startKey());
        var last = indexOf(members, hint.endKey());
        if (first < 0 || last < first) continue;
        for (int index = first; index <= last; index++) {
          var segment = members.get(index);
          int start = index == first ? hint.startOffset() : 0;
          int end = index == last ? hint.endOffset() : segment.text().length();
          translated
              .computeIfAbsent(segment.key(), ignored -> new ArrayList<>())
              .add(
                  new SegmentAnalysisRecord.TranslatedRange(
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
    var records = new ArrayList<SegmentAnalysisRecord>();
    for (var key : result.processedKeys()) {
      var segment = segments.get(key);
      if (segment == null || !segment.append())
        throw new IOException("processed segment is missing");
      records.add(
          new SegmentAnalysisRecord(
              digest(key), segment.text(), translated.getOrDefault(key, List.of())));
    }
    store.append(List.copyOf(records));
  }

  private static int indexOf(List<CaptionIncrementalRequest.Segment> segments, String key) {
    for (int i = 0; i < segments.size(); i++) if (segments.get(i).key().equals(key)) return i;
    return -1;
  }

  private static String digest(String value) {
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 unavailable", exception);
    }
  }
}
