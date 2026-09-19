package io.lexiflow.lexicon.platform.persistence;

import java.util.UUID;

/**
 * persistence 内部使用的导入批次数据库对象。
 *
 * @param batchId 含义：批次主键。取值范围：由方法调用前置条件限定。
 * @param version 含义：staged 词库版本。取值范围：由方法调用前置条件限定。
 * @param sourceRowsProcessed 含义：已写入来源行边界。取值范围：由方法调用前置条件限定。
 */
record LexiconImportBatchDO(UUID batchId, long version, long sourceRowsProcessed) {}
