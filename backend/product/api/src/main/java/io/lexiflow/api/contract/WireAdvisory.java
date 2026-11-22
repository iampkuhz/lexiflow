package io.lexiflow.api.contract;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** 仅用于已明确容忍额外键的只读响应：字段可缺省，扩展不得依赖其值。 */
@Target(ElementType.RECORD_COMPONENT)
@Retention(RetentionPolicy.SOURCE)
public @interface WireAdvisory {}
