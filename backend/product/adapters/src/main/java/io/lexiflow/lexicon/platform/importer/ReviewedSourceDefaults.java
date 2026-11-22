package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** 离线受核质量覆盖；完整来源及关系锁定，不参与观看时选义。 */
final class ReviewedSourceDefaults {
  private static final String HEADER =
      "lemma\ttranslation_sha256\texchange_sha256\tanchor\tgloss\tindependent\tfact_url\tbasis\torigin";

  private ReviewedSourceDefaults() {}

  static String digest() {
    return StardictLexicalEvidence.sha256(resource("reviewed-source-defaults.tsv"));
  }

  static Map<String, Default> entries() {
    return Holder.ENTRIES;
  }

  static Default select(String word, String translation, String exchange) {
    var value = entries().get(word);
    if (value != null) value.validate(translation, exchange);
    return value;
  }

  static Map<String, Default> parse(String text) {
    var lines = text.lines().toList();
    if (lines.isEmpty() || !HEADER.equals(lines.getFirst()))
      throw new IllegalArgumentException("reviewed default header invalid");
    var result = new HashMap<String, Default>();
    for (var line : lines.subList(1, lines.size())) {
      var f = line.split("\t", -1);
      if (f.length != 9
          || java.util.Arrays.stream(f).anyMatch(String::isBlank)
          || !f[1].matches("[0-9a-f]{64}")
          || !f[2].matches("[0-9a-f]{64}")
          || !StardictGlossPreparation.safeGloss(f[4])
          || !List.of("true", "false").contains(f[5])
          || !f[6].startsWith("https://")
          || !List.of("ecdict", "project-construction", "project-reviewed").contains(f[8]))
        throw new IllegalArgumentException("reviewed default row invalid");
      if (result.putIfAbsent(
              f[0],
              new Default(f[1], f[2], f[3], f[4], Boolean.parseBoolean(f[5]), f[6], f[7], f[8]))
          != null) throw new IllegalArgumentException("duplicate reviewed default");
    }
    return Map.copyOf(result);
  }

  /** 自编通用构式使用同一内容锁和静态发布路径，不冒充 ECDICT 或独立审核签名。 */
  static List<io.lexiflow.lexicon.application.importing.model.LexiconImportRow> constructionRows() {
    return entries().entrySet().stream()
        .filter(e -> e.getValue().origin().equals("project-construction"))
        .sorted(Map.Entry.comparingByKey())
        .map(
            e -> {
              var value = e.getValue();
              value.validate(value.anchor(), "");
              var tokens =
                  io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy.queryTokens(e.getKey());
              if (tokens.size() < 2
                  || tokens.size() > 3
                  || !String.join(" ", tokens).equals(e.getKey()))
                throw new IllegalArgumentException(
                    "construction requires complete normalized phrase");
              var source =
                  new io.lexiflow.lexicon.application.importing.model.SourceReference(
                      "lexiflow-reviewed-source-defaults",
                      "MIT",
                      value.factUrl() + "; " + value.basis());
              return new io.lexiflow.lexicon.application.importing.model.LexiconImportRow(
                  e.getKey(),
                  value.gloss(),
                  "",
                  List.of(),
                  List.of(),
                  io.lexiflow.lexicon.domain.model.LexiconPriority.fromEvidence(0, 0),
                  source,
                  new io.lexiflow.lexicon.application.importing.model.SourceReference(
                      "lexiflow-reviewed-source-defaults",
                      "MIT",
                      "unranked; no frequency evidence"),
                  List.of(),
                  false,
                  false,
                  "reviewed-construction;sha256=" + digest() + ";" + value.factUrl(),
                  value.anchor(),
                  null,
                  null,
                  List.of(),
                  false,
                  false,
                  true);
            })
        .toList();
  }

  private static byte[] resource(String name) {
    try (var in = ReviewedSourceDefaults.class.getResourceAsStream("/lexicon/" + name)) {
      if (in == null) throw new IllegalStateException("reviewed defaults unavailable");
      return in.readAllBytes();
    } catch (java.io.IOException failure) {
      throw new IllegalStateException(failure);
    }
  }

  /** 延迟载入锁定的离线资料。 */
  private static final class Holder {
    static final Map<String, Default> ENTRIES = load();

    private static Map<String, Default> load() {
      if (!digest()
          .equals(
              new String(resource("reviewed-source-defaults.sha256"), StandardCharsets.UTF_8)
                  .strip())) throw new IllegalArgumentException("reviewed default digest mismatch");
      return parse(new String(resource("reviewed-source-defaults.tsv"), StandardCharsets.UTF_8));
    }
  }

  /**
   * 受核展示与完整原来源关系，不改变原文或运行时统计。
   *
   * @param translationSha256 统一换行后的完整来源译文字节摘要，使用 UTF-8 和小写十六进制 SHA-256。
   * @param exchangeSha256 完整来源词形关系的 UTF-8 字节摘要，使用小写十六进制 SHA-256。
   * @param anchor 必须存在于来源译文候选中的锚定义，用于验证展示短义与原来源的关系。
   * @param gloss 通过安全校验的受核中文展示短义，不替换完整来源译文。
   * @param independent 受核资料是否认定存在独立词义，供派生词补充准入使用。
   * @param factUrl 支持受核结论的 HTTPS 事实来源地址。
   * @param basis 受核结论的依据说明，随项目自编构式的来源引用保留。
   * @param origin 短义来源类别，限 ecdict、project-construction 或 project-reviewed。
   */
  record Default(
      String translationSha256,
      String exchangeSha256,
      String anchor,
      String gloss,
      boolean independent,
      String factUrl,
      String basis,
      String origin) {
    void validate(String translation, String exchange) {
      String normalized =
          translation.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n");
      if (!translationSha256.equals(
              StardictLexicalEvidence.sha256(normalized.getBytes(StandardCharsets.UTF_8)))
          || !exchangeSha256.equals(
              StardictLexicalEvidence.sha256(exchange.getBytes(StandardCharsets.UTF_8)))
          || !StardictGlossPreparation.candidates(StardictCsvReader.cleanTranslation(translation))
              .contains(anchor))
        throw new IllegalArgumentException("reviewed default source or relation changed");
    }
  }
}
