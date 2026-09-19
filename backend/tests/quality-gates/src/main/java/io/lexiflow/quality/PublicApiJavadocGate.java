package io.lexiflow.quality;

import com.sun.source.doctree.DocCommentTree;
import com.sun.source.doctree.DocTree;
import com.sun.source.doctree.ParamTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import javax.lang.model.element.Modifier;

/**
 * 检查产品公开 API 的多行 Javadoc、职责摘要与参数含义和取值范围说明。
 *
 * <p>Checkstyle 负责公开方法缺少 Javadoc 的基础风格规则；本门禁只检查已有 Javadoc 的结构化内容，避免以正则扫描源码或用 Python 重复 Java 规则。
 */
public final class PublicApiJavadocGate implements JavaSourceGate {

  private static final String ID = "public-api-javadocs";
  private static final Pattern MEANING = Pattern.compile("含义\\s*[：:]\\s*\\S", Pattern.DOTALL);
  private static final Pattern RANGE = Pattern.compile("取值范围\\s*[：:]\\s*\\S", Pattern.DOTALL);

  /** 返回公开 API Javadoc 规则标识。 */
  @Override
  public String id() {
    return ID;
  }

  /** 检查产品主源码中可从外部访问的类或接口所声明的公开成员。 */
  @Override
  public List<QualityViolation> evaluate(JavaSourceContext context) {
    var violations = new ArrayList<QualityViolation>();
    for (var source : context.parsedSources().sources()) {
      if (isProductMainSource(source.source().relativePath())) {
        new Scanner(context.parsedSources(), source, violations).scan(source.unit(), null);
      }
    }
    return violations.stream().sorted().toList();
  }

  private static boolean isProductMainSource(String relativePath) {
    return relativePath.contains("/src/main/java/") && !relativePath.startsWith("tests/");
  }

  /** 使用 compiler AST 定位真实声明，避免字符串、注释或生成访问器造成误报。 */
  private static final class Scanner extends TreePathScanner<Void, Void> {
    private final ParsedJavaSources sources;
    private final ParsedJavaSources.ParsedSource source;
    private final List<QualityViolation> violations;

    private Scanner(
        ParsedJavaSources sources,
        ParsedJavaSources.ParsedSource source,
        List<QualityViolation> violations) {
      this.sources = sources;
      this.source = source;
      this.violations = violations;
    }

    @Override
    public Void visitMethod(MethodTree tree, Void unused) {
      if (isPublicApiMember(tree, getCurrentPath())) {
        inspect(tree, getCurrentPath());
      }
      return super.visitMethod(tree, unused);
    }

    private boolean isPublicApiMember(MethodTree tree, TreePath methodPath) {
      if (tree.getName().contentEquals("<init>")) {
        return false;
      }
      var owner = enclosingClass(methodPath);
      if (owner == null || !isPubliclyAccessible(owner)) {
        return false;
      }
      if (owner.getKind() == Tree.Kind.INTERFACE || owner.getKind() == Tree.Kind.ANNOTATION_TYPE) {
        return !tree.getModifiers().getFlags().contains(Modifier.PRIVATE);
      }
      return tree.getModifiers().getFlags().contains(Modifier.PUBLIC);
    }

    private ClassTree enclosingClass(TreePath path) {
      for (var current = path.getParentPath(); current != null; current = current.getParentPath()) {
        if (current.getLeaf() instanceof ClassTree classTree) {
          return classTree;
        }
      }
      return null;
    }

    private boolean isPubliclyAccessible(ClassTree owner) {
      for (var current = getCurrentPath(); current != null; current = current.getParentPath()) {
        if (current.getLeaf() instanceof ClassTree classTree
            && !classTree.getModifiers().getFlags().contains(Modifier.PUBLIC)) {
          return false;
        }
      }
      return owner.getModifiers().getFlags().contains(Modifier.PUBLIC);
    }

