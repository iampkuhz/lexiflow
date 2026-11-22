package io.lexiflow.lexicon.platform.importer;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import javax.xml.stream.XMLInputFactory;
import javax.xml.stream.XMLStreamConstants;

/** 从固定公开 WordNet XML 生成有界计数及精确词形身份资料；不读取中文、不访问网络。 */
public final class WordnetEvidenceMain {
  private static final String INPUT_SHA256 =
      "3ab4c3e4324a6d1676cf23ea3eafdd8f858f92969bf85108d049db6b7e4539bd";

  private WordnetEvidenceMain() {}

  /**
   * 生成固定版本词性计数投影；目标必须不存在。
   *
   * @param args 含义：依次指定原始 omw-en2.0 XML 和输出 TSV 的路径；取值范围：恰好两个有效路径，输入须匹配固定摘要及 WordNet 身份，输出须不存在。
   * @throws Exception 固定输入、XML 身份或输出合同失败。
   */
  public static void main(String[] args) throws Exception {
    if (args.length != 2) throw new IllegalArgumentException("expected XML and new TSV paths");
    var input = Path.of(args[0]);
    if (!StardictLexicalEvidence.sha256(Files.readAllBytes(input)).equals(INPUT_SHA256))
      throw new IllegalArgumentException("fixed WordNet source digest mismatch");
    var entries = read(input);
    var out = new StringBuilder(StardictLexicalEvidence.HEADER + "\n");
    for (var entry : entries.entrySet()) {
      out.append(entry.getKey());
      for (var pos : java.util.List.of("n", "v", "a", "r"))
        out.append('\t').append(entry.getValue().counts().getOrDefault(pos, 0L));
      out.append('\t')
          .append(String.join("", new TreeSet<>(entry.getValue().pos())))
          .append('\t')
          .append(String.join("|", new TreeSet<>(entry.getValue().synsets())))
          .append('\n');
    }
    var output = Path.of(args[1]);
    Files.writeString(output, out, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
    System.out.println(
        "PASS lemmas="
            + entries.size()
            + " sha256="
            + StardictLexicalEvidence.sha256(out.toString().getBytes(StandardCharsets.UTF_8)));
  }

  static Map<String, StardictLexicalEvidence.Evidence> read(Path path) throws Exception {
    var factory = XMLInputFactory.newFactory();
    factory.setProperty(XMLInputFactory.SUPPORT_DTD, false);
    factory.setProperty("javax.xml.stream.isSupportingExternalEntities", false);
    var counts = new TreeMap<String, Map<String, Long>>();
    var parts = new TreeMap<String, java.util.Set<String>>();
    var seen = new HashMap<String, String>();
    var synsets = new TreeMap<String, java.util.Set<String>>();
    String lemma = null;
    String key = null;
    String pos = null;
    String synset = null;
    long count = 0;
    boolean countSeen = false;
    boolean validHeader = false;
    try (var input = Files.newInputStream(path)) {
      var reader = factory.createXMLStreamReader(input, StandardCharsets.UTF_8.name());
      try {
        while (reader.hasNext()) {
          int event = reader.next();
          if (event == XMLStreamConstants.DTD && reader.getText().contains("<!ENTITY"))
            throw new IllegalArgumentException("WordNet entity declaration rejected");
          if (event == XMLStreamConstants.START_ELEMENT) {
            String name = reader.getLocalName();
            if (name.equals("Lexicon")) {
              validHeader =
                  "omw-en".equals(reader.getAttributeValue(null, "id"))
                      && "2.0".equals(reader.getAttributeValue(null, "version"))
                      && "en".equals(reader.getAttributeValue(null, "language"));
              if (!validHeader) throw new IllegalArgumentException("WordNet version mismatch");
            } else if (name.equals("LexicalEntry")) {
              lemma = reader.getAttributeValue(null, "index");
            } else if (name.equals("Sense")) {
              key =
                  reader.getAttributeValue(
                      "https://globalwordnet.github.io/schemas/dc/", "identifier");
              synset = reader.getAttributeValue(null, "synset");
              if (key == null
                  || lemma == null
                  || !key.startsWith(lemma + "%")
                  || synset == null
                  || !synset.matches("omw-en-[0-9]{8}-[nvars]"))
                throw new IllegalArgumentException("WordNet sense identity mismatch");
              pos =
                  switch (key.charAt(key.indexOf('%') + 1)) {
                    case '1' -> "n";
                    case '2' -> "v";
                    case '3', '5' -> "a";
                    case '4' -> "r";
                    default -> throw new IllegalArgumentException("WordNet POS invalid");
                  };
              String suffix = synset.substring(synset.length() - 1).replace('s', 'a');
              if (!pos.equals(suffix))
                throw new IllegalArgumentException("WordNet key/POS mismatch");
              count = 0;
              countSeen = false;
            } else if (name.equals("Count")) {
              if (key == null || countSeen) throw new IllegalArgumentException("duplicate Count");
              count = Long.parseLong(reader.getElementText());
              if (count < 0) throw new IllegalArgumentException("negative sense count");
              countSeen = true;
            }
          } else if (event == XMLStreamConstants.END_ELEMENT
              && reader.getLocalName().equals("Sense")) {
            var identity = synset + ":" + count;
            String previous = seen.putIfAbsent(key, identity);
            if (previous != null && !previous.equals(identity))
              throw new IllegalArgumentException("conflicting WordNet sense duplicate");
            if (previous == null) {
              String word = lemma.replace('_', ' ');
              counts
                  .computeIfAbsent(word, ignored -> new HashMap<>())
                  .merge(pos, count, Math::addExact);
              parts.computeIfAbsent(word, ignored -> new HashSet<>()).add(pos);
              synsets.computeIfAbsent(word, ignored -> new HashSet<>()).add(synset);
            }
            key = null;
          }
        }
      } finally {
        reader.close();
      }
    }
    if (!validHeader || counts.isEmpty())
      throw new IllegalArgumentException("WordNet inventory missing");
    var result = new TreeMap<String, StardictLexicalEvidence.Evidence>();
    counts.forEach(
        (word, values) ->
            result.put(
                word,
                new StardictLexicalEvidence.Evidence(
                    Map.copyOf(values),
                    java.util.Set.copyOf(parts.get(word)),
                    java.util.Set.copyOf(synsets.get(word)))));
    return result;
  }
}
