package io.lexiflow.enrichment.application.preferences;

import io.lexiflow.lexicon.domain.port.LexiconIdentity;
import java.text.Normalizer;
import java.util.List;
import java.util.Locale;

/** 管理用户明确选择的本机偏好，不包含观看历史或字幕状态。 通过存储端口读写阈值及用户显式隐藏的稳定词条身份。 */
public final class LocalPreferences {
  /** 新初始化的本机偏好存储所使用的常用词默认阈值。 其值必须与受支持的常用词档位一致。 */
  public static final int DEFAULT_CUTOFF = 1500;

  /**
   * 一项用户明确隐藏的稳定词条及其展示信息。
   *
   * @param entryId 含义：词条稳定身份。取值范围：与规范化 lemma 匹配的正十进制 long 字符串。
   * @param lemma 含义：规范化词元。取值范围：非空、去除首尾空白且长度不超过 128 个字符。
   * @param gloss 含义：用于管理页展示的释义。取值范围：非空、去除首尾空白且长度不超过 500 个字符。
   */
  public record Hidden(String entryId, String lemma, String gloss) {}

  /**
   * 当前阈值与隐藏词条的不可变快照。
   *
   * @param commonWordCutoff 含义：纳入常用词的最高排名。取值范围：1000、1500 或 2000。
   * @param hidden 含义：本机显式隐藏的词条。取值范围：非空 List，元素为稳定词条身份。
   */
  public record State(int commonWordCutoff, List<Hidden> hidden) {
    /**
     * 复制列表以保证偏好快照不可变。
     *
     * @param hidden 含义：待保存的隐藏身份快照。取值范围：非空 List，元素不可为 null。
     */
    public State {
      hidden = List.copyOf(hidden);
    }
  }

  /** 只保存本机显式选择的持久化端口。 实现负责将单次写入提交并返回最新状态，不保存观看或字幕数据。 */
  public interface Store {
    /**
     * 读取当前持久化偏好快照。
     *
     * @return 当前阈值和隐藏身份组成的状态值。
     */
    State read();

    /**
     * 持久化一个受支持的常用词排名阈值。
     *
     * @param cutoff 含义：常用词最高排名。取值范围：1000、1500 或 2000。
     * @return 写入后的完整偏好快照。
     */
    State setCutoff(int cutoff);

    /**
     * 持久化规范化词元对应的稳定身份及展示释义。
     *
     * @param entryId 含义：稳定词条身份。取值范围：正十进制 long 字符串。
     * @param lemma 含义：规范化词元。取值范围：非空且长度不超过 128 个字符。
     * @param gloss 含义：管理页展示释义。取值范围：非空且长度不超过 500 个字符。
     * @return 写入后的完整偏好快照。
     */
    State hide(String entryId, String lemma, String gloss);

    /**
     * 移除指定的隐藏身份；不存在时保持原状态。
     *
     * @param entryId 含义：需恢复的稳定身份。取值范围：正十进制 long 字符串。
     * @return 删除后的完整偏好快照。
     */
    State restore(String entryId);
  }

  private final Store store;

  /**
   * 使用持久化端口创建本机偏好应用服务。
   *
   * @param store 含义：偏好状态的持久化端口。取值范围：非空且仅保存显式本机偏好的实现。
   */
  public LocalPreferences(Store store) {
    this.store = store;
  }

  /**
   * 读取用户当前明确选择的本机偏好。
   *
   * @return 已保存的完整偏好快照。
   */
  public State read() {
    return store.read();
  }

  /**
   * 设置不提示的常用词最高排名。
   *
   * @param value 含义：常用词排名阈值。取值范围：1000、1500 或 2000。
   * @return 写入后的完整偏好快照。
   */
  public State setCutoff(int value) {
    if (value != 1000 && value != 1500 && value != 2000)
      throw new IllegalArgumentException("commonWordCutoff must be 1000, 1500, or 2000");
    return store.setCutoff(value);
  }

  /**
   * 仅在十进制身份与规范化词元匹配时保存隐藏偏好。
   *
   * @param id 含义：词条稳定身份。取值范围：规范化 lemma 的正十进制 long 字符串。
   * @param lemma 含义：待隐藏的词元。取值范围：非空、无首尾空白且长度不超过 128 个字符。
   * @param gloss 含义：管理页显示的释义。取值范围：非空、无首尾空白且长度不超过 500 个字符。
   * @return 写入后的完整偏好快照。
   */
  public State hide(String id, String lemma, String gloss) {
    if (!validEntryId(id))
      throw new IllegalArgumentException("entryId must be a canonical positive decimal string");
    if (lemma == null || lemma.isBlank() || lemma.length() > 128 || !lemma.equals(lemma.strip()))
      throw new IllegalArgumentException("lemma is invalid");
    if (gloss == null || gloss.isBlank() || gloss.length() > 500 || !gloss.equals(gloss.strip()))
      throw new IllegalArgumentException("gloss is invalid");
    var normalized =
        Normalizer.normalize(lemma, Normalizer.Form.NFC)
            .trim()
            .replaceAll("\\s+", " ")
            .toLowerCase(Locale.ROOT);
    if (!Long.toString(LexiconIdentity.entryId(normalized)).equals(id))
      throw new IllegalArgumentException("entryId does not match canonical lemma");
    return store.hide(id, normalized, gloss);
  }

  /**
   * 恢复指定稳定身份对应的词条显示。
   *
   * @param id 含义：需恢复的词条稳定身份。取值范围：正十进制 long 字符串。
   * @return 删除该隐藏项后的完整偏好快照；目标不存在时返回未改变的状态。
   */
  public State restore(String id) {
    if (!validEntryId(id))
      throw new IllegalArgumentException("entryId must be a canonical positive decimal string");
    return store.restore(id);
  }

  private static boolean validEntryId(String id) {
    if (id == null || !id.matches("[1-9][0-9]{0,18}")) return false;
    try {
      return Long.toString(Long.parseLong(id)).equals(id);
    } catch (NumberFormatException invalid) {
      return false;
    }
  }
}
