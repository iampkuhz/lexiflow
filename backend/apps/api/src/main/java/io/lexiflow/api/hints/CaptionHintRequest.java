package io.lexiflow.api.hints;

import java.util.UUID;

/**
 * API 接收的已定位英文字幕，不包含浏览器、账号或来源 DOM 字段。
 *
 * @param contentId 内容稳定 UUID。
 * @param contentRevision 内容或字幕修订。
 * @param segmentId 当前规范字幕片段 identity。
 * @param caption 当前英文字幕。
 * @param startOffset 目标区间起点。
 * @param endOffset 目标区间终点。
 */
public record CaptionHintRequest(
    UUID contentId,
    long contentRevision,
    String segmentId,
    String caption,
    int startOffset,
    int endOffset) {}
