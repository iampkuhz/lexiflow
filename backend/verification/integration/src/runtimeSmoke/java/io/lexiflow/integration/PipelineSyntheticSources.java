package io.lexiflow.integration;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Creates deterministic, synthetic-only source files and caption requests for the runtime path. */
final class PipelineSyntheticSources {
  private static final String CANONICAL_HEADER =
      "lemma,chinese_gloss,definition,aliases,inflections,frequency_zipf,frequency_source_id,frequency_license_id,frequency_ref,complex_evidence,dictionary_source_id,dictionary_license_id,dictionary_ref\n";
  private static final String STARDICT_HEADER =
      "word,phonetic,definition,translation,pos,collins,oxford,tag,bnc,frq,exchange,detail,audio\n";

  private PipelineSyntheticSources() {}

  static Path canonical(Path dir, String version) throws Exception {
    var rows =
        List.of(
            row("lexiflow", "词库" + version, "", "", "", "7.20", ""),
            row("be", "是", "", "basicform", "was", "7.60", ""),
            row("the", "定冠词", "", "", "", "7.90", ""),
            row("focusword", "重点词", "", "focus", "focuswords", "5.70", "ielts~CC0~focus"),
            row("coldprobe", "冷键验证", "", "", "", "2.00", ""),
            row("tailword", "长尾词", "", "tail", "tailwords", "3.20", ""),
            row("zerofrequency", "零频率词", "", "unknown", "unknownwords", "0", ""),
            row("dependable", "可靠的", "", "reliable", "dependables", "6.10", ""),
            row("the and", "噪声短语", "", "", "", "0", ""),
            row("badgloss", "[bad]；正确释义", "", "", "", "6.10", ""),
            row("abdu", "非洲野狗", "", "", "abdus", "6.10", ""),
            row("abdus", "流浪的", "", "", "", "6.10", ""),
            row("stable phrase", "可靠短语", "", "", "", "6.10", ""));
    var data = new java.util.ArrayList<>(rows);
    if (version.equals("thirdversion")) {
      data.add(row("thirdversion", "第三版坏行", "", "", "", "6.10", ""));
    }
    var file = dir.resolve("canonical-" + version + ".csv");
    Files.writeString(file, CANONICAL_HEADER + String.join("\n", data) + "\n");
    return file;
  }

  static Path stardict(Path dir) throws Exception {
    var file = dir.resolve("synthetic-stardict.csv");
    // No rankings and no Oxford mark: unknown frequency is distinct from low-frequency evidence.
    Files.writeString(
        file,
        STARDICT_HEADER
            + String.join(
                "\n",
                stardictRow("stardictword", "合成词", "synthetic definition"),
                stardictRow("lexiflow", "词库v1", "synthetic shared-surface definition"),
                stardictRow("badstardict", "；后项释义", "bad first candidate"))
            + "\n");
    return file;
  }

  private static String stardictRow(String word, String translation, String definition) {
    return String.join(
        ",", word, "", definition, translation, "n", "0", "0", "", "", "", "", "", "");
  }

  private static String row(
      String lemma,
      String gloss,
      String definition,
      String aliases,
      String inflections,
      String zipf,
      String complex) {
    return String.join(
        ",",
        lemma,
        quote(gloss),
        quote(definition),
        aliases,
        inflections,
        zipf,
        "synthetic-frequency",
        "CC0",
        "freq-" + lemma,
        complex,
        "synthetic-dictionary",
        "CC0",
        "dict-" + lemma);
  }

  private static String quote(String value) {
    return "\"" + value.replace("\"", "\"\"") + "\"";
  }

  static String caption(String key, String text, boolean append) {
    return "{\"captionTopicKey\":\"synthetic-topic\",\"trackKey\":null,"
        + "\"lastRequestedSnapshot\":null,\"currentSnapshot\":{\"captions\":[{"
        + "\"windowId\":null,\"startMs\":null,\"segments\":[{\"key\":\""
        + key
        + "\",\"text\":\""
        + text
        + "\",\"offsetMs\":null,\"append\":"
        + append
        + ",\"line\":0}]}]}}";
  }
}
