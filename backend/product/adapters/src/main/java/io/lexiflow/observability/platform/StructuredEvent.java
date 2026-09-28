package io.lexiflow.observability.platform;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * 封闭的普通观测事件值；任何用户文本均不可进入事件字段。
 *
 * @param type 固定事件类别；取值范围：EventType。
 * @param reason 固定原因码；取值范围：Reason。
 * @param durationMs 单调时钟耗时；取值范围：非负毫秒。
 * @param counts 事件计数；取值范围：事件白名单中的非负值。
 * @param lexiconVersion 已知发布版本；取值范围：null 或非负值。
 * @param requestId 请求关联 UUID；取值范围：规定事件必填，其他事件为空。
 * @param timingsMs 请求阶段耗时；取值范围：请求事件中的非负值。
 * @param step 导入步骤；取值范围：导入 stage 事件必填。
 * @param phase 导入步骤阶段；取值范围：导入 stage 事件必填。
 * @param reasonCounts 导入规则计数；取值范围：导入完成事件中的固定规则键。
 */
public record StructuredEvent(
    EventType type,
    Reason reason,
    long durationMs,
    Map<Count, Long> counts,
    Long lexiconVersion,
    UUID requestId,
    Map<Timing, Long> timingsMs,
    ImportStep step,
    ImportPhase phase,
    Map<ReasonCount, Long> reasonCounts) {
  /** 固定事件类别。 */
  public enum EventType {
    RUNTIME_START_COMPLETED,
    LEXICON_IMPORT_STAGE,
    LEXICON_IMPORT_COMPLETED,
    LEXICON_CACHE_VERSION_CHANGED,
    CAPTION_REQUEST_COMPLETED,
    RUNTIME_DEPENDENCY_CHANGED,
    ANALYSIS_RECORD_FAILED
  }

  /** 固定事件原因码。 */
  public enum Reason {
    OK,
    DEMO_MODE,
    NO_PUBLISHED_DATA,
    DEPENDENCY_UNAVAILABLE,
    SCHEMA_MISMATCH,
    PREWARM_DEGRADED,
    STARTED,
    SOURCE_INVALID,
    SOURCE_CHANGED,
    CANCELLED,
    VERSION_CHANGED,
    NO_HINT,
    NO_NEW_SEGMENTS,
    INVALID_REQUEST,
    VERSION_CONFLICT,
    INTERNAL_ERROR,
    RECOVERED,
    ANALYSIS_WRITE_FAILED,
    PUBLISH_ROLLED_BACK
  }

  /** 允许输出的计数键。 */
  public enum Count {
    PREWARM_POSITIVE,
    PREWARM_NEGATIVE,
    INPUT_ROWS,
    PREPARED_ROWS,
    HINT_ROWS,
    BLOCKED_ROWS,
    LOOKUP_ROWS,
    INVALIDATED_POSITIVE,
    INVALIDATED_NEGATIVE,
    NEW_RANGES,
    QUERY_KEYS,
    POSITIVE_HITS,
    NEGATIVE_HITS,
    CACHE_MISSES,
    DB_BATCHES,
    VERSION_READS,
    PREWARM_READS,
    CANDIDATES,
    SELECTED,
    AMBIGUOUS,
    OVERLAP_DROPPED,
    ATTEMPTED_SEGMENTS
  }

  /** 请求终态的耗时步骤键。 */
  public enum Timing {
    VALIDATION,
    CANDIDATES,
    QUERY,
    SELECTION,
    ANALYSIS,
    API
  }

  /** 固定词库导入步骤。 */
  public enum ImportStep {
    SOURCE_CHECK,
    PREPARE,
    PERSIST,
    PUBLISH
  }

  /** 导入步骤生命周期阶段。 */
  public enum ImportPhase {
    STARTED,
    HEARTBEAT,
    COMPLETED
  }

  /** 固定导入准备规则计数键。 */
  public enum ReasonCount {
    NON_ASCII_LEMMA,
    OUTSIDE_QUERY_WINDOW,
    BASIC_VOCABULARY,
    UNSAFE_DEFAULT_CANDIDATE,
    ENGLISH_HEAVY_EXPANSION,
    NO_HAN_SOURCE,
    SPECIALIST_NOTATION,
    EMPTY_FIRST_CANDIDATE,
    NO_HAN_FIRST_CANDIDATE,
    ALL_BASIC_PHRASE,
    LOW_INFORMATION_PHRASE,
    CURATED,
    EXISTING_SAFE,
    UNRESOLVED,
    SOURCE_LABEL,
    ENGLISH_EXPANSION,
    ANGLE_TAG,
    PERSON_HEADER,
    LEADING_HAN,
    ACRONYM_EXPANSION,
    HAN_SPACING,
    MIXED_SPACING,
    TRAILING_PARENTHESIS,
    MEDICAL_INSERT
  }

  /** 固定日志级别。 */
  public enum Level {
    INFO,
    WARN,
    ERROR
  }

  /**
   * 校验固定事件 schema 及字段关系。
   *
   * @param type 含义：固定事件类型。取值范围：EventType。
   * @param reason 含义：固定原因码。取值范围：Reason。
   * @param durationMs 含义：单调时钟耗时毫秒。取值范围：非负 long。
   * @param counts 含义：允许计数。取值范围：仅事件白名单中的非负数。
   * @param lexiconVersion 含义：已知词库版本。取值范围：null 或非负 long。
   * @param requestId 含义：后端生成请求 UUID。取值范围：请求终态/敏感失败必填，其余为空。
   * @param timingsMs 含义：请求阶段耗时毫秒。取值范围：仅请求终态的非负值。
   * @param step 含义：导入步骤。取值范围：导入阶段事件必填。
   * @param phase 含义：导入阶段状态。取值范围：导入阶段事件必填。
   * @param reasonCounts 含义：导入规则计数。取值范围：仅导入终态固定规则键。
   */
  public StructuredEvent {
    Objects.requireNonNull(type);
    Objects.requireNonNull(reason);
    if (durationMs < 0 || (lexiconVersion != null && lexiconVersion < 0))
      throw new IllegalArgumentException("negative measurement");
    counts = Map.copyOf(Objects.requireNonNull(counts));
    timingsMs = Map.copyOf(Objects.requireNonNull(timingsMs));
    reasonCounts = Map.copyOf(Objects.requireNonNull(reasonCounts));
    counts
        .values()
        .forEach(
            v -> {
              if (v == null || v < 0) throw new IllegalArgumentException("invalid count");
            });
    timingsMs
        .values()
        .forEach(
            v -> {
              if (v == null || v < 0) throw new IllegalArgumentException("invalid timing");
            });
    reasonCounts
        .values()
        .forEach(
            v -> {
              if (v == null || v < 0) throw new IllegalArgumentException("invalid reason count");
            });
    if (!allowedReason(type, reason))
      throw new IllegalArgumentException("reason not allowed for event");
    if (type == EventType.CAPTION_REQUEST_COMPLETED) {
      if (requestId == null
          || !timingsMs.containsKey(Timing.API)
          || step != null
          || phase != null
          || !reasonCounts.isEmpty()) throw new IllegalArgumentException("invalid request event");
      allow(
          counts,
          Count.NEW_RANGES,
          Count.QUERY_KEYS,
          Count.POSITIVE_HITS,
          Count.NEGATIVE_HITS,
          Count.CACHE_MISSES,
          Count.DB_BATCHES,
          Count.VERSION_READS,
          Count.PREWARM_READS,
          Count.CANDIDATES,
          Count.SELECTED,
          Count.AMBIGUOUS,
          Count.OVERLAP_DROPPED);
      allow(
          timingsMs,
          Timing.VALIDATION,
          Timing.CANDIDATES,
          Timing.QUERY,
          Timing.SELECTION,
          Timing.ANALYSIS,
          Timing.API);
    } else {
      if ((type != EventType.ANALYSIS_RECORD_FAILED && requestId != null) || !timingsMs.isEmpty())
        throw new IllegalArgumentException("request id or timing forbidden");
      if (type != EventType.LEXICON_IMPORT_STAGE && (step != null || phase != null))
        throw new IllegalArgumentException("import fields forbidden");
      if (type != EventType.LEXICON_IMPORT_COMPLETED && !reasonCounts.isEmpty())
        throw new IllegalArgumentException("reason counts forbidden");
      switch (type) {
        case RUNTIME_START_COMPLETED ->
            allow(counts, Count.PREWARM_POSITIVE, Count.PREWARM_NEGATIVE);
        case LEXICON_IMPORT_STAGE -> {
          allow(counts, Count.INPUT_ROWS, Count.PREPARED_ROWS, Count.HINT_ROWS, Count.BLOCKED_ROWS);
          Objects.requireNonNull(step);
          Objects.requireNonNull(phase);
          if (phase == ImportPhase.STARTED && reason != Reason.STARTED
              || phase == ImportPhase.HEARTBEAT && reason != Reason.OK
              || phase == ImportPhase.COMPLETED && reason == Reason.STARTED
              || (phase == ImportPhase.STARTED || phase == ImportPhase.HEARTBEAT)
                  && !counts.isEmpty()) throw new IllegalArgumentException("invalid import phase");
        }
        case LEXICON_IMPORT_COMPLETED ->
            allow(
                counts,
                Count.INPUT_ROWS,
                Count.PREPARED_ROWS,
                Count.LOOKUP_ROWS,
                Count.BLOCKED_ROWS);
        case LEXICON_CACHE_VERSION_CHANGED ->
            allow(counts, Count.INVALIDATED_POSITIVE, Count.INVALIDATED_NEGATIVE);
        case RUNTIME_DEPENDENCY_CHANGED -> allow(counts);
        case ANALYSIS_RECORD_FAILED -> {
          if (requestId == null) throw new IllegalArgumentException("request id required");
          allow(counts, Count.ATTEMPTED_SEGMENTS);
        }
        default -> throw new IllegalArgumentException("unsupported type");
      }
    }
  }

  private static boolean allowedReason(EventType type, Reason reason) {
    return switch (type) {
      case RUNTIME_START_COMPLETED ->
          java.util.Set.of(
                  Reason.OK,
                  Reason.DEMO_MODE,
                  Reason.NO_PUBLISHED_DATA,
                  Reason.DEPENDENCY_UNAVAILABLE,
                  Reason.SCHEMA_MISMATCH,
                  Reason.PREWARM_DEGRADED)
              .contains(reason);
      case LEXICON_IMPORT_STAGE ->
          java.util.Set.of(
                  Reason.STARTED,
                  Reason.OK,
                  Reason.SOURCE_INVALID,
                  Reason.SOURCE_CHANGED,
                  Reason.CANCELLED,
                  Reason.DEPENDENCY_UNAVAILABLE)
              .contains(reason);
      case LEXICON_IMPORT_COMPLETED ->
          java.util.Set.of(
                  Reason.OK,
                  Reason.CANCELLED,
                  Reason.SOURCE_INVALID,
                  Reason.SOURCE_CHANGED,
                  Reason.PUBLISH_ROLLED_BACK)
              .contains(reason);
      case LEXICON_CACHE_VERSION_CHANGED -> reason == Reason.VERSION_CHANGED;
      case CAPTION_REQUEST_COMPLETED ->
          java.util.Set.of(
                  Reason.OK,
                  Reason.NO_HINT,
                  Reason.NO_NEW_SEGMENTS,
                  Reason.INVALID_REQUEST,
                  Reason.NO_PUBLISHED_DATA,
                  Reason.VERSION_CONFLICT,
                  Reason.DEPENDENCY_UNAVAILABLE,
                  Reason.INTERNAL_ERROR)
              .contains(reason);
      case RUNTIME_DEPENDENCY_CHANGED ->
          reason == Reason.DEPENDENCY_UNAVAILABLE || reason == Reason.RECOVERED;
      case ANALYSIS_RECORD_FAILED -> reason == Reason.ANALYSIS_WRITE_FAILED;
    };
  }

  @SafeVarargs
  private static <K> void allow(Map<K, Long> values, K... allowed) {
    for (var key : values.keySet()) {
      boolean found = false;
      for (var candidate : allowed) if (candidate.equals(key)) found = true;
      if (!found) throw new IllegalArgumentException("count key not allowed");
    }
  }

  /**
   * 根据事件枚举返回固定的 schema 事件名称，不接受自由文本。
   *
   * @return 固定 schema 事件名称。
   */
  public String eventName() {
    return switch (type) {
      case RUNTIME_START_COMPLETED -> "runtime.start.completed";
      case LEXICON_IMPORT_STAGE -> "lexicon.import.stage";
      case LEXICON_IMPORT_COMPLETED -> "lexicon.import.completed";
      case LEXICON_CACHE_VERSION_CHANGED -> "lexicon.cache.version_changed";
      case CAPTION_REQUEST_COMPLETED -> "caption.request.completed";
      case RUNTIME_DEPENDENCY_CHANGED -> "runtime.dependency.changed";
      case ANALYSIS_RECORD_FAILED -> "analysis.record.failed";
    };
  }

  /**
   * 根据事件类别返回固定处理阶段，用作低基数观测标签。
   *
   * @return 由事件类别派生的固定处理阶段。
   */
  public String stage() {
    return switch (type) {
      case RUNTIME_START_COMPLETED, RUNTIME_DEPENDENCY_CHANGED -> "startup";
      case LEXICON_IMPORT_STAGE, LEXICON_IMPORT_COMPLETED -> "import";
      case LEXICON_CACHE_VERSION_CHANGED -> "query";
      case CAPTION_REQUEST_COMPLETED -> "request";
      case ANALYSIS_RECORD_FAILED -> "analysis";
    };
  }

  /**
   * 根据固定原因码返回业务结果，调用者不能覆盖此映射。
   *
   * @return 由固定原因码派生的业务结果。
   */
  public String result() {
    return switch (reason) {
      case NO_PUBLISHED_DATA, DEPENDENCY_UNAVAILABLE, CANCELLED -> "BLOCKED";
      case ANALYSIS_WRITE_FAILED -> "FAIL";
      case INVALID_REQUEST,
          VERSION_CONFLICT,
          SCHEMA_MISMATCH,
          INTERNAL_ERROR,
          SOURCE_INVALID,
          SOURCE_CHANGED,
          PUBLISH_ROLLED_BACK ->
          "FAIL";
      default -> "PASS";
    };
  }

  /**
   * 从事件类别与固定原因导出日志输出级别。
   *
   * @return 由封闭事件和原因矩阵确定的固定日志级别。
   */
  public Level level() {
    if (type == EventType.RUNTIME_START_COMPLETED) {
      if (reason == Reason.PREWARM_DEGRADED) return Level.WARN;
      if (result().equals("BLOCKED") || result().equals("FAIL")) return Level.ERROR;
    }
    if (type == EventType.CAPTION_REQUEST_COMPLETED) {
      if (reason == Reason.INVALID_REQUEST) return Level.WARN;
      if (result().equals("FAIL") || result().equals("BLOCKED")) return Level.ERROR;
    }
    if (type == EventType.RUNTIME_DEPENDENCY_CHANGED)
      return reason == Reason.RECOVERED ? Level.INFO : Level.WARN;
    if (type == EventType.ANALYSIS_RECORD_FAILED || reason == Reason.CANCELLED) return Level.WARN;
    if (type == EventType.LEXICON_IMPORT_STAGE || type == EventType.LEXICON_IMPORT_COMPLETED)
      return result().equals("FAIL") || result().equals("BLOCKED") ? Level.ERROR : Level.INFO;
    return Level.INFO;
  }
}
