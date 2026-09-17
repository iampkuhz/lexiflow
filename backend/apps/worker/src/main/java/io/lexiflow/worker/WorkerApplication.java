package io.lexiflow.worker;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/** LexiFlow 异步 worker 的唯一组合根。 */
@SpringBootApplication
public class WorkerApplication {
  /**
   * 启动不包含 Web Server 的 worker 进程。
   *
   * @param args 进程参数
   */
  public static void main(String[] args) {
    SpringApplication application = new SpringApplication(WorkerApplication.class);
    application.setWebApplicationType(WebApplicationType.NONE);
    application.run(args);
  }
}
