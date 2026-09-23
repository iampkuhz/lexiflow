package io.lexiflow.lexicon.platform.persistence;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** 验证 Repository 边界前会完整组装 DO 中的全部子对象。 */
class LexiconEntryMapperTest {
  @Test
  void mapsEverySenseAliasAndInflectionWithoutDeduplicationLoss() {
    var firstSense = UUID.fromString("11111111-1111-1111-1111-111111111111");
    var secondSense = UUID.fromString("22222222-2222-2222-2222-222222222222");
    var source =
        new LexiconEntryDO(
            UUID.fromString("33333333-3333-3333-3333-333333333333"),
            4,
            "en",
            "word",
            "reliable",
            "fixture",
            "MIT",
            "a".repeat(64),
            Instant.EPOCH,
            4.2,
            2,
            900,
            List.of(
                new LexiconSenseDO(firstSense, "可靠的", "worthy of trust", "first"),
                new LexiconSenseDO(secondSense, "稳妥的", "dependable", "second")),
            List.of("trustworthy", "dependable"),
            List.of("reliably", "reliability"),
            "BASIC_VOCABULARY");

    var entry = new LexiconEntryMapper().toModel(source);

    assertEquals(
        List.of(firstSense, secondSense),
        entry.senses().stream().map(value -> value.senseId()).toList());
    assertEquals(
        List.of("trustworthy", "dependable"),
        entry.aliases().stream().map(value -> value.normalizedForm()).toList());
    assertEquals(
        List.of("reliably", "reliability"),
        entry.inflections().stream().map(value -> value.normalizedForm()).toList());
    assertEquals(4, entry.lexiconVersion());
    assertEquals(
        io.lexiflow.lexicon.domain.LexiconHintEligibility.BASIC_VOCABULARY,
        entry.hintEligibility());
  }
}
