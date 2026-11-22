package io.lexiflow.lexicon.platform.importer;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Consumer;

/** 补齐固定来源预处理丢弃的独立词义；原准备输入的全部行和字段原样保留。 */
public final class StardictSourcePreparationMain {
  private static final List<String> COLUMNS =
      List.of("word", "translation", "oxford", "tag", "bnc", "frq", "exchange");
  private static final String ECDICT_SHA256 =
      "1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf";

  private StardictSourcePreparationMain() {}

  /**
   * 合并独立词条；只创建新输出，绝不原地改动输入。
   *
   * @param args 含义：依次指定已准备 CSV、固定完整 ECDICT CSV 和新输出 CSV 的路径；取值范围：恰好三个有效路径，输入须符合来源格式及固定 ECDICT
   *     摘要，输出须不存在且不得与任一输入路径相同。
   * @throws IOException 来源、格式或新输出创建失败。
   */
  public static void main(String[] args) throws IOException {
    if (args.length != 3)
      throw new IllegalArgumentException("expected prepared, full ECDICT, new output");
    var base = Path.of(args[1]);
    if (!StardictLexicalEvidence.sha256(Files.readAllBytes(base)).equals(ECDICT_SHA256))
      throw new IllegalArgumentException("fixed ECDICT supplement digest mismatch");
    prepare(Path.of(args[0]), base, Path.of(args[2]));
  }

  static void prepare(Path prepared, Path full, Path output) throws IOException {
    if (Files.exists(output)
        || output.toAbsolutePath().equals(prepared.toAbsolutePath())
        || output.toAbsolutePath().equals(full.toAbsolutePath()))
      throw new IllegalArgumentException("source preparation requires a fresh output");
    var existing = new java.util.HashSet<String>();
    var original = new java.util.ArrayList<Map<String, String>>();
    read(
        prepared,
        row -> {
          original.add(row);
          existing.add(normalize(row.get("word")));
        });
    var additions = new TreeMap<String, Map<String, String>>();
    read(
        full,
        row -> {
          String word = normalize(row.get("word"));
          boolean derived =
              java.util.Arrays.stream(row.get("exchange").split("/"))
                  .anyMatch(part -> part.startsWith("0:"));
          var reviewed =
              ReviewedSourceDefaults.select(word, row.get("translation"), row.get("exchange"));
          if (!existing.contains(word)
              && ((word.contains(" ") && reviewed != null && reviewed.origin().equals("ecdict"))
                  || (derived
                      && word.matches("[a-z]+(?:['-][a-z]+)*")
                      && ((reviewed != null && reviewed.independent())
                          || StardictLexicalEvidence.independent(
                              word, row.get("translation"), row.get("exchange"))))))
            additions.putIfAbsent(word, row);
        });
    try (var out =
        Files.newBufferedWriter(output, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW)) {
      out.write(String.join(",", COLUMNS));
      out.newLine();
      for (var row : original) write(out, row);
      for (var row : additions.values()) write(out, row);
    }
    System.out.println(
        "PASS preserved=" + original.size() + " independent_added=" + additions.size());
  }

  private static void write(java.io.BufferedWriter output, Map<String, String> row)
      throws IOException {
    output.write(
        COLUMNS.stream()
            .map(column -> "\"" + row.get(column).replace("\"", "\"\"") + "\"")
            .collect(java.util.stream.Collectors.joining(",")));
    output.newLine();
  }

  private static String normalize(String text) {
    return text.strip().toLowerCase(Locale.ROOT);
  }

  private static void read(Path input, Consumer<Map<String, String>> consumer) throws IOException {
    try (var csv =
        new StardictCsvReader.CsvRecordReader(
            Files.newBufferedReader(input, StandardCharsets.UTF_8))) {
      var header = csv.next();
      if (header == null
          || !(header.equals(COLUMNS)
              || header.equals(
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
                      "audio"))))
        throw new IllegalArgumentException("source preparation CSV header invalid");
      List<String> values;
      while ((values = csv.next()) != null) {
        if (values.size() != header.size())
          throw new IllegalArgumentException("CSV row width mismatch");
        var row = new HashMap<String, String>();
        for (var column : COLUMNS) row.put(column, values.get(header.indexOf(column)));
        consumer.accept(Map.copyOf(row));
      }
    }
  }
}
