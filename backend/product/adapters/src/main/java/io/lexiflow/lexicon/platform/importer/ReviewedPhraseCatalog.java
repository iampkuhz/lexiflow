package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.model.LexiconImportMetadata;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.application.importing.validation.StardictGlossPreparation;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Properties;

/** 有限工程候选术语来源；不接受运行期任意追加，也不从真实字幕构造资料。 */
public final class ReviewedPhraseCatalog {
  private static final String SOURCE_ID = "lexiflow-reviewed-phrases";
  private static final String NCI_LICENSE = "LicenseRef-NCI-Text-Reuse";
  private static final String NASA_LICENSE = "LicenseRef-NASA-Factual-Use";
  private static final String HEADER =
      "lemma\tgloss\tinflections\tfact_urls\tlicense_id\tlicense_url\tcurator\treview_basis\tcontent_review";

  private ReviewedPhraseCatalog() {}

  /**
   * 返回工程候选来源的精确字节摘要；缺失来源时失败关闭。
   *
   * @return 内置候选 TSV 原始字节的 SHA-256 小写十六进制摘要；不代表内容审核或发布资格
   * @throws IllegalStateException 来源资源缺失或读取失败
   */
  public static String digest() {
    return sha256(bytes());
  }

  /**
   * 返回有界来源候选；内容完整性锁不认证审核者身份。
   *
   * @return 按来源顺序排列的不可变候选列表，包含尚未审核的条目，不可直接视为发布子集
   * @throws IllegalArgumentException 内容摘要不匹配，或候选字段、许可、词形不符合约束
   * @throws IllegalStateException 来源资源或完整性锁缺失、不可读，或完整性锁格式无效
   */
  public static List<LexiconImportRow> rows() {
    return parse(new String(bytes(), StandardCharsets.UTF_8));
  }

  /**
   * 正常离线发布只消费真实独立内容核查所绑定的同一子集，不要求用户逐词签字。
   *
   * @return 通过内容及证据一致性校验且标记为 REVIEWED 的不可变条目列表；无已审核条目时为空
   * @throws IllegalArgumentException 候选内容无效，或审核证据、主体摘要、子集及独立审核字段不匹配
   * @throws IllegalStateException 来源、完整性锁或审核资源不可用，或完整性锁格式无效
   */
  public static List<LexiconImportRow> publicationRows() {
    var input = new String(bytes(), StandardCharsets.UTF_8);
    parse(input);
    return reviewedRows(
        input,
        resource("reviewed-phrases-review.properties"),
        resource("reviewed-phrases-review.md"));
  }

