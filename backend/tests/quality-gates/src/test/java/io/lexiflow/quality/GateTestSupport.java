package io.lexiflow.quality;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

final class GateTestSupport {

  private GateTestSupport() {}

  static JavaSourceContext context(Path root, String fixture, String fileName) throws IOException {
    String content;
    try (var resource = GateTestSupport.class.getResourceAsStream("/fixtures/" + fixture)) {
      if (resource == null) {
        throw new IOException("missing fixture: " + fixture);
      }
      content = new String(resource.readAllBytes(), StandardCharsets.UTF_8);
    }
    var path = root.resolve("sample/src/main/java/example").resolve(fileName);
    Files.createDirectories(path.getParent());
    Files.writeString(path, content, StandardCharsets.UTF_8);
    var source =
        new JavaSourceFile(
            path.toAbsolutePath().normalize(),
            JavaSourceDiscovery.normalize(root.relativize(path)),
            content);
    return new JavaSourceContext(List.of(source), ParsedJavaSources.parse(root, List.of(source)));
  }
}
