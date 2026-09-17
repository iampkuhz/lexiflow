package io.lexiflow.api;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** LexiFlow 同步 API 的唯一组合根。 */
@SpringBootApplication
public class ApiApplication {
  /**
   * 启动 API 进程。
   *
   * @param args 进程参数
   */
  public static void main(String[] args) {
    SpringApplication.run(ApiApplication.class, args);
  }
}