  static List<LexiconImportRow> reviewedRows(String input, byte[] review, byte[] report) {
    var candidates = parseCandidates(input);
    var evidence = new Properties();
    try {
      evidence.load(new java.io.StringReader(new String(review, StandardCharsets.UTF_8)));
    } catch (IOException failure) {
      throw new IllegalArgumentException("reviewed phrase review unreadable", failure);
    }
    // 这些字段记录已从官方附件/run 核对的实际来源，不把自填 actor/SHA 当身份认证。
    for (var field : List.of("actorId", "issueId", "runId", "attachmentId"))
      if (!evidence.getProperty(field, "").matches("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"))
        throw new IllegalArgumentException("reviewed phrase review origin missing");
    var originalEvidence = evidence.getProperty("evidenceSha256", "");
    var publicEvidence = evidence.getProperty("publicEvidenceSha256", "");
    if (!"independent-ai-content".equals(evidence.getProperty("reviewType"))
        || !evidence.getProperty("reviewedInputSha256", "").matches("[0-9a-f]{64}")
        || !originalEvidence.matches("[0-9a-f]{64}")
        || originalEvidence.equals(publicEvidence)
        || !sha256(report).equals(publicEvidence))
      throw new IllegalArgumentException("reviewed phrase evidence mismatch");
    var lines = input.lines().skip(1).toList();
    var reviewed = List.of(evidence.getProperty("reviewedLemmas", "").split("\\|", -1));
    var boundLines =
        lines.stream().filter(line -> reviewed.contains(line.split("\t", -1)[0])).toList();
    if (!reviewed.equals(boundLines.stream().map(line -> line.split("\t", -1)[0]).toList()))
      throw new IllegalArgumentException("reviewed phrase subset mismatch");
    var reviewBasis = evidence.getProperty("reviewBasis", "");
    if (!reviewBasis.matches("TASK-[0-9]+:source-facts-review\\.md"))
      throw new IllegalArgumentException("reviewed phrase review basis missing");
    var subjects =
        String.join(
                "\n",
                boundLines.stream()
                    .map(
                        line -> String.join("\t", java.util.Arrays.copyOf(line.split("\t", -1), 7)))
                    .toList())
            + "\n";
    if (!sha256(subjects.getBytes(StandardCharsets.UTF_8))
        .equals(evidence.getProperty("subjectsSha256")))
      throw new IllegalArgumentException("reviewed phrase subject mismatch");
    var published = new ArrayList<LexiconImportRow>();
    for (int i = 0; i < lines.size(); i++) {
      var cells = lines.get(i).split("\t", -1);
      if ("REVIEWED".equals(cells[8])) {
        if (!reviewed.contains(cells[0])
            || !reviewBasis.equals(cells[7])
            || cells[6].equals("Codex:" + evidence.getProperty("actorId")))
          throw new IllegalArgumentException("reviewed phrase independent review missing");
        published.add(candidates.get(i));
      }
    }
    return List.copyOf(published);
  }

  static List<LexiconImportRow> parse(String input) {
    if (!sha256(input.getBytes(StandardCharsets.UTF_8)).equals(lockedDigest()))
      throw new IllegalArgumentException("reviewed phrase content changed without review");
    return parseCandidates(input);
  }

  static List<LexiconImportRow> parseCandidates(String input) {
    var lines = input.lines().toList();
    if (lines.isEmpty() || !HEADER.equals(lines.getFirst()))
      throw new IllegalArgumentException("reviewed phrase header invalid");
    var rows = new ArrayList<LexiconImportRow>();
    var surfaces = new HashSet<String>();
    for (var line : lines.subList(1, lines.size())) {
      var cells = line.split("\t", -1);
      if (cells.length != 9 || java.util.Arrays.stream(cells).anyMatch(String::isBlank))
        throw new IllegalArgumentException("reviewed phrase evidence missing");
      String lemma = cells[0], gloss = cells[1];
      requirePhrase(lemma);
      if (!StardictGlossPreparation.safeGloss(gloss))
        throw new IllegalArgumentException("reviewed phrase gloss unsafe");
      String license = cells[4];
      boolean nci = NCI_LICENSE.equals(license);
      if (!(nci || NASA_LICENSE.equals(license))
          || !(nci
                  ? "https://www.cancer.gov/policies/copyright-reuse"
                  : "https://www.nasa.gov/nasa-brand-center/images-and-media/")
              .equals(cells[5]))
        throw new IllegalArgumentException("reviewed phrase license not approved");
      for (var fact : cells[3].split("\\|", -1)) {
        var uri = URI.create(fact);
        var hosts =
            nci
                ? List.of("www.cancer.gov", "training.seer.cancer.gov")
                : List.of("www1.grc.nasa.gov");
        if (!"https".equals(uri.getScheme())
            || !hosts.contains(uri.getHost())
            || uri.getUserInfo() != null)
          throw new IllegalArgumentException("reviewed phrase fact source not approved");
      }
      if (!cells[6].startsWith("Codex:") || !List.of("PENDING", "REVIEWED").contains(cells[8]))
        throw new IllegalArgumentException("reviewed phrase review basis missing");
      if (!surfaces.add(lemma)) throw new IllegalArgumentException("reviewed phrase duplicated");
      var inflections = List.of(cells[2].split("\\|", -1));
      for (var form : inflections) {
        requirePhrase(form);
        if (!form.equals(lemma + "s"))
          throw new IllegalArgumentException("reviewed phrase inflection outside approved scope");
        if (!surfaces.add(form)) throw new IllegalArgumentException("reviewed form duplicated");
      }
      var reference = new SourceReference(sourceId(license), license, cells[3] + "; " + cells[7]);
      rows.add(
          new LexiconImportRow(
              lemma,
              gloss,
              "",
              List.of(),
              inflections,
              LexiconPriority.fromEvidence(0, 0),
              reference,
              new SourceReference(sourceId(license), license, "unranked; no frequency evidence"),
              List.of(),
              false));
    }
    if (rows.isEmpty()) throw new IllegalArgumentException("reviewed phrase catalog empty");
    return List.copyOf(rows);
  }

