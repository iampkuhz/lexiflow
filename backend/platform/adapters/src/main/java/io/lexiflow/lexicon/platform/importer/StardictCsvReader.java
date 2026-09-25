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

  /** 判断输入首行是否为受支持的 StarDict CSV 合同。 */
  boolean matches(Path input) throws IOException {
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      return header != null && header.equals(REQUIRED_HEADERS);
    }
  }

  static final String PREPARATION_POLICY = "oxford-ranked-top2000-fixed-and-identical-gloss-v1";

  /** 预扫描只保留 2000 个有来源排名的基础 lemma，不把全部词库装入内存。 */
  BasicSelection selectBasicVocabulary(Path input) throws IOException {
    var order = java.util.Comparator.comparingLong(BasicWord::rank).thenComparing(BasicWord::lemma);
    var selected = new java.util.TreeSet<BasicWord>(order);
    scan(
        input,
        source -> {
          if (source.oxfordBasic() && source.rank() > 0 && !source.row().lemma().contains(" ")) {
            selected.add(new BasicWord(source.row().lemma(), source.rank()));
            if (selected.size() > 2000) selected.pollLast();
          }
        },
        new BasicSelection(List.of()));
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
    var gloss = cleanTranslation(row.get("translation"));
    if (gloss.isBlank()) {
      return Conversion.omit(OmissionReason.NO_GLOSS);
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
                selection.lemmas().contains(word),
                PREPARATION_POLICY
                    + ";list_sha256="
                    + selection.digest()
                    + ";"
                    + evidenceReference),
            "1".equals(normalize(row.get("oxford"))),
            rank,
            !gloss.equals(
                io.lexiflow.lexicon.application.importing.validation.GlossPreparation.normalize(
                    gloss))));
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
   * @param selectedBasicLemmas 由来源和排名选定的基础 lemma 数
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
   * 有明确来源排名的基础词。
   *
   * @param lemma 已归一 lemma。
   * @param rank 公开来源中的正排名。
   */
  record BasicWord(String lemma, long rank) {}

  /** 冻结的有界基础词清单；摘要不含用户资料。 */
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
