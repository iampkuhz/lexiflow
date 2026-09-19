package io.lexiflow.lexicon.application;

import io.lexiflow.lexicon.domain.LexiconEntry;
import java.util.Collection;
import java.util.List;

/** 词库聚合的唯一持久化合同；调用者不接触表、DAO、DO 或 PostgreSQL 类型。 */
public interface LexiconRepository {
  /**
   * 返回当前已发布词库版本；没有已发布批次时返回 0。
   *
   * @return 供查询使用的已发布版本
   */
  long publishedVersion();

  /**
   * 按指定版本和已规范化表面查询完整词条聚合。
   *
   * @param version 含义：已发布词库版本。取值范围：由方法调用前置条件限定。
   * @param forms 含义：已规范化、待查的表面集合。取值范围：由方法调用前置条件限定。
   * @return 匹配表面的完整词条聚合
   */
  List<LexiconEntry> findByForms(long version, Collection<String> forms);

  /**
   * 返回指定版本内可预热的完整词条聚合。
   *
   * @param version 含义：已发布词库版本。取值范围：由方法调用前置条件限定。
   * @param limit 含义：返回候选的上限。取值范围：由方法调用前置条件限定。
   * @return 按预热优先级排序的完整词条聚合
   */
  List<LexiconEntry> findPrewarmCandidates(long version, int limit);

  /**
   * 原子地持久化并发布一个规范词库版本。
   *
   * @param request 含义：完整且已验证的导入输入。取值范围：由方法调用前置条件限定。
   * @return 新发布的词库版本
   */
  long publish(LexiconImportRequest request);

  /**
   * 创建或取得可恢复的 StarDict staged 批次。
   *
   * @param metadata 含义：来源的可审计元数据。取值范围：由方法调用前置条件限定。
   * @return 当前来源对应的 staged 批次
   */
  StagedLexiconImport openOrResume(LexiconImportMetadata metadata);

  /**
   * 写入 staged 批次的一个连续、已验证分块。
   *
   * @param batch 含义：要续接的 staged 批次。取值范围：由方法调用前置条件限定。
   * @param rows 含义：本次已验证的来源行。取值范围：由方法调用前置条件限定。
   * @param processedThrough 含义：本次写入后的来源行边界。取值范围：由方法调用前置条件限定。
   * @param metadata 含义：来源的可审计元数据。取值范围：由方法调用前置条件限定。
   */
  void stage(
      StagedLexiconImport batch,
      List<LexiconImportRow> rows,
      long processedThrough,
      LexiconImportMetadata metadata);

  /**
   * 在完整来源已写入后原子切换 staged 批次。
   *
   * @param batch 含义：要发布的 staged 批次。取值范围：由方法调用前置条件限定。
   * @param sourceRowsTotal 含义：已扫描的全部来源行数。取值范围：由方法调用前置条件限定。
   */
  void publish(StagedLexiconImport batch, long sourceRowsTotal);
}
