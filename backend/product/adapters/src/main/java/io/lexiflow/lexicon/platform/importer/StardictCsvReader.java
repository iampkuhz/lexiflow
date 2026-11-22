package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation;
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
  private static final List<String> COMPACT_HEADERS =
      List.of("word", "translation", "oxford", "tag", "bnc", "frq", "exchange");
  private static final Set<String> INFLECTION_KEYS = Set.of("p", "d", "i", "3", "r", "t", "s");
  private static final Set<String> COMPLEX_TAGS =
      Set.of("cet6", "ky", "toefl", "ielts", "gre", "sat");
  private static final Map<String, CuratedGloss> CURATED_GLOSSES =
      Map.ofEntries(
          Map.entry("sustainability", new CuratedGloss("持续性", "可持续性")),
          Map.entry("literally", new CuratedGloss("字面上地", "按字面意思")),
          Map.entry("stream of data", new CuratedGloss("数据流", "数据流")),
          Map.entry(
              "attribute",
              new CuratedGloss(
                  "把...归于",
                  "归因于",
                  "e3dae84e346bdeb814e916ce57ac93dbae157a55d54f9a00d1992988c7f59564",
                  "WordNet-3.1:attribute%2:31:00::;tag=16;nouns=3;Cambridge:attribute-to;Collins:attribute")),
          Map.entry(
              "efficiently",
              new CuratedGloss(
                  "效率高",
                  "效率高",
                  "70ef11e0296f3debf74c7d45aed2ca39f26c801ace24c76f7ebbf37c7948ee44",
                  "WordNet-3.1:efficiently%4:02:00::;tag=2;Cambridge:efficiently")),
          Map.entry(
              "plane",
              new CuratedGloss(
                  "飞机",
                  "飞机",
                  "ea5c7814fc717ca770cde445c7e3d5e397208c0a6d02b341f16a8ebf3265a5f9",
                  "WordNet-3.1:plane%1:06:01::;tag=21;mathematical=16")),
          Map.entry(
              "tissue",
              new CuratedGloss(
                  "组织",
                  "组织",
                  "84767de2630ded151da2f28e6daad5dba7140f987670123e018970ede2dfa229",
                  "WordNet-3.1:tissue%1:08:00::;tag=37;paper=4")),
          Map.entry(
              "equal",
              new CuratedGloss(
                  "相等的",
                  "相等的",
                  "59aaa94c11255906698a442855aaad7978569a35f49052ca1c7233b90a67c2f6",
                  "WordNet-3.1:equal%3:00:00::;tag=31;verb=13;noun=1")),
          Map.entry(
              "nerve",
              new CuratedGloss(
                  "神经",
                  "神经",
                  "1ef31eff25595e4f1f1acf5b41d037fda72ad42fcd220fd0d04cc5f3e4d03ad4",
                  "WordNet-3.1:nerve%1:08:00::;tag=6;courage=5;Cambridge:nerve")),
          Map.entry(
              "mostly",
              new CuratedGloss(
                  "大部分",
                  "大部分",
                  "f8c7c09e5f216be2e6863eecd07eab77903c702b8b500f63f1ea4f63687aa3d9",
                  "WordNet-3.1:mostly%4:02:00::;tag=10;usually=3")),
          Map.entry(
              "cook",
              new CuratedGloss(
                  "烹调",
                  "烹调",
                  "36432337d802deb0b7a9515cc1128cf36a09b6b3a9e1eaa35e24837422085361",
                  "WordNet-3.1:cook%2:36:00::;tag=9;meal=9;person=7")),
          Map.entry(
              "wrap",
              new CuratedGloss(
                  "包",
                  "包",
                  "5760f17ee1c137ed9f94a560bcc4d1aa3fd9c05ffa7346fd67133bfe39732f11",
                  "WordNet-3.1:wrap%2:35:00::;tag=5;coil=4;cloak=1;Oxford:wrap_1")),
          Map.entry(
              "spinal",
              new CuratedGloss(
                  "脊柱的",
                  "脊柱的",
                  "f71d9406cbe30275a47757067f78633a2345300403dd06680b4e1fac0454d1fb",
                  "Oxford:spinal;WordNet-3.1:spinal%3:01:00::;tag=0;quality-not-frequency")));
  // 仅此已核混合行有独立土地义；完整来源关系锁防止把修正套在漂移后的来源上。
  private static final String GROUND_SOURCE_EXPRESSION = "土地";
  private static final Set<String> GROUND_SOURCE_EXCHANGE =
      Set.of("s:grounds", "d:grounded", "p:grounded", "0:grind", "1:dp", "i:grinding", "3:grinds");
  private static final Set<String> GROUND_OWN_EXCHANGE =
      Set.of("s:grounds", "d:grounded", "p:grounded");

  /** 判断输入首行是否为受支持的 StarDict CSV 合同。 */
  boolean matches(Path input) throws IOException {
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      return header != null && (header.equals(REQUIRED_HEADERS) || header.equals(COMPACT_HEADERS));
    }
  }

  static final String PREPARATION_POLICY =
      "deterministic-preparation-bnc-ranked-oxford-and-bounded-cleaning-v10;independent_ground_sha256="
          + groundCorrectionDigest()
          + ";curated_sha256="
          + curatedGlossDigest()
          + ";lexical_evidence_sha256="
          + StardictLexicalEvidence.digest()
          + ";reviewed_defaults_sha256="
          + ReviewedSourceDefaults.digest()
          + ";reviewed_pos_sha256="
          + StardictLexicalEvidence.reviewDigest();

  private static String groundCorrectionDigest() {
    try {
      var sha = java.security.MessageDigest.getInstance("SHA-256");
      updateField(sha, "ground");
      updateField(sha, GROUND_SOURCE_EXPRESSION);
      GROUND_SOURCE_EXCHANGE.stream().sorted().forEach(value -> updateField(sha, value));
      GROUND_OWN_EXCHANGE.stream().sorted().forEach(value -> updateField(sha, value));
      return java.util.HexFormat.of().formatHex(sha.digest());
    } catch (java.security.NoSuchAlgorithmException exception) {
      throw new IllegalStateException(exception);
    }
  }

  private static String curatedGlossDigest() {
    try {
      var sha = java.security.MessageDigest.getInstance("SHA-256");
      CURATED_GLOSSES.entrySet().stream()
          .sorted(Map.Entry.comparingByKey())
          .forEach(
              entry -> {
                updateField(sha, entry.getKey());
                updateField(sha, entry.getValue().sourceExpression());
                updateField(sha, entry.getValue().displayGloss());
                updateField(sha, entry.getValue().sourceSha256());
                updateField(sha, entry.getValue().reviewReference());
              });
      return java.util.HexFormat.of().formatHex(sha.digest());
    } catch (java.security.NoSuchAlgorithmException exception) {
      throw new IllegalStateException(exception);
    }
  }

  private static void updateField(java.security.MessageDigest digest, String value) {
    var bytes = value.getBytes(StandardCharsets.UTF_8);
    digest.update(java.nio.ByteBuffer.allocate(Integer.BYTES).putInt(bytes.length).array());
    digest.update(bytes);
  }

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
      if (header == null || !supportedHeader(header)) {
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
    var rootForms = rootForms(input);
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      if (header == null || !supportedHeader(header)) {
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
        var converted = convert(values, indexes, sourceRows, selection, rootForms);
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
      Map<String, String> row,
      long sourceRow,
      BasicSelection selection,
      Map<String, Set<String>> rootForms) {
    var word = normalize(row.get("word"));
    var reviewedDefault =
        ReviewedSourceDefaults.select(word, row.get("translation"), row.get("exchange"));
    var curated = reviewedDefault == null ? CURATED_GLOSSES.get(word) : null;
    var sourceGloss = cleanTranslation(row.get("translation"));
    if (curated != null) curated.validateSource(word, sourceGloss);
    var independentGround = word.equals("ground");
    if (independentGround) {
      var exchange = List.of(row.get("exchange").split("/", -1));
      if (!parseFirstStardictCandidate(cleanTranslation(row.get("translation")))
              .equals(GROUND_SOURCE_EXPRESSION)
          || exchange.size() != GROUND_SOURCE_EXCHANGE.size()
          || !Set.copyOf(exchange).equals(GROUND_SOURCE_EXCHANGE)) {
        throw new IllegalArgumentException(
            "independent ground source expression or exchange changed");
      }
    } else if (isDerived(row.get("exchange"))
        && !(reviewedDefault != null && reviewedDefault.independent())
        && !StardictLexicalEvidence.independent(
            word, row.get("translation"), row.get("exchange"))) {
      return Conversion.omit(OmissionReason.DERIVED);
    }
    if (!isSupportedSurface(word)) {
      return Conversion.omit(OmissionReason.UNSUPPORTED_SURFACE);
    }
    if (sourceGloss.isBlank()) {
      return Conversion.omit(OmissionReason.NO_GLOSS);
    }
    var gloss = parseFirstStardictCandidate(sourceGloss);
    var lexical =
        curated == null && reviewedDefault == null
            ? StardictLexicalEvidence.select(word, row.get("translation"))
            : null;
    if (reviewedDefault != null) gloss = reviewedDefault.gloss();
    else if (curated != null) gloss = curated.displayGloss();
    else if (lexical != null) gloss = lexical.gloss();
    var bncRank = parseRank(row.get("bnc"));
    var rank = rank(row.get("bnc"), row.get("frq"));
    var complexTags = complexTags(row.get("tag"));
    var frequencyZipf = rank == 0 ? 0.0 : zipf(rank);
    var priority = LexiconPriority.fromRankedEvidence(frequencyZipf, complexTags.size(), rank != 0);
    var inflections =
        inflections(
            independentGround
                ? String.join("/", GROUND_OWN_EXCHANGE.stream().sorted().toList())
                : ownedExchange(row, rootForms),
            word);
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
        reviewedDefault != null && reviewedDefault.origin().equals("project-reviewed")
            ? new SourceReference(
                "lexiflow-reviewed-source-defaults",
                "MIT",
                "original short gloss; "
                    + reviewedDefault.factUrl()
                    + "; "
                    + reviewedDefault.basis())
            : new SourceReference(
                "ecdict-stardict", "MIT", "stardict.csv#" + sourceRow + ":" + word);
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
                cleanDefinition(row.getOrDefault("definition", "")),
                List.of(),
                inflections,
                priority,
                dictionary,
                frequency,
                complexEvidence,
                rank != 0 && !"1".equals(normalize(row.get("oxford"))),
                !word.contains(" ")
                    && bncRank == 0
                    && (selection.lemmas().contains(word)
                        || inflections.stream().anyMatch(selection.lemmas()::contains)),
                PREPARATION_POLICY
                    + ";list_sha256="
                    + selection.digest()
                    + ";"
                    + evidenceReference
                    + (lexical == null ? "" : ";" + lexical.reference())
                    + (reviewedDefault == null ? "" : ";quality=" + reviewedDefault.factUrl()),
                sourceGloss,
                bncRank > 0 ? bncRank : null,
                parseRank(row.get("frq")) > 0 ? parseRank(row.get("frq")) : null,
                complexTags.stream().sorted().toList(),
                "1".equals(normalize(row.get("oxford"))),
                selection.allBasicPhrase(word),
                curated != null || lexical != null || reviewedDefault != null),
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

  private static boolean supportedHeader(List<String> header) {
    return header.equals(REQUIRED_HEADERS) || header.equals(COMPACT_HEADERS);
  }

  private static Conversion convert(
      List<String> values,
      Map<String, Integer> indexes,
      long sourceRow,
      BasicSelection selection,
      Map<String, Set<String>> rootForms) {
    var row = new HashMap<String, String>();
    indexes.forEach((name, index) -> row.put(name, values.get(index)));
    return convert(row, sourceRow, selection, rootForms);
  }

  private static Map<String, Set<String>> rootForms(Path input) throws IOException {
    var result = new HashMap<String, Set<String>>();
    try (var reader = new CsvRecordReader(Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = reader.next();
      if (header == null || !supportedHeader(header))
        throw new IllegalArgumentException("headers must exactly match ECDICT StarDict CSV");
      int word = header.indexOf("word");
      int exchange = header.indexOf("exchange");
      List<String> row;
      while ((row = reader.next()) != null) {
        if (row.size() != header.size())
          throw new IllegalArgumentException("invalid source row width");
        if (row.get(exchange).isBlank()) continue;
        String lemma = normalize(row.get(word));
        result.putIfAbsent(lemma, Set.copyOf(inflections(row.get(exchange), lemma)));
      }
    }
    return result;
  }

  private static String ownedExchange(Map<String, String> row, Map<String, Set<String>> roots) {
    String word = normalize(row.get("word"));
    String exchange = row.get("exchange");
    if (!isDerived(exchange)) return exchange;
    var inherited = new HashSet<String>();
    boolean foundRoot = false;
    boolean otherRoot = false;
    for (var part : exchange.split("/", -1)) {
      if (!part.startsWith("0:")) continue;
      String root = normalize(part.substring(2));
      if (root.equals(word)) continue;
      otherRoot = true;
      var forms = roots.get(root);
      if (forms != null) {
        inherited.addAll(forms);
        foundRoot = true;
      }
    }
    if (!otherRoot) return exchange;
    var own = new ArrayList<String>();
    for (var part : exchange.split("/", -1)) {
      int colon = part.indexOf(':');
      if (colon <= 0 || !INFLECTION_KEYS.contains(part.substring(0, colon))) continue;
      String form = normalize(part.substring(colon + 1));
      if (foundRoot && !inherited.contains(form)) own.add(part);
    }
    return String.join("/", own);
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
      if (!form.isBlank()
          && !form.equals(lemma)
          && isSupportedSurface(form)
          && !(ReviewedSourceDefaults.entries().containsKey(form)
              && ReviewedSourceDefaults.entries().get(form).independent())) {
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

  static String cleanTranslation(String value) {
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
    return StardictGlossPreparation.firstCandidate(cleanedTranslation);
  }

  /** 完整解析保持首项和来源顺序；共享解析器拒绝整段括号错误。 */
  static List<String> parseStardictCandidates(String cleanedTranslation) {
    return StardictGlossPreparation.candidates(cleanedTranslation);
  }

  /**
   * 已受核的有限默认义；来源锁与依据一起进入准备身份，不代表全库义频保证。
   *
   * @param sourceExpression 非 null、非空白的原始来源锚点；新增受核项必须是确切解析候选，可含词典省略号，不用于展示
   * @param displayGloss 非 null、符合安全短释规则的展示义；可为有明确词典依据的受核中文，不伪称来自原来源
   * @param sourceSha256 非 null 的完整清理后来源摘要；原三项为空字符串，新增受核项为 64 位十六进制 SHA-256
   * @param reviewReference 非 null 的依据标识字符串；原三项使用 existing-reviewed-gloss，新增项记录受核依据
   */
  private record CuratedGloss(
      String sourceExpression, String displayGloss, String sourceSha256, String reviewReference) {
    CuratedGloss(String sourceExpression, String displayGloss) {
      this(sourceExpression, displayGloss, "", "existing-reviewed-gloss");
    }

    CuratedGloss {
      if (sourceExpression.isBlank()
          || !StardictGlossPreparation.safeGloss(displayGloss)
          || (!sourceSha256.isEmpty() && reviewReference.isBlank()))
        throw new IllegalArgumentException("unsafe curated default");
    }

    void validateSource(String word, String sourceGloss) {
      if (sourceSha256.isEmpty()) {
        if (!sourceGloss.contains(sourceExpression))
          throw new IllegalArgumentException("curated gloss source expression changed for " + word);
        return;
      }
      try {
        var actual =
            java.util.HexFormat.of()
                .formatHex(
                    java.security.MessageDigest.getInstance("SHA-256")
                        .digest(sourceGloss.getBytes(StandardCharsets.UTF_8)));
        if (!actual.equals(sourceSha256)
            || !parseStardictCandidates(sourceGloss).contains(sourceExpression))
          throw new IllegalArgumentException("reviewed default source changed for " + word);
      } catch (java.security.NoSuchAlgorithmException exception) {
        throw new IllegalStateException(exception);
      }
    }
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

    /** 逐个空格词元查询冻结基础集合；不推导词形、不查询模型。 */
    boolean allBasicPhrase(String lemma) {
      var tokens = lemma.split(" ");
      return tokens.length >= 2
          && java.util.Arrays.stream(tokens)
              .allMatch(
                  token ->
                      lemmas.contains(token)
                          || io.lexiflow.lexicon.application.importing.validation.BasicVocabulary
                              .contains(token));
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
  static final class CsvRecordReader implements AutoCloseable {
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