    private void inspect(MethodTree tree, TreePath path) {
      var positions = sources.docTrees().getSourcePositions();
      var declarationPosition = positions.getStartPosition(source.unit(), tree);
      var declarationLine = line(declarationPosition);
      var doc = sources.docTrees().getDocCommentTree(path);
      if (doc == null) {
        return;
      }
      if (javadocLineCount(declarationPosition) < 3) {
        violations.add(
            violation(
                declarationLine,
                "PUBLIC_API_JAVADOC_NOT_MULTILINE",
                "公开 API 的 Javadoc 必须至少占三行",
                Map.of("member", memberName(tree))));
      }
      if (doc.getFullBody().isEmpty()) {
        violations.add(
            violation(
                declarationLine,
                "PUBLIC_API_JAVADOC_SUMMARY_MISSING",
                "公开 API 的 Javadoc 必须说明函数整体职责",
                Map.of("member", memberName(tree))));
      }
      var descriptions = parameterDescriptions(doc);
      for (var parameter : tree.getParameters()) {
        var name = parameter.getName().toString();
        var description = descriptions.get(name);
        if (description == null) {
          violations.add(
              violation(
                  declarationLine,
                  "PUBLIC_API_PARAM_MISSING",
                  "公开 API 参数缺少 @param 说明",
                  Map.of("member", memberName(tree), "parameter", name)));
        } else if (!MEANING.matcher(description).find()) {
          violations.add(
              violation(
                  declarationLine,
                  "PUBLIC_API_PARAM_MEANING_MISSING",
                  "公开 API 参数说明必须包含“含义：”及其内容",
                  Map.of("member", memberName(tree), "parameter", name)));
        } else if (!RANGE.matcher(description).find()) {
          violations.add(
              violation(
                  declarationLine,
                  "PUBLIC_API_PARAM_RANGE_MISSING",
                  "公开 API 参数说明必须包含“取值范围：”及其内容",
                  Map.of("member", memberName(tree), "parameter", name)));
        }
      }
    }

    private Map<String, String> parameterDescriptions(DocCommentTree doc) {
      var descriptions = new LinkedHashMap<String, String>();
      for (var tag : doc.getBlockTags()) {
        if (tag instanceof ParamTree parameter && !parameter.isTypeParameter()) {
          descriptions.put(parameter.getName().toString(), render(doc, parameter.getDescription()));
        }
      }
      return descriptions;
    }

    private String render(DocCommentTree doc, List<? extends DocTree> trees) {
      var positions = sources.docTrees().getSourcePositions();
      var value = new StringBuilder();
      for (var tree : trees) {
        var start = positions.getStartPosition(source.unit(), doc, tree);
        var end = positions.getEndPosition(source.unit(), doc, tree);
        if (start >= 0 && end >= start) {
          value.append(source.source().text(), (int) start, (int) end).append(' ');
        }
      }
      return value.toString().trim();
    }

    private String memberName(MethodTree tree) {
      return tree.getName().contentEquals("<init>")
          ? enclosingClass(getCurrentPath()).getSimpleName().toString()
          : tree.getName().toString();
    }

    private int javadocLineCount(long declarationPosition) {
      var text = source.source().text();
      var declarationOffset = (int) declarationPosition;
      var start = text.lastIndexOf("/**", declarationOffset);
      var end = start < 0 ? -1 : text.indexOf("*/", start);
      if (end < start) {
        return 0;
      }
      var lines = 1;
      for (var offset = start; offset < end + 2; offset++) {
        if (text.charAt(offset) == '\n') {
          lines++;
        }
      }
      return lines;
    }

    private int line(long position) {
      return (int) source.unit().getLineMap().getLineNumber(position);
    }

    private QualityViolation violation(
        int line, String code, String message, Map<String, String> attributes) {
      return new QualityViolation(
          ID, source.source().relativePath(), line, code, message, attributes);
    }
  }
}
