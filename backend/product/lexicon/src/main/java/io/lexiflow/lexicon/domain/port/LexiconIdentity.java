package io.lexiflow.lexicon.domain.port;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.Locale;
import java.util.Objects;
import java.util.UUID;

/** 词库词条与义项的稳定身份派生规则。 */
public final class LexiconIdentity {
  private LexiconIdentity() {}

  /**
   * 从规范英文 lemma 派生正 long 词条身份。
   *
   * @param lemma 含义：英文词条 lemma。取值范围：非空白英文表面；按 NFC、小写、空格归一化。
   * @return SHA-256 前八字节大端非负值；零映射为 1。
   */
  public static long entryId(String lemma) {
    var normalized = normalize(lemma);
    try {
      var digest =
          MessageDigest.getInstance("SHA-256")
              .digest(("entry:en:" + normalized).getBytes(StandardCharsets.UTF_8));
      long value = ByteBuffer.wrap(digest, 0, Long.BYTES).getLong() & Long.MAX_VALUE;
      return value == 0 ? 1 : value;
    } catch (NoSuchAlgorithmException impossible) {
      throw new IllegalStateException("SHA-256 is unavailable", impossible);
    }
  }

  /**
   * 从发布版本和规范英文 lemma 派生义项 UUID。
   *
   * @param version 含义：义项所属发布版本。取值范围：正 long。
   * @param lemma 含义：英文词条 lemma。取值范围：非空白英文表面；按 NFC、小写、空格归一化。
   * @return 确定性名称 UUID。
   */
  public static UUID senseId(long version, String lemma) {
    if (version < 1) throw new IllegalArgumentException("version must be positive");
    return UUID.nameUUIDFromBytes(
        ("sense:" + version + ":" + normalize(lemma)).getBytes(StandardCharsets.UTF_8));
  }

  private static String normalize(String lemma) {
    Objects.requireNonNull(lemma, "lemma");
    var normalized =
        Normalizer.normalize(lemma, Normalizer.Form.NFC).trim().replaceAll("\\s+", " ");
    if (normalized.isBlank()) throw new IllegalArgumentException("lemma must not be blank");
    return normalized.toLowerCase(Locale.ROOT);
  }
}
