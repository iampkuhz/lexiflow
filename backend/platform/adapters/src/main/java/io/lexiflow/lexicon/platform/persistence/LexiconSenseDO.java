package io.lexiflow.lexicon.platform.persistence;

import java.util.UUID;

/**
 * persistence 内部使用的义项数据库对象。
 *
 * @param senseId 含义：义项主键。取值范围：由方法调用前置条件限定。
 * @param chineseGloss 含义：中文释义。取值范围：由方法调用前置条件限定。
 * @param definition 含义：来源定义。取值范围：由方法调用前置条件限定。
 * @param provenanceReference 含义：来源记录定位。取值范围：由方法调用前置条件限定。
 */
record LexiconSenseDO(
    UUID senseId, String chineseGloss, String definition, String provenanceReference) {}
