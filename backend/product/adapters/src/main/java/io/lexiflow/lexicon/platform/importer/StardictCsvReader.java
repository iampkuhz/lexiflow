package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Consumer;

/** 以有界内存读取 ECDICT 的 StarDict CSV，并投影为词库导入行。 */
final class StardictCsvReader {
  private static final List<String> REQUIRED_HEADERS =
      List.of(
          "word",
          "phonetic",
          "definition",
          "translation",
          "pos",
          "collins",
          "oxford",
          "tag",
          "bnc",
          "frq",
          "exchange",
          "detail",
          "audio");
  private static final Set<String> INFLECTION_KEYS = Set.of("p", "d", "i", "3", "r", "t", "s");
  private static final Set<String> COMPLEX_TAGS =
      Set.of("cet6", "ky", "toefl", "ielts", "gre", "sat");
  private static final Map<String, CuratedGloss> CURATED_GLOSSES =
      Map.of(
          "sustainability", new CuratedGloss("持续性", "可持续性"),
          "literally", new CuratedGloss("字面上地", "按字面意思"),
          "stream of data", new CuratedGloss("数据流", "数据流"));

  /** 判断输入首行是否为受支持的 StarDict CSV 合同。 */
  boolean matches(Path input) throws IOException {
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      return header != null && header.equals(REQUIRED_HEADERS);
    }
  }

  static final String PREPARATION_POLICY =
      "oxford-all-words-fixed-and-curated-gloss-first-candidate-v4";

  /** 预扫描收集来源明确标记的全部 Oxford 单词；缺排名不改变基础资格。 */
  BasicSelection selectBasicVocabulary(Path input) throws IOException {
    var order =
        java.util.Comparator.comparingLong(
                (BasicWord word) -> word.rank() > 0 ? word.rank() : Long.MAX_VALUE)
            .thenComparing(BasicWord::lemma);
    var selected = new java.util.TreeSet<BasicWord>(order);
    // 在派生行归并前收集证据，否则 bacteria 等只有词形被标记的基础词会丢失资格。
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      if (header == null || !header.equals(REQUIRED_HEADERS)) {
        throw new IllegalArgumentException("headers must exactly match ECDICT StarDict CSV");
      }
      var indexes = indexes(header);
      List<String> values;
      while ((values = reader.next()) != null) {
        if (values.size() != header.size()) {
          throw new IllegalArgumentException("source row has an unexpected column count");
        }
        var word = normalize(values.get(indexes.get("word")));
        if ("1".equals(normalize(values.get(indexes.get("oxford"))))
            && !word.contains(" ")
            && isSupportedSurface(word)) {
          selected.add(
              new BasicWord(
                  word, rank(values.get(indexes.get("bnc")), values.get(indexes.get("frq")))));
        }
      }
    }
    return new BasicSelection(List.copyOf(selected));
  }

  /** 读取前先冻结基础词集合，正式导入只查询该集合并保存资格。 */
  ScanResult read(Path input, Consumer<SourceRecord> consumer) throws IOException {
    return read(input, consumer, selectBasicVocabulary(input));
  }

  ScanResult read(Path input, Consumer<SourceRecord> consumer, BasicSelection selection)
      throws IOException {
    return scan(input, consumer, selection);
  }

  /** 逐条读取并转换来源记录；消费者只接收可发布的 lemma。 */
  private ScanResult scan(Path input, Consumer<SourceRecord> consumer, BasicSelection selection)
      throws IOException {
    Objects.requireNonNull(input, "input");
    Objects.requireNonNull(consumer, "consumer");
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      if (header == null || !header.equals(REQUIRED_HEADERS)) {
        throw new IllegalArgumentException("headers must exactly match ECDICT StarDict CSV");
      }
      var indexes = indexes(header);
      var sourceRows = 0L;
      var importableRows = 0L;
      var derivedRows = 0L;
      var noGlossRows = 0L;
      var unsupportedSurfaceRows = 0L;
      var basicRows = 0L;
      var deduplicatedRows = 0L;
      List<String> values;
      while ((values = reader.next()) != null) {
        sourceRows += 1;
        if (values.size() != header.size()) {
          throw new IllegalArgumentException(
              "source row " + sourceRows + " has an unexpected column count");
        }
        var converted = convert(values, indexes, sourceRows, selection);
        if (converted.reason() == OmissionReason.DERIVED) {
          derivedRows += 1;
        } else if (converted.reason() == OmissionReason.NO_GLOSS) {
          noGlossRows += 1;
        } else if (converted.reason() == OmissionReason.UNSUPPORTED_SURFACE) {
          unsupportedSurfaceRows += 1;
        } else {
          importableRows += 1;
          if (converted.record().row().basicVocabulary()) basicRows++;
          if (converted.record().glossDeduplicated()) deduplicatedRows++;
          consumer.accept(converted.record());
        }
      }
      return new ScanResult(
          sourceRows,
          importableRows,
          derivedRows,
          noGlossRows,
          unsupportedSurfaceRows,
          selection.words().size(),
          basicRows,
          deduplicatedRows);
    }
  }

  private static Conversion convert(
      Map<String, String> row, long sourceRow, BasicSelection selection) {
    var word = normalize(row.get("word"));
    if (isDerived(row.get("exchange"))) {
      return Conversion.omit(OmissionReason.DERIVED);
    }
    if (!isSupportedSurface(word)) {
      return Conversion.omit(OmissionReason.UNSUPPORTED_SURFACE);
    }
    var sourceGloss = cleanTranslation(row.get("translation"));
    if (sourceGloss.isBlank()) {
      return Conversion.omit(OmissionReason.NO_GLOSS);
    }
    var gloss = parseFirstStardictCandidate(sourceGloss);
    var curated = CURATED_GLOSSES.get(word);
    if (curated != null) {
      if (!sourceGloss.contains(curated.sourceExpression())) {
        throw new IllegalArgumentException("curated gloss source expression changed for " + word);
      }
      gloss = curated.displayGloss();
    }
    var rank = rank(row.get("bnc"), row.get("frq"));
    var complexTags = complexTags(row.get("tag"));
    var frequencyZipf = rank == 0 ? 0.0 : zipf(rank);
    var priority = LexiconPriority.fromRankedEvidence(frequencyZipf, complexTags.size(), rank != 0);
    var inflections = inflections(row.get("exchange"), word);
    var evidenceReference =
        "stardict.csv#"
            + sourceRow
            + ":"
            + word
            + ";bnc="
            + rawRank(row.get("bnc"))
            + ";frq="
            + rawRank(row.get("frq"))
            + ";rank="
            + (rank == 0 ? "missing" : rank)
            + ";formula=ecdict-bnc-frq-rank-calibrated-v1";
    var dictionary =
        new SourceReference("ecdict-stardict", "MIT", "stardict.csv#" + sourceRow + ":" + word);
    var frequency = new SourceReference("ecdict-bnc-frq", "MIT", evidenceReference);
    var complexEvidence =
        complexTags.stream()
            .map(
                tag ->
                    new SourceReference(
                        "ecdict-tag-" + tag, "MIT", "stardict.csv#" + sourceRow + ":" + word))
            .toList();
    var deduplicated =
        !sourceGloss.equals(
            io.lexiflow.lexicon.application.importing.validation.GlossPreparation.normalize(
                sourceGloss));
    return Conversion.record(
        new SourceRecord(
            sourceRow,
            new LexiconImportRow(
                word,
                gloss,
                cleanDefinition(row.get("definition")),
                List.of(),
                inflections,
                priority,
                dictionary,
                frequency,
                complexEvidence,
                rank != 0 && !"1".equals(normalize(row.get("oxford"))),
                !word.contains(" ")
                    && (selection.lemmas().contains(word)
                        || inflections.stream().anyMatch(selection.lemmas()::contains)),
                PREPARATION_POLICY + ";list_sha256=" + selection.digest() + ";" + evidenceReference,
                sourceGloss,
                parseRank(row.get("bnc")) > 0 ? parseRank(row.get("bnc")) : null,
                parseRank(row.get("frq")) > 0 ? parseRank(row.get("frq")) : null,
                complexTags.stream().sorted().toList(),
                "1".equals(normalize(row.get("oxford")))),
            "1".equals(normalize(row.get("oxford"))),
            rank,
            deduplicated));
  }

  private static Map<String, Integer> indexes(List<String> header) {
    var result = new HashMap<String, Integer>();
    for (var index = 0; index < header.size(); index++) {
      result.put(header.get(index), index);
    }
    return result;
  }

  private static Conversion convert(
      List<String> values, Map<String, Integer> indexes, long sourceRow, BasicSelection selection) {
    var row = new HashMap<String, String>();
    indexes.forEach((name, index) -> row.put(name, values.get(index)));
    return convert(row, sourceRow, selection);
  }

  private static boolean isDerived(String exchange) {
    for (var part : exchange.split("/", -1)) {
      var separator = part.indexOf(':');
      if (separator > 0 && part.substring(0, separator).trim().equals("0")) {
        return true;
      }
    }
    return false;
  }

  private static List<String> inflections(String exchange, String lemma) {
    var values = new HashSet<String>();
    for (var part : exchange.split("/", -1)) {
      var separator = part.indexOf(':');
      if (separator <= 0 || !INFLECTION_KEYS.contains(part.substring(0, separator).trim())) {
        continue;
      }
      var form = normalize(part.substring(separator + 1));
      if (!form.isBlank() && !form.equals(lemma) && isSupportedSurface(form)) {
        values.add(form);
      }
    }
    return values.stream().sorted().toList();
  }

  private static Set<String> complexTags(String tags) {
    var result = new HashSet<String>();
    for (var tag : normalize(tags).split(" ")) {
      if (COMPLEX_TAGS.contains(tag)) {
        result.add(tag);
      }
    }
    return Set.copyOf(result);
  }

  private static String cleanTranslation(String value) {
    var ordinary = new ArrayList<String>();
    var network = new ArrayList<String>();
    for (var line : value.replace("\\n", "\n").split("\\R")) {
      var cleaned = line.trim().replaceFirst("^(?:[a-z]{1,6}\\.)\\s*", "");
      if (cleaned.isBlank()) {
        continue;
      }
      if (cleaned.startsWith("[网络]")) {
        network.add(cleaned);
      } else {
        ordinary.add(cleaned);
      }
    }
    var selected = ordinary.isEmpty() ? network : ordinary;
    return String.join("；", selected).replaceAll("\\s+", " ").trim();
  }

  /**
   * 从已清洗的来源短释中解析首候选；括号外识别 StarDict 列举分隔符。
   *
   * <p>分隔符：{@code ； ; ， , 、}；括号 {@code () [] （） 【} 内部不切分。 不按句号、冒号、斜线或任意空白截断。
   * 括号必须类型配对且平衡；多余闭合、未闭合和错配返回空候选，由准备政策阻断展示。
   *
   * <p>首项为空时不补位，保留首项身份。
   */
  static String parseFirstStardictCandidate(String cleanedTranslation) {
    try {
      return parseStardictCandidates(cleanedTranslation).getFirst();
    } catch (IllegalArgumentException exception) {
      // 释义内部结构不可靠只阻断本词条；完整来源仍入库，不猜测或补选。
      return "";
    }
  }

  /**
   * 将已清洗的来源短释按 StarDict 分隔符切分为有序候选列表。
   *
   * <p>使用栈追踪括号配对，支持英文/中文圆括号和方括号。 分隔符仅在括号深度为零时切分。 多余闭合、未闭合和错配抛出异常。 保留首项身份，空首项不丢弃。
   */
  static List<String> parseStardictCandidates(String cleanedTranslation) {
    var points = cleanedTranslation.codePoints().toArray();
    var splits = splitRespectingBrackets(points);
    var candidates = new ArrayList<String>();
    for (var segment : splits) {
      candidates.add(cleanStarDictCandidate(segment));
    }
    return candidates;
  }

  /**
   * 在括号深度为零的分隔符处切分，同时校验括号配对。
   *
   * @return 切分后的原始片段列表（未清理），至少包含一个元素。
   */
  private static List<String> splitRespectingBrackets(int[] points) {
    var stack = new ArrayList<Integer>();
    var segments = new ArrayList<String>();
    var current = new StringBuilder();
    for (var point : points) {
      var closer = bracketCloser(point);
      if (closer != 0) {
        stack.add(closer);
        current.appendCodePoint(point);
      } else if (isBracketCloser(point)) {
        if (stack.isEmpty() || stack.removeLast() != point) {
          throw new IllegalArgumentException("mismatched closing bracket in gloss");
        }
        current.appendCodePoint(point);
      } else if (stack.isEmpty() && "；;，,、".indexOf(point) >= 0) {
        segments.add(current.toString());
        current.setLength(0);
      } else {
        current.appendCodePoint(point);
      }
    }
    if (!stack.isEmpty()) {
      throw new IllegalArgumentException("unclosed opening bracket in gloss");
    }
    segments.add(current.toString());
    return segments;
  }

  /** 返回与开括号配对的闭括号 code point；非开括号返回 0。 */
  private static int bracketCloser(int codePoint) {
    return switch (codePoint) {
      case '(' -> ')';
      case '（' -> '）';
      case '[' -> ']';
      case '【' -> '】';
      default -> 0;
    };
  }

  /** 判断是否为闭括号。 */
  private static boolean isBracketCloser(int codePoint) {
    return codePoint == ')' || codePoint == '）' || codePoint == ']' || codePoint == '】';
  }

  /** 去除明确词性前缀与领域括号前缀。 */
  private static String cleanStarDictCandidate(String raw) {
    var trimmed =
        raw.trim()
            .replaceFirst("^(?:[a-z]{1,6}\\.)\\s*", "")
            .replaceFirst("^\\[[\\p{IsHan}A-Za-z]{1,12}\\] *", "");
    return trimmed.trim();
  }

  /**
   * 人工审阅且可从当前来源核对的少量短释，不通用截取多义列表首项。
   *
   * @param sourceExpression 来源译文必须包含的表达。
   * @param displayGloss 经人工审阅后发布的简短中文释义。
   */
  private record CuratedGloss(String sourceExpression, String displayGloss) {}

  private static String cleanDefinition(String value) {
    return value.replace("\\n", "\n").replaceAll("\\s+", " ").trim();
  }

  private static long rank(String bnc, String frq) {
    var left = parseRank(bnc);
    var right = parseRank(frq);
    if (left == 0) {
      return right;
    }
    if (right == 0) {
      return left;
    }
    return Math.min(left, right);
  }

  private static long parseRank(String value) {
    try {
      var parsed = Long.parseLong(value.trim());
      return parsed > 0 ? parsed : 0;
    } catch (NumberFormatException exception) {
      return 0;
    }
  }

  private static String rawRank(String value) {
    return value.trim().isEmpty() ? "missing" : value.trim();
  }

  private static double zipf(long rank) {
    var raw = Math.max(0.0, Math.min(8.0, 8.0 - Math.log10(rank)));
    return java.math.BigDecimal.valueOf(raw)
        .setScale(2, java.math.RoundingMode.HALF_UP)
        .doubleValue();
  }

  private static boolean isSupportedSurface(String value) {
    var tokens = value.split(" ");
    if (tokens.length < 1 || tokens.length > 5) {
      return false;
    }
    for (var token : tokens) {
      if (!token.matches("[\\p{IsAlphabetic}][\\p{IsAlphabetic}'-]*")) {
        return false;
      }
    }
    return true;
  }

  private static String normalize(String value) {
    return java.text.Normalizer.normalize(value, java.text.Normalizer.Form.NFC)
        .trim()
        .replaceAll("\\s+", " ")
        .toLowerCase(Locale.ROOT);
  }

  /**
   * 一条可发布来源记录及其物理来源行号。
   *
   * @param sourceRow 一起计入表头之后的物理来源行号
   * @param row 已规范化的导入行
   * @param oxfordBasic 来源是否标记 oxford=1
   * @param rank 有效 BNC/FRQ 排名的较小值，缺失为零
   * @param glossDeduplicated 是否合并了完全相同的重复表达
   */
  record SourceRecord(
      long sourceRow,
      LexiconImportRow row,
      boolean oxfordBasic,
      long rank,
      boolean glossDeduplicated) {}

  /**
   * 流式扫描的可审计汇总。
   *
   * @param sourceRows 已读取的原始数据行数
   * @param importableRows 可发布的 lemma 数
   * @param derivedRows 被归并到 root lemma 的派生行数
   * @param noGlossRows 缺少中文释义而跳过的行数
   * @param unsupportedSurfaceRows 超出首版匹配表面范围而跳过的行数
   * @param selectedBasicLemmas 由来源 Oxford 标记选定的基础 lemma 数
   * @param basicRows 合并固定功能词后的基础词条数
   * @param deduplicatedRows 重复表达清洗行数
   */
  record ScanResult(
      long sourceRows,
      long importableRows,
      long derivedRows,
      long noGlossRows,
      long unsupportedSurfaceRows,
      int selectedBasicLemmas,
      long basicRows,
      long deduplicatedRows) {}

  /**
   * 来源明确标记的基础词；排名只用于报告排序。
   *
   * @param lemma 已归一 lemma。
   * @param rank 公开来源中的正排名，缺失时为零。
   */
  record BasicWord(String lemma, long rank) {}

  /** 冻结的来源基础词清单；只收集 Oxford 单词，摘要不含用户资料。 */
  static final class BasicSelection {
    private final List<BasicWord> words;
    private final Set<String> lemmas;
    private final String digest;

    BasicSelection(List<BasicWord> words) {
      this.words = List.copyOf(words);
      this.lemmas =
          words.stream()
              .map(BasicWord::lemma)
              .collect(java.util.stream.Collectors.toUnmodifiableSet());
      try {
        var sha = java.security.MessageDigest.getInstance("SHA-256");
        sha.update((PREPARATION_POLICY + "\n").getBytes(StandardCharsets.UTF_8));
        words.forEach(
            word ->
                sha.update(
                    (word.rank() + "\t" + word.lemma() + "\n").getBytes(StandardCharsets.UTF_8)));
        io.lexiflow.lexicon.application.importing.validation.BasicVocabulary.fixedWords().stream()
            .sorted()
            .forEach(
                word -> sha.update(("fixed\t" + word + "\n").getBytes(StandardCharsets.UTF_8)));
        digest = java.util.HexFormat.of().formatHex(sha.digest());
      } catch (java.security.NoSuchAlgorithmException exception) {
        throw new IllegalStateException(exception);
      }
    }

    List<BasicWord> words() {
      return words;
    }

    Set<String> lemmas() {
      return lemmas;
    }

    String digest() {
      return digest;
    }
  }

  /** 不能构成可发布 entry 的来源记录分类。 */
  private enum OmissionReason {
    DERIVED,
    NO_GLOSS,
    UNSUPPORTED_SURFACE
  }

  /**
   * 单条转换成功结果或明确跳过原因。
   *
   * @param record 转换成功时的记录
   * @param reason 跳过记录时的分类
   */
  private record Conversion(SourceRecord record, OmissionReason reason) {
    static Conversion record(SourceRecord record) {
      return new Conversion(record, null);
    }

    static Conversion omit(OmissionReason reason) {
      return new Conversion(null, reason);
    }
  }

  /** 最小 RFC 4180 流式记录读取器；不会把整个 CSV 保存在内存。 */
  private static final class CsvRecordReader implements AutoCloseable {
    private final BufferedReader reader;

    CsvRecordReader(BufferedReader reader) {
      this.reader = reader;
    }

    List<String> next() throws IOException {
      var row = new ArrayList<String>();
      var cell = new StringBuilder();
      var quoted = false;
      var sawAny = false;
      int read;
      while ((read = reader.read()) != -1) {
        sawAny = true;
        var character = (char) read;
        if (character == '"') {
          if (!quoted && !cell.isEmpty()) {
            throw new IllegalArgumentException("CSV quote must begin a field");
          }
          if (quoted) {
            reader.mark(1);
            var next = reader.read();
            if (next == '"') {
              cell.append('"');
            } else {
              quoted = false;
              if (next != -1) {
                reader.reset();
              }
            }
          } else {
            quoted = true;
          }
        } else if (character == ',' && !quoted) {
          row.add(cell.toString());
          cell.setLength(0);
        } else if ((character == '\n' || character == '\r') && !quoted) {
          if (character == '\r') {
            reader.mark(1);
            if (reader.read() != '\n') {
              reader.reset();
            }
          }
          row.add(cell.toString());
          return List.copyOf(row);
        } else {
          cell.append(character);
        }
      }
      if (quoted) {
        throw new IllegalArgumentException("CSV contains an unclosed quoted field");
      }
      if (!sawAny) {
        return null;
      }
      row.add(cell.toString());
      return List.copyOf(row);
    }

    @Override
    public void close() throws IOException {
      reader.close();
    }
  }
}
