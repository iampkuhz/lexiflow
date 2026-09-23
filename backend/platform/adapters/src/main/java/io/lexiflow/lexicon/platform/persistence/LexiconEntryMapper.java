package io.lexiflow.lexicon.platform.persistence;

import io.lexiflow.lexicon.domain.LexiconAlias;
import io.lexiflow.lexicon.domain.LexiconEntry;
import io.lexiflow.lexicon.domain.LexiconEntryKind;
import io.lexiflow.lexicon.domain.LexiconInflection;
import io.lexiflow.lexicon.domain.LexiconPriority;
import io.lexiflow.lexicon.domain.LexiconProvenance;
import io.lexiflow.lexicon.domain.LexiconSense;
import java.util.List;
import java.util.Locale;

/** 将 persistence DO 转换为不泄漏存储细节的词库领域模型。 */
final class LexiconEntryMapper {
  LexiconEntry toModel(LexiconEntryDO source) {
    return new LexiconEntry(
        source.entryId(),
        source.lexiconVersion(),
        source.languageTag(),
        LexiconEntryKind.valueOf(source.entryKind().toUpperCase(Locale.ROOT)),
        source.lemma(),
        source.senses().stream()
            .map(
                value ->
                    new LexiconSense(
                        value.senseId(),
                        value.chineseGloss(),
                        value.definition(),
                        value.provenanceReference()))
            .toList(),
        source.aliases().stream().map(LexiconAlias::new).toList(),
        source.inflections().stream().map(LexiconInflection::new).toList(),
        new LexiconProvenance(
            source.provenanceSourceId(),
            source.provenanceLicenseId(),
            source.provenanceDigest(),
            source.acquiredAt()),
        new LexiconPriority(
            source.frequencyZipf(), source.complexListCount(), source.memoryPriority()),
        io.lexiflow.lexicon.domain.LexiconHintEligibility.valueOf(source.hintEligibility()));
  }

  List<LexiconEntry> toModels(List<LexiconEntryDO> sources) {
    return sources.stream().map(this::toModel).toList();
  }
}
