package io.lexiflow.api.contract;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/** 声明不能仅由 JSON 基本类型推断的线协议语义。 注解值由代码维护，供合同检查读取，不参与运行期序列化。 */
@Target(ElementType.TYPE)
@Retention(RetentionPolicy.SOURCE)
public @interface WireSemantics {
  /**
   * 返回该类型的固定线协议语义说明。 每项说明一个仅凭 JSON 字段类型无法表达的约束。
   *
   * @return 该类型的语义约束文本
   */
  String[] value();
}
