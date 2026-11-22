package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

/** 固定义频资料的离线投影；只帮助选择来源词性，不产生中文或实时语境判断。 */
final class StardictLexicalEvidence {
  static final String HEADER = "lemma\tn\tv\ta\tr\tpos\tsynsets";
  private static final Pattern POS = Pattern.compile("^(n|a|adj|adv|v|vt|vi)\\.\\s*(.+)$");

  private StardictLexicalEvidence() {}

  static String digest() {
    return sha256(resource("wordnet-lexical-evidence.tsv"));
  }

  static String reviewDigest() {
    return sha256(resource("reviewed-pos-defaults.tsv"));
  }

  static Map<String, Evidence> entries() {
    return Holder.ENTRIES;
  }

  static Map<String, Evidence> parse(String text) {
    var lines = text.lines().toList();
    if (lines.isEmpty() || !lines.getFirst().equals(HEADER))
      throw new IllegalArgumentException("lexical evidence header invalid");
    var result = new HashMap<String, Evidence>();
    for (var line : lines.subList(1, lines.size())) {
      var fields = line.split("\\t", -1);
      if (fields.length != 7 || fields[0].isBlank())
        throw new IllegalArgumentException("lexical evidence row invalid");
      var counts = new HashMap<String, Long>();
      for (int i = 0; i < 4; i++) {
        long count = Long.parseLong(fields[i + 1]);
        if (count < 0) throw new IllegalArgumentException("negative lexical count");
        counts.put(List.of("n", "v", "a", "r").get(i), count);
      }
      var pos = new TreeSet<String>();
      for (var value : fields[5].split("")) {
        if (!counts.containsKey(value) || !pos.add(value))
          throw new IllegalArgumentException("lexical POS invalid");
      }
      var synsets = Set.of(fields[6].split("\\|"));
      if (synsets.isEmpty()
          || synsets.stream().anyMatch(value -> !value.matches("omw-en-[0-9]{8}-[nvars]")))
        throw new IllegalArgumentException("lexical synset identity invalid");
      if (result.putIfAbsent(fields[0], new Evidence(Map.copyOf(counts), Set.copyOf(pos), synsets))
          != null) throw new IllegalArgumentException("duplicate lexical lemma");
    }
    return Map.copyOf(result);
  }

  static boolean independent(String word, String translation, String exchange) {
    var evidence = entries().get(word);
    if (evidence == null
        || lines(translation).stream()
            .noneMatch(line -> evidence.pos().contains(partOfSpeech(line)))) return false;
    var independent = new java.util.HashSet<>(evidence.synsets());
    boolean relation = false;
    for (var part : exchange.split("/", -1)) {
      if (!part.startsWith("0:")) continue;
      relation = true;
      String root = part.substring(2).strip().toLowerCase(java.util.Locale.ROOT);
      if (root.equals(word)) continue;
      var origin = entries().get(root);
      if (origin == null) return false;
      independent.removeAll(origin.synsets());
    }
    return relation && !independent.isEmpty();
  }

  static Selection select(String word, String translation) {
    var evidence = entries().get(word);
    if (evidence == null || word.contains(" ")) return null;
    var ordered =
        evidence.counts().entrySet().stream()
            .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
            .toList();
    var top = ordered.getFirst();
    long runner = ordered.get(1).getValue();
    // 至少五次且严格领先、达到次位两倍；历史计数不被冒称现代总体概率。
    if (top.getValue() < 5 || top.getValue() <= runner || top.getValue() < 2 * runner) return null;
    var sourceLines = lines(translation);
    if (sourceLines.isEmpty()
        || partOfSpeech(sourceLines.getFirst()).isEmpty()
        || partOfSpeech(sourceLines.getFirst()).equals(top.getKey())) return null;
    for (var line : sourceLines) {
      if (!partOfSpeech(line).equals(top.getKey())) continue;
      var candidate = StardictGlossPreparation.firstCandidate(line);
      if (!StardictGlossPreparation.safeGloss(candidate)) return null;
      var reviewed = Holder.REVIEWED.get(word);
      var normalized =
          translation.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n");
      if (reviewed == null
          || !reviewed
              .translationSha256()
              .equals(sha256(normalized.getBytes(StandardCharsets.UTF_8)))
          || !reviewed.gloss().equals(candidate)) return null;
      return new Selection(
          candidate,
          "PWN3.0/omw-en2.0:dominant-pos="
              + top.getKey()
              + ";count="
              + top.getValue()
              + ";runner="
              + runner);
    }
    return null;
  }

