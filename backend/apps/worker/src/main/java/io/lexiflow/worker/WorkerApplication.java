package io.lexiflow.worker;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** LexiFlow 异步 worker 的唯一组合根。 */
@SpringBootApplication(
    excludeName = "org.springframework.boot.jdbc.autoconfigure.DataSourceAutoConfiguration")
public class WorkerApplication {
  /**
   * 启动不包含 Web Server 的 worker 进程。
   *
   * @param args 含义：交由 Spring Boot 解析的启动参数。取值范围：非空数组，长度为 0 至任意。
   */
  public static void main(String[] args) {
    SpringApplication application = new SpringApplication(WorkerApplication.class);
    application.setWebApplicationType(WebApplicationType.NONE);
    application.run(args);
  }
}
