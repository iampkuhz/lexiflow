package io.lexiflow.enrichment.domain.model;

/** 前台结果对后台语义工作的声明状态。 */
public enum HintState {
  /** 已有确定性提示，无需异步工作。 */
  READY,
  /** 没有可靠提示，且本切片不会伪造待处理工作。 */
  NO_PENDING
}
