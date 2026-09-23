package io.lexiflow.lexicon.platform.persistence;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * persistence 内部使用的词条数据库对象；禁止越过本包边界。
 *
 * @param entryId 含义：词条主键。取值范围：由方法调用前置条件限定。
 * @param lexiconVersion 含义：词库版本。取值范围：由方法调用前置条件限定。
 * @param languageTag 含义：语言标签。取值范围：由方法调用前置条件限定。
 * @param entryKind 含义：词条种类。取值范围：由方法调用前置条件限定。
 * @param lemma 含义：词条原形。取值范围：由方法调用前置条件限定。
 * @param provenanceSourceId 含义：来源标识。取值范围：由方法调用前置条件限定。
 * @param provenanceLicenseId 含义：许可证标识。取值范围：由方法调用前置条件限定。
 * @param provenanceDigest 含义：来源内容摘要。取值范围：由方法调用前置条件限定。
 * @param acquiredAt 含义：来源获取时刻。取值范围：由方法调用前置条件限定。
 * @param frequencyZipf 含义：Zipf 词频。取值范围：由方法调用前置条件限定。
 * @param complexListCount 含义：出现于复杂词表的次数。取值范围：由方法调用前置条件限定。
 * @param memoryPriority 含义：记忆优先级。取值范围：由方法调用前置条件限定。
 * @param senses 含义：联表读取的全部义项。取值范围：由方法调用前置条件限定。
 * @param aliases 含义：别名规范表面。取值范围：由方法调用前置条件限定。
 * @param inflections 含义：屈折规范表面。取值范围：由方法调用前置条件限定。
 * @param hintEligibility 已发布的预处理资格。
 */
record LexiconEntryDO(
    UUID entryId,
    long lexiconVersion,
    String languageTag,
    String entryKind,
    String lemma,
    String provenanceSourceId,
    String provenanceLicenseId,
    String provenanceDigest,
    Instant acquiredAt,
    double frequencyZipf,
    int complexListCount,
    int memoryPriority,
    List<LexiconSenseDO> senses,
    List<String> aliases,
    List<String> inflections,
    String hintEligibility) {}
