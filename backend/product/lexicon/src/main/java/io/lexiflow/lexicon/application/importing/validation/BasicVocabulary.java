package io.lexiflow.lexicon.application.importing.validation;

import java.text.Normalizer;
import java.util.Locale;
import java.util.Set;

/** 导入期固定基础功能词；观看链路不依赖此清单。 */
public final class BasicVocabulary {
  private BasicVocabulary() {}

  private static final Set<String> NON_HINT_WORDS =
      Set.of(
          "a",
          "an",
          "the",
          "this",
          "that",
          "these",
          "those",
          "i",
          "me",
          "my",
          "mine",
          "we",
          "us",
          "our",
          "ours",
          "you",
          "your",
          "yours",
          "he",
          "him",
          "his",
          "she",
          "her",
          "hers",
          "it",
          "its",
          "they",
          "them",
          "their",
          "theirs",
          "be",
          "am",
          "is",
          "are",
          "was",
          "were",
          "been",
          "being",
          "have",
          "has",
          "had",
          "having",
          "do",
          "does",
          "did",
          "doing",
          "can",
          "could",
          "may",
          "might",
          "must",
          "shall",
          "should",
          "will",
          "would",
          "and",
          "or",
          "but",
          "nor",
          "so",
          "yet",
          "if",
          "as",
          "than",
          "then",
          "when",
          "while",
          "because",
          "although",
          "though",
          "unless",
          "until",
          "whether",
          "which",
          "who",
          "whom",
          "whose",
          "what",
          "not",
          "no",
          "yes",
          "to",
          "of",
          "in",
          "on",
          "at",
          "by",
          "for",
          "from",
          "with",
          "without",
          "into",
          "onto",
          "upon",
          "over",
          "under",
          "between",
          "among",
          "up",
          "down",
          "out",
          "about",
          "through",
          "during",
          "before",
          "after",
          "since",
          "around",
          "beside",
          "very",
          "really",
          "just",
          "also",
          "here",
          "there",
          "now",
          "well",
          "still",
          "too",
          "only",
          "even",
          "again",
          "once",
          "always",
          "never",
          "often",
          "sometimes",
          "today",
          "tomorrow",
          "yesterday");

  /**
   * 返回导入策略包含的基础表面形式，不从观看行为推断。
   *
   * @return 不可变的固定功能词集合。
   */
  public static Set<String> fixedWords() {
    return NON_HINT_WORDS;
  }

  /**
   * 仅在预处理时判断独立词；不抑制包含它的完整短语。
   *
   * @param lemma 含义：待归一的词条表面。取值范围：非空字符串。
   * @return 表面是否命中固定功能词。
   */
  public static boolean contains(String lemma) {
    return NON_HINT_WORDS.contains(
        Normalizer.normalize(lemma, Normalizer.Form.NFC).trim().toLowerCase(Locale.ROOT));
  }
}
