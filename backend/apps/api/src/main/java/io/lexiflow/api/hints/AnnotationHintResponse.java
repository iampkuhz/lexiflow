package io.lexiflow.api.hints;

/**
 * API 返回的一条版本化词段中文提示。
 *
 * @param startOffset 词段区间起点。
 * @param endOffset 词段区间终点。
 * @param lexiconEntryId 候选词条标识。
 * @param lexiconVersion 候选词条版本。
 * @param chineseGloss 简短中文释义。
 */
public record AnnotationHintResponse(
    int startOffset,
    int endOffset,
    String lexiconEntryId,
    long lexiconVersion,
    String chineseGloss) {}
