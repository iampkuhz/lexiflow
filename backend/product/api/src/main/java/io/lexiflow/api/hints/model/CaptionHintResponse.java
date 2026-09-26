package io.lexiflow.api.hints.model;

import java.util.List;

/**
 * API 返回的英文字幕快速结果及其词段级中文提示。
 *
 * @param caption 保持原样的英文字幕。
 * @param state 快速结果状态。
 * @param hints 词段级中文提示。
 */
public record CaptionHintResponse(
    String caption, String state, List<AnnotationHintResponse> hints) {}
