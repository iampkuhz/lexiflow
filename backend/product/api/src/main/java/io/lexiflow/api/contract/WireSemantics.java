package io.lexiflow.api.contract;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** Code-owned meanings which cannot be inferred from JSON primitive types. */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface WireSemantics {
  String[] value();
}
