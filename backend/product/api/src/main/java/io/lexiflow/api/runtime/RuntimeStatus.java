package io.lexiflow.api.runtime;

/**
 * 面向发布就绪查询的固定安全响应。
 *
 * @param softwareVersion 含义：构建时嵌入的软件版本。取值范围：有效的三段式版本。
 * @param apiContract 含义：HTTP API 合同标识。取值范围：固定非空标识。
 * @param mode 含义：运行模式。取值范围：formal、demo 或 invalid。
 * @param ready 含义：正式资料是否可用于提示。取值范围：true 或 false。
 * @param reason 含义：本次运行探测的固定原因。取值范围：LexiconRuntime.Reason 名称。
 * @param datasetVersion 含义：已发布资料版本。取值范围：非负版本或未知时 null。
 */
public record RuntimeStatus(
    String softwareVersion,
    String apiContract,
    String mode,
    boolean ready,
    String reason,
    Long datasetVersion) {}
