package io.lexiflow.api.captiondebug;

/**
 * 经浏览器确认的字幕调试单行事件。
 *
 * @param eventId 由客户端为单条确认事件生成的规范 UUID 字符串。
 * @param event 固定事件值：video-start、incremental、final 或 interrupted。
 * @param topicKey 不透明字幕主题身份，非空且最多 128 个 UTF-16 单元。
 * @param videoId YouTube 十一字符视频身份。
 * @param subtitleKey 字幕片段身份，最多 128 个 UTF-16 单元。
 * @param positionMs 非负视频毫秒位置。
 * @param text 扩展确认已显示的实际正文，最多 16384 个 UTF-16 单元。
 */
public record CaptionDebugRequest(
    String eventId,
    String event,
    String topicKey,
    String videoId,
    String subtitleKey,
    Long positionMs,
    String text) {}
