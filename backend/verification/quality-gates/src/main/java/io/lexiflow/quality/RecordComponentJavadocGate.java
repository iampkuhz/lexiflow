package io.lexiflow.quality;

import com.sun.source.doctree.ParamTree;
import com.sun.source.tree.ClassTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.TreePathScanner;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import javax.lang.model.element.Modifier;

/** 使用 compiler AST 与 DocTrees 检查 record component 的中文参数说明。 */
public final class RecordComponentJavadocGate implements JavaSourceGate {

  private static final String ID = "record-component-javadocs";
  private static final Pattern CHINESE =
      Pattern.compile("[\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff]");

  /** 返回 record Javadoc 规则标识。 */
  @Override
  public String id() {
    return ID;
  }

  /** 检查生产源码中的每个 record component。 */
  @Override
  public List<QualityViolation> evaluate(JavaSourceContext context) {
    var violations = new ArrayList<QualityViolation>();
    for (var source : context.parsedSources().sources()) {
      if (source.source().relativePath().contains("/src/main/java/")) {
        new Scanner(context.parsedSources(), source, violations).scan(source.unit(), null);
      }
    }
    return violations.stream().sorted().toList();
  }

  /** 遍历 record 声明并定位 Javadoc 参数标签。 */
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
    public Void visitClass(ClassTree tree, Void unused) {
      if (tree.getKind() == Tree.Kind.RECORD) {
        inspect(tree);
      }
      return super.visitClass(tree, unused);
    }

    private void inspect(ClassTree tree) {
      var positions = sources.docTrees().getSourcePositions();
      var doc = sources.docTrees().getDocCommentTree(getCurrentPath());
      var recordName = tree.getSimpleName().toString();
      var declarationPosition = positions.getStartPosition(source.unit(), tree);
      var declarationLine = line(declarationPosition);
      if (doc == null) {
        violations.add(
            violation(
                declarationLine,
                "RECORD_JAVADOC_MISSING",
                "record " + recordName + " 缺少类型 Javadoc",
                Map.of("record", recordName)));
        return;
      }
      var descriptions = new LinkedHashMap<String, String>();
      for (var tag : doc.getBlockTags()) {
        if (tag instanceof ParamTree param && !param.isTypeParameter()) {
          var description = new StringBuilder();
          for (var part : param.getDescription()) {
            var start = positions.getStartPosition(source.unit(), doc, part);
            var end = positions.getEndPosition(source.unit(), doc, part);
            if (start >= 0 && end >= start) {
              description.append(source.source().text(), (int) start, (int) end).append(' ');
            }
          }
          descriptions.put(param.getName().toString(), description.toString().trim());
        }
      }
      for (var member : tree.getMembers()) {
        if (!(member instanceof VariableTree component)
            || component.getModifiers().getFlags().contains(Modifier.STATIC)) {
          continue;
        }
        var start = positions.getStartPosition(source.unit(), component);
        if (start < 0) {
          continue;
        }
        var name = component.getName().toString();
        var description = descriptions.get(name);
        if (description == null) {
          violations.add(
              violation(
                  line(start),
                  "RECORD_COMPONENT_PARAM_MISSING",
                  "record component " + name + " 缺少 @param 说明",
                  Map.of("record", recordName, "component", name)));
        } else if (!CHINESE.matcher(description).find()) {
          violations.add(
              violation(
                  line(start),
                  "RECORD_COMPONENT_PARAM_NOT_CHINESE",
                  "record component " + name + " 的 @param 说明必须包含中文",
                  Map.of("record", recordName, "component", name)));
        }
      }
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