  static List<String> lines(String text) {
    return text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
        .lines()
        .map(String::strip)
        .filter(line -> !line.isEmpty())
        .toList();
  }

  static String partOfSpeech(String line) {
    var match = POS.matcher(line);
    if (!match.matches()) return "";
    return switch (match.group(1)) {
      case "n" -> "n";
      case "a", "adj" -> "a";
      case "adv" -> "r";
      default -> "v";
    };
  }

  static String sha256(byte[] bytes) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    } catch (java.security.NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private static byte[] resource(String name) {
    try (var in = StardictLexicalEvidence.class.getResourceAsStream("/lexicon/" + name)) {
      if (in == null) throw new IllegalStateException("lexical evidence resource missing");
      return in.readAllBytes();
    } catch (IOException failure) {
      throw new IllegalStateException("lexical evidence unreadable", failure);
    }
  }

  /** 延迟加载；生成器可在资源尚未生成时独立执行。 */
  private static final class Holder {
    private static final Map<String, Evidence> ENTRIES = load();
    private static final Map<String, Reviewed> REVIEWED = loadReviewed();

    private static Map<String, Reviewed> loadReviewed() {
      var lines =
          new String(resource("reviewed-pos-defaults.tsv"), StandardCharsets.UTF_8)
              .lines()
              .toList();
      if (!lines.getFirst().equals("lemma\ttranslation_sha256\tgloss"))
        throw new IllegalArgumentException("reviewed POS header invalid");
      var result = new HashMap<String, Reviewed>();
      for (var line : lines.subList(1, lines.size())) {
        var f = line.split("\t", -1);
        if (f.length != 3
            || !f[1].matches("[0-9a-f]{64}")
            || !StardictGlossPreparation.safeGloss(f[2])
            || result.putIfAbsent(f[0], new Reviewed(f[1], f[2])) != null)
          throw new IllegalArgumentException("reviewed POS row invalid");
      }
      return Map.copyOf(result);
    }

    private static Map<String, Evidence> load() {
      var bytes = resource("wordnet-lexical-evidence.tsv");
      var expected =
          new String(resource("wordnet-lexical-evidence.sha256"), StandardCharsets.UTF_8).strip();
      if (!sha256(bytes).equals(expected))
        throw new IllegalArgumentException("lexical evidence digest mismatch");
      return parse(new String(bytes, StandardCharsets.UTF_8));
    }
  }

  /**
   * 单个词形的固定 WordNet 证据；历史计数不代表现代总体概率。
   *
   * @param counts 按 n、v、a、r 词性汇总的非负历史义项计数。
   * @param pos 来源中实际出现的词性集合，使用 n、v、a、r 标识。
   * @param synsets 来源义项所属的精确 omw-en 同义词集身份集合，用于比较派生词与原形。
   */
  record Evidence(Map<String, Long> counts, Set<String> pos, Set<String> synsets) {}

  /**
   * 锁定完整来源译文和受核词性默认短义的关系。
   *
   * @param translationSha256 统一换行后的完整来源译文的 UTF-8 字节 SHA-256 小写十六进制摘要。
   * @param gloss 已受核且通过安全校验的中文短义，须与优势词性行的首个候选一致。
   */
  record Reviewed(String translationSha256, String gloss) {}

  /**
   * 离线选择出的受核展示短义及其历史词性计数依据。
   *
   * @param gloss 优势词性对应的安全来源候选短义，已核对受核记录。
   * @param reference 包含固定 WordNet 版本、优势词性及前两位计数的来源引用。
   */
  record Selection(String gloss, String reference) {}
}
