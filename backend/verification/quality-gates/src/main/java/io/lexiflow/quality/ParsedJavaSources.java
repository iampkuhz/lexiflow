package io.lexiflow.quality;

import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.util.DocTrees;
import com.sun.source.util.JavacTask;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaFileObject;
import javax.tools.ToolProvider;

/**
 * 一次 JDK compiler parse 生成的共享抽象语法树。
 *
 * @param sources 已按相对路径排序的解析源码。
 * @param docTrees 文档树与源码位置入口。
 */
public record ParsedJavaSources(List<ParsedSource> sources, DocTrees docTrees) {

  /** 对解析结果做防御性复制。 */
  public ParsedJavaSources {
    sources = List.copyOf(sources);
  }

  /**
   * 解析调用者明确提供的源码，不解析类型也不访问工作树状态。
   *
   * @param root 后端构建根目录。
   * @param candidates 候选源码。
   * @return 共享解析结果。
   * @throws IOException 源码读取失败或存在语法错误。
   */
  public static ParsedJavaSources parse(Path root, List<JavaSourceFile> candidates)
      throws IOException {
    var compiler = ToolProvider.getSystemJavaCompiler();
    if (compiler == null) {
      throw new IOException("JDK compiler is unavailable");
    }
    var diagnostics = new DiagnosticCollector<JavaFileObject>();
    try (var fileManager =
        compiler.getStandardFileManager(diagnostics, null, StandardCharsets.UTF_8)) {
      var paths = candidates.stream().map(JavaSourceFile::path).toList();
      var task =
          (JavacTask)
              compiler.getTask(
                  null,
                  fileManager,
                  diagnostics,
                  List.of("-proc:none", "-Xlint:none"),
                  null,
                  fileManager.getJavaFileObjectsFromPaths(paths));
      var trees = DocTrees.instance(task);
      var byPath =
          candidates.stream()
              .collect(
                  java.util.stream.Collectors.toMap(item -> item.path().normalize(), item -> item));
      var parsed = new ArrayList<ParsedSource>();
      for (var unit : task.parse()) {
        var path = Path.of(unit.getSourceFile().toUri()).normalize();
        var source = byPath.get(path);
        if (source == null) {
          throw new IOException("parsed source is outside explicit inputs: " + path);
        }
        parsed.add(new ParsedSource(source, unit));
      }
      var errors =
          diagnostics.getDiagnostics().stream()
              .filter(item -> item.getKind() == Diagnostic.Kind.ERROR)
              .map(
                  item ->
                      item.getSource() + ":" + item.getLineNumber() + ": " + item.getMessage(null))
              .toList();
      if (!errors.isEmpty()) {
        throw new IOException("Java source parse failed: " + String.join("; ", errors));
      }
      parsed.sort(Comparator.comparing(item -> item.source().relativePath()));
      return new ParsedJavaSources(parsed, trees);
    }
  }

  /**
   * 一份源码与对应的 compiler tree。
   *
   * @param source 原始源码。
   * @param unit compiler 生成的语法树。
   */
  public record ParsedSource(JavaSourceFile source, CompilationUnitTree unit) {}
}
