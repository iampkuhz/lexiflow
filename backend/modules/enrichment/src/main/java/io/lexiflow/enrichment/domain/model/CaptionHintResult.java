package io.lexiflow.enrichment.domain.model;

import java.util.List;
import java.util.Objects;

/**
 * 英文字幕的快速提示结果，不承诺任何后台模型工作。
 *
 * @param caption 保持原样的英文字幕。
 * @param state 前台工作声明状态。
 * @param hints 可显示的版本化词段提示。
 */
public record CaptionHintResult(String caption, HintState state, List<AnnotationHint> hints) {

  /** 冻结结果集合，并校验状态和提示是否一致。 */
  public CaptionHintResult {
    Objects.requireNonNull(caption, "caption");
    Objects.requireNonNull(state, "state");
    hints = List.copyOf(hints);
    if (state == HintState.READY && hints.isEmpty()) {
      throw new IllegalArgumentException("ready result requires at least one hint");
    }
    if (state == HintState.NO_PENDING && !hints.isEmpty()) {
      throw new IllegalArgumentException("no-pending result must not contain hints");
    }
  }
}