  /**
   * 按许可分别声明来源；摘要绑定各子集的原始表头及有序行，不把增补伪称原 CSV。
   *
   * @return 发布子集的不可变来源列表，按 NCI、NASA 顺序仅包含非空许可子集及其摘要
   * @throws IllegalArgumentException 候选或发布审核校验失败
   * @throws IllegalStateException 来源、完整性锁或审核资源不可用，或完整性锁格式无效
   */
  public static List<LexiconImportMetadata.Source> sources() {
    return sources(publicationRows().stream().map(LexiconImportRow::lemma).toList());
  }

  static List<LexiconImportMetadata.Source> sources(List<String> publishedLemmas) {
    rows();
    var content = new String(bytes(), StandardCharsets.UTF_8);
    var result = new ArrayList<LexiconImportMetadata.Source>();
    for (var license : List.of(NCI_LICENSE, NASA_LICENSE)) {
      var selected =
          content
              .lines()
              .skip(1)
              .filter(line -> publishedLemmas.contains(line.split("\t", -1)[0]))
              .filter(line -> line.split("\t", -1)[4].equals(license))
              .toList();
      if (!selected.isEmpty())
        result.add(
            new LexiconImportMetadata.Source(
                sourceId(license),
                license,
                sha256(
                    (HEADER + "\n" + String.join("\n", selected) + "\n")
                        .getBytes(StandardCharsets.UTF_8))));
    }
    return List.copyOf(result);
  }

  private static String sourceId(String license) {
    return SOURCE_ID + (NCI_LICENSE.equals(license) ? "-nci" : "-nasa");
  }

  private static String lockedDigest() {
    try (var in =
        ReviewedPhraseCatalog.class.getResourceAsStream("/lexicon/reviewed-phrases.sha256")) {
      if (in == null) throw new IllegalStateException("reviewed phrase content lock unavailable");
      var digest = new String(in.readAllBytes(), StandardCharsets.UTF_8).trim();
      if (!digest.matches("[0-9a-f]{64}"))
        throw new IllegalStateException("reviewed phrase content lock invalid");
      return digest;
    } catch (IOException failure) {
      throw new IllegalStateException("reviewed phrase content lock unreadable", failure);
    }
  }

  private static String sha256(byte[] input) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(input));
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException(impossible);
    }
  }

  private static void requirePhrase(String form) {
    var tokens = LexiconSurfacePolicy.queryTokens(form);
    if (tokens.size() < 2
        || tokens.size() > LexiconSurfacePolicy.MAX_PHRASE_TOKENS
        || !String.join(" ", tokens).equals(form))
      throw new IllegalArgumentException("reviewed phrase must be a normalized complete phrase");
  }

  private static byte[] bytes() {
    return resource("reviewed-phrases.tsv");
  }

  private static byte[] resource(String name) {
    try (var in = ReviewedPhraseCatalog.class.getResourceAsStream("/lexicon/" + name)) {
      if (in == null) throw new IllegalStateException("reviewed phrase source unavailable");
      return in.readAllBytes();
    } catch (IOException failure) {
      throw new IllegalStateException("reviewed phrase source unreadable", failure);
    }
  }
}
