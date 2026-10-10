package io.lexiflow.api;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 仓库结构定位与独立发行目录的分析台账边界。 */
class RepositoryRootTest {
  @TempDir Path directory;

  @Test
  void nestedWorkingDirectoryLocatesRepositoryWithoutHarnessManifest() throws Exception {
    Files.writeString(directory.resolve("AGENTS.md"), "instructions");
    Files.createDirectories(directory.resolve("backend/product/api/build"));
    Files.writeString(directory.resolve("backend/settings.gradle.kts"), "settings");
    assertEquals(
        directory,
        ApiApplication.locateRepositoryRoot(directory.resolve("backend/product/api/build")));
  }

  @Test
  void ancestorAgentInstructionsAloneDoNotIdentifyARepository() throws Exception {
    Files.writeString(directory.resolve("AGENTS.md"), "instructions");
    var unrelated = Files.createDirectories(directory.resolve("another-project"));
    assertThrows(IllegalStateException.class, () -> ApiApplication.locateRepositoryRoot(unrelated));
  }

  @Test
  void packagedRuntimeWithoutCheckoutRequiresExplicitAnalysisPath() throws Exception {
    var runtime = Files.createDirectories(directory.resolve("release/api"));
    assertThrows(IllegalStateException.class, () -> ApiApplication.locateRepositoryRoot(runtime));
    // 显式路径不触发仓库定位，发行进程可继续使用本机私有台账。
    assertNotNull(
        new ApiApplication()
            .segmentAnalysisLog(true, runtime.resolve("segments.jsonl").toString()));
    assertNotNull(new ApiApplication().segmentAnalysisLog(false, ""));
  }

  @Test
  void realCheckoutIsLocatedFromBackendProject() {
    var root = Path.of(System.getProperty("lexiflow.repository.root"));
    assertEquals(root, ApiApplication.locateRepositoryRoot(root.resolve("backend/product/api")));
  }
}
