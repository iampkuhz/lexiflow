package io.lexiflow.quality;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;

/** 基于调用者提供根目录发现 Java 源码，不查询 Git 或任何外部进程。 */
public final class JavaSourceDiscovery {

  private static final Set<String> EXCLUDED_DIRECTORIES = Set.of("build", ".gradle");

  private JavaSourceDiscovery() {}

  /**
   * 读取 main 与 test Java source set，排除构建输出和资源 fixture。
   *
   * @param root 后端构建根目录。
   * @return 已按相对路径稳定排序的源码。
   * @throws IOException 遍历或读取失败。
   */
  public static List<JavaSourceFile> discover(Path root) throws IOException {
    var normalizedRoot = root.toAbsolutePath().normalize();
    var sources = new ArrayList<JavaSourceFile>();
    Files.walkFileTree(
        normalizedRoot,
        new SimpleFileVisitor<>() {
          @Override
          public FileVisitResult preVisitDirectory(Path directory, BasicFileAttributes attributes) {
            var name = directory.getFileName();
            var relative = normalize(normalizedRoot.relativize(directory));
            var insideSourceSet =
                relative.contains("/src/main/java/") || relative.contains("/src/test/java/");
            return !insideSourceSet
                    && name != null
                    && EXCLUDED_DIRECTORIES.contains(name.toString())
                ? FileVisitResult.SKIP_SUBTREE
                : FileVisitResult.CONTINUE;
          }

          @Override
          public FileVisitResult visitFile(Path path, BasicFileAttributes attributes)
              throws IOException {
            var normalized = path.toAbsolutePath().normalize();
            var relative = normalize(normalizedRoot.relativize(normalized));
            if (attributes.isRegularFile()
                && relative.endsWith(".java")
                && (relative.contains("/src/main/java/") || relative.contains("/src/test/java/"))) {
              sources.add(
                  new JavaSourceFile(
                      normalized, relative, Files.readString(normalized, StandardCharsets.UTF_8)));
            }
            return FileVisitResult.CONTINUE;
          }
        });
    sources.sort(Comparator.comparing(JavaSourceFile::relativePath));
    return List.copyOf(sources);
  }

  /** 把路径稳定规范化为 POSIX 形式。 */
  public static String normalize(Path path) {
    return path.normalize().toString().replace('\\', '/');
  }
}
