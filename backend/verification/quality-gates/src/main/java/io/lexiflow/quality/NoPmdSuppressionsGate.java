package io.lexiflow.quality;

import com.sun.source.tree.AnnotationTree;
import com.sun.source.tree.AssignmentTree;
import com.sun.source.tree.BinaryTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.LiteralTree;
import com.sun.source.tree.NewArrayTree;
import com.sun.source.tree.ParenthesizedTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.TreePathScanner;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** 禁止源码通过 SuppressWarnings 绕过任何 PMD 规则。 */
public final class NoPmdSuppressionsGate implements JavaSourceGate {

  private static final String ID = "no-pmd-suppressions";

  /** 返回 PMD suppression 规则标识。 */
  @Override
  public String id() {
    return ID;
  }

  /** 检查主源码与测试源码的 suppression 注解和行抑制标记。 */
  @Override
  public List<QualityViolation> evaluate(JavaSourceContext context) {
    var violations = new ArrayList<QualityViolation>();
    for (var source : context.parsedSources().sources()) {
      new Scanner(context.parsedSources(), source, violations).scan(source.unit(), null);
      for (var comment : JvmCommentLexer.extract(source.source().text())) {
        if (comment.kind() == JvmCommentLexer.CommentKind.LINE
            && comment.text().contains("NOPMD")) {
          violations.add(
              new QualityViolation(
                  ID,
                  source.source().relativePath(),
                  comment.line(),
                  "PMD_SUPPRESSION_FORBIDDEN",
                  "不得用行注释压制 PMD",
                  Map.of("pmdRule", "NOPMD")));
        }
      }
    }
    return violations.stream().sorted().toList();
  }

  /** 遍历注解节点并定位 PMD suppression。 */
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
    public Void visitAnnotation(AnnotationTree tree, Void unused) {
      if (tree.getAnnotationType().toString().endsWith("SuppressWarnings")) {
        for (var argument : tree.getArguments()) {
          inspect(argument, tree);
        }
      }
      return super.visitAnnotation(tree, unused);
    }

    private void inspect(ExpressionTree value, AnnotationTree annotation) {
      if (value instanceof AssignmentTree assignment) {
        inspect(assignment.getExpression(), annotation);
      } else if (value instanceof NewArrayTree array && array.getInitializers() != null) {
        array.getInitializers().forEach(item -> inspect(item, annotation));
      } else {
        var warning = literalValue(value);
        if (warning == null
            || warning.equals("PMD")
            || warning.startsWith("PMD.")
            || warning.equals("unused")
            || warning.equals("all")) {
          var position =
              sources.docTrees().getSourcePositions().getStartPosition(source.unit(), annotation);
          var line = (int) source.unit().getLineMap().getLineNumber(position);
          violations.add(
              new QualityViolation(
                  ID,
                  source.source().relativePath(),
                  line,
                  warning == null ? "SUPPRESSION_VALUE_UNRESOLVED" : "PMD_SUPPRESSION_FORBIDDEN",
                  warning == null
                      ? "SuppressWarnings 必须使用可检查的字面量"
                      : "不得用 SuppressWarnings 压制 " + warning,
                  Map.of("pmdRule", warning == null ? value.toString() : warning)));
        }
      }
    }

    private static String literalValue(ExpressionTree value) {
      if (value instanceof LiteralTree literal && literal.getValue() instanceof String text) {
        return text;
      }
      if (value instanceof ParenthesizedTree parenthesized) {
        return literalValue(parenthesized.getExpression());
      }
      if (value instanceof BinaryTree binary && binary.getKind() == Tree.Kind.PLUS) {
        var left = literalValue(binary.getLeftOperand());
        var right = literalValue(binary.getRightOperand());
        return left != null && right != null ? left + right : null;
      }
      return null;
    }
  }
}
