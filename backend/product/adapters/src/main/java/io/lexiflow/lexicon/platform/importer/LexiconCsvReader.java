package io.lexiflow.lexicon.platform.importer;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.application.importing.model.SourceReference;
import io.lexiflow.lexicon.domain.model.LexiconPriority;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** 读取受控的 `lexiflow-lexicon-v1.csv`，并在连接数据库前完成全量语法校验。 */
final class LexiconCsvReader {
  static final List<String> REQUIRED_HEADERS =
      List.of(
          "lemma",
          "chinese_gloss",
          "definition",
          "aliases",
          "inflections",
          "frequency_zipf",
          "frequency_source_id",
          "frequency_license_id",
          "frequency_ref",
          "complex_evidence",
          "dictionary_source_id",
          "dictionary_license_id",
          "dictionary_ref");

  List<LexiconImportRow> read(Path input) throws IOException {
    Objects.requireNonNull(input, "input");
    var rows = parseCsv(Files.readString(input, StandardCharsets.UTF_8));
    if (rows.isEmpty()) {
      throw new IllegalArgumentException("lexicon input must include a header");
    }
    var header = indexHeaders(rows.getFirst());
    var result = new ArrayList<LexiconImportRow>();
    for (var index = 1; index < rows.size(); index++) {
      var values = rows.get(index);
      if (values.stream().allMatch(String::isBlank)) {
        continue;
      }
      if (values.size() != header.size()) {
        throw new IllegalArgumentException(
            "row " + (index + 1) + " has an unexpected column count");
      }
      try {
        result.add(toRow(values, header));
      } catch (IllegalArgumentException exception) {
        throw new IllegalArgumentException(
            "row " + (index + 1) + " is invalid: " + exception.getMessage(), exception);
      }
    }
    if (result.isEmpty()) {
      throw new IllegalArgumentException("lexicon input must contain at least one entry");
    }
    return List.copyOf(result);
  }

  private static Map<String, Integer> indexHeaders(List<String> header) {
    var indexes = new HashMap<String, Integer>();
    for (var index = 0; index < header.size(); index++) {
      var name = header.get(index).trim();
      if (indexes.put(name, index) != null) {
        throw new IllegalArgumentException("duplicate header: " + name);
      }
    }
    if (!indexes.keySet().equals(java.util.Set.copyOf(REQUIRED_HEADERS))) {
      throw new IllegalArgumentException("headers must exactly match lexiflow-lexicon-v1.csv");
    }
    return indexes;
  }

  private static LexiconImportRow toRow(List<String> values, Map<String, Integer> header) {
    var frequencyZipf = parseZipf(value(values, header, "frequency_zipf"));
    var complexLists = parseComplexEvidence(value(values, header, "complex_evidence"));
    return new LexiconImportRow(
        value(values, header, "lemma"),
        value(values, header, "chinese_gloss"),
        value(values, header, "definition"),
        splitForms(value(values, header, "aliases")),
        splitForms(value(values, header, "inflections")),
        LexiconPriority.fromEvidence(frequencyZipf, complexLists.size()),
        new SourceReference(
            value(values, header, "dictionary_source_id"),
            value(values, header, "dictionary_license_id"),
            value(values, header, "dictionary_ref")),
        new SourceReference(
            value(values, header, "frequency_source_id"),
            value(values, header, "frequency_license_id"),
            value(values, header, "frequency_ref")),
        complexLists,
        true);
  }

  private static String value(List<String> values, Map<String, Integer> header, String name) {
    return values.get(header.get(name)).trim();
  }

  private static double parseZipf(String value) {
    try {
      return Double.parseDouble(value);
    } catch (NumberFormatException exception) {
      throw new IllegalArgumentException("frequency_zipf must be a number", exception);
    }
  }

  private static List<String> splitForms(String value) {
    if (value.isBlank()) {
      return List.of();
    }
    return List.of(value.split("\\|", -1)).stream().map(String::trim).toList();
  }

  private static List<SourceReference> parseComplexEvidence(String value) {
    if (value.isBlank()) {
      return List.of();
    }
    var result = new ArrayList<SourceReference>();
    for (var evidence : value.split(";", -1)) {
      var parts = evidence.split("~", -1);
      if (parts.length != 3) {
        throw new IllegalArgumentException(
            "complex_evidence must use sourceId~licenseId~recordReference");
      }
      result.add(new SourceReference(parts[0].trim(), parts[1].trim(), parts[2].trim()));
    }
    if (new java.util.HashSet<>(result).size() != result.size()) {
      throw new IllegalArgumentException("complex_evidence must not repeat the same source record");
    }
    return List.copyOf(result);
  }

  private static List<List<String>> parseCsv(String content) {
    var rows = new ArrayList<List<String>>();
    var row = new ArrayList<String>();
    var cell = new StringBuilder();
    var quoted = false;
    for (var index = 0; index < content.length(); index++) {
      var character = content.charAt(index);
      if (character == '"') {
        if (!quoted && !cell.isEmpty()) {
          throw new IllegalArgumentException("CSV quote must begin a field");
        }
        if (quoted && index + 1 < content.length() && content.charAt(index + 1) == '"') {
          cell.append(character);
          index += 1;
        } else {
          quoted = !quoted;
        }
      } else if (character == ',' && !quoted) {
        row.add(cell.toString());
        cell.setLength(0);
      } else if ((character == '\n' || character == '\r') && !quoted) {
        if (character == '\r'
            && index + 1 < content.length()
            && content.charAt(index + 1) == '\n') {
          index += 1;
        }
        row.add(cell.toString());
        rows.add(List.copyOf(row));
        row.clear();
        cell.setLength(0);
      } else {
        cell.append(character);
      }
    }
    if (quoted) {
      throw new IllegalArgumentException("CSV contains an unclosed quoted field");
    }
    if (!row.isEmpty() || !cell.isEmpty()) {
      row.add(cell.toString());
      rows.add(List.copyOf(row));
    }
    return List.copyOf(rows);
  }
}
