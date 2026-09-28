package io.lexiflow.enrichment.application.caption;

import io.lexiflow.lexicon.domain.port.LexiconSurfacePolicy;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;

/** 生成字幕的精确词形查表键；定位坐标仍必须使用原字幕。 */
public final class CandidateForms {
  private CandidateForms() {}

  /**
   * 按顺序枚举每个起点的一至三词连续查表键并去重。
   *
   * @param caption 含义：原始字幕片段。取值范围：非空，可为空白。
   * @return 按首次出现顺序排列的不可变规范查表键。
   */
  public static List<String> fromCaption(String caption) {
    Objects.requireNonNull(caption, "caption");
    var tokens = LexiconSurfacePolicy.queryTokens(caption);
    var forms = new LinkedHashSet<String>();
    for (int start = 0; start < tokens.size(); start++) {
      var phrase = new StringBuilder();
      for (int length = 1;
          length <= LexiconSurfacePolicy.MAX_PHRASE_TOKENS && start + length <= tokens.size();
          length++) {
        if (length > 1) phrase.append(' ');
        phrase.append(tokens.get(start + length - 1));
        forms.add(phrase.toString());
      }
    }
    return List.copyOf(forms);
  }
}
