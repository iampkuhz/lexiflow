package io.lexiflow.architecture;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.tngtech.archunit.base.DescribedPredicate;
import com.tngtech.archunit.core.domain.JavaClass;
import com.tngtech.archunit.core.domain.JavaClasses;
import com.tngtech.archunit.core.importer.ClassFileImporter;
import com.tngtech.archunit.lang.ArchRule;
import io.lexiflow.api.ApiApplication;
import io.lexiflow.api.fixtures.SpringAllowedCompositionRoot;
import io.lexiflow.architecture.fixtures.api.SpringFalseCompositionRoot;
import io.lexiflow.architecture.fixtures.application.AllowedApplication;
import io.lexiflow.architecture.fixtures.application.model.AllowedModelValue;
import io.lexiflow.architecture.fixtures.application.model.ForbiddenModelService;
import io.lexiflow.architecture.fixtures.domain.ExternalLibraryAllowedDomain;
import io.lexiflow.architecture.fixtures.domain.ExternalLibraryForbiddenDomain;
import io.lexiflow.architecture.fixtures.domain.ForbiddenDomainToApplication;
import io.lexiflow.architecture.fixtures.domain.ForbiddenDomainToPlatform;
import io.lexiflow.architecture.fixtures.domain.SpringForbiddenDomain;
import io.lexiflow.architecture.fixtures.domain.ValidDomain;
import io.lexiflow.architecture.fixtures.enrichment.application.AllowedLexiconContract;
import io.lexiflow.architecture.fixtures.enrichment.application.ForbiddenLexiconApplication;
import io.lexiflow.architecture.fixtures.enrichment.application.ForbiddenLexiconImplementation;
import io.lexiflow.architecture.fixtures.lexicon.domain.ForbiddenEnrichmentDependency;
import io.lexiflow.architecture.fixtures.platform.PlatformAdapter;
import io.lexiflow.worker.WorkerApplication;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 验证产品编译类的模块边界，并用隔离 fixture 证明规则确实拒绝反例。 */
class LayerArchitectureTest {
  @TempDir Path directory;
  private static final List<String> PRODUCT_SOURCE_ROOTS = List.of("apps", "modules", "platform");

  private static final JavaClasses PRODUCT_CLASSES = importProductionClasses();

  @Test
  void productionImportIsBoundedAndContainsScaffoldMarkers() {
    assertFalse(PRODUCT_CLASSES.stream().findAny().isEmpty(), "产品 src/main 编译目录不能为空");
    assertTrue(containsClass(ApiApplication.class));
    assertTrue(containsClass(WorkerApplication.class));
    assertFalse(
        PRODUCT_CLASSES.stream().anyMatch(javaClass -> javaClass.getName().contains(".fixtures.")));
    System.out.println(
        "Architecture scope: productClasses="
            + PRODUCT_CLASSES.size()
            + " domainClasses="
            + PRODUCT_CLASSES.stream().filter(DOMAIN_ONLY).count());
  }

  @Test
  void rejectsMissingProductCompileDirectory() throws Exception {
    createProductRoots();
    var source = directory.resolve("modules/sample/src/main/java/probe/Example.java");
    Files.createDirectories(source.getParent());
    Files.writeString(source, "package probe; class Example {}");
    var failure =
        assertThrows(IllegalStateException.class, () -> productCompileDirectories(directory));
    assertTrue(failure.getMessage().contains("Missing expected product compile directory"));
  }

  @Test
  void rejectsEmptyProductCompileDirectory() throws Exception {
    createProductRoots();
    var source = directory.resolve("modules/sample/src/main/java/probe/Example.java");
    Files.createDirectories(source.getParent());
    Files.writeString(source, "package probe; class Example {}");
    Files.createDirectories(directory.resolve("modules/sample/build/classes/java/main"));
    var failure =
        assertThrows(IllegalStateException.class, () -> productCompileDirectories(directory));
    assertTrue(failure.getMessage().contains("Expected product compile directory is empty"));
  }

  @Test
  void rejectsEntirelyEmptyProductInput() throws Exception {
    createProductRoots();
    var failure =
        assertThrows(IllegalStateException.class, () -> productCompileDirectories(directory));
    assertTrue(failure.getMessage().contains("No product compile directories"));
  }

  private void createProductRoots() throws Exception {
    for (var root : PRODUCT_SOURCE_ROOTS) {
      Files.createDirectories(directory.resolve(root));
    }
  }

  @Test
  void domainAndApplicationDoNotDependOnConcreteAdapterLibraries() {
    checkIfSelectorExists(
        "external library boundary", externalLibraryRule(), PRODUCT_CLASSES, DOMAIN_OR_APPLICATION);
  }

  @Test
  void domainAndApplicationDoNotDependOnAdaptersOrCompositionRoots() {
    checkIfSelectorExists(
        "adapter and composition-root boundary",
        domainAndApplicationAdapterRule(),
        PRODUCT_CLASSES,
        DOMAIN_OR_APPLICATION);
  }

  @Test
  void domainDoesNotDependOnApplication() {
    checkIfSelectorExists(
        "domain-only boundary", domainApplicationRule(), PRODUCT_CLASSES, DOMAIN_ONLY);
  }

  @Test
  void modelsAndApplicationDoNotLeakPersistenceDoOrDaoTypes() {
    noClasses()
        .that()
        .resideInAnyPackage(
            "..domain..", "..application..", "io.lexiflow.api..", "io.lexiflow.worker..")
        .should()
        .dependOnClassesThat()
        .haveSimpleNameEndingWith("DO")
        .check(PRODUCT_CLASSES);
    noClasses()
        .that()
        .resideOutsideOfPackage("..platform..")
        .should()
        .dependOnClassesThat()
        .haveSimpleNameEndingWith("Dao")
        .check(PRODUCT_CLASSES);
  }

  @Test
  void springDependenciesStayInsideAppsAndPlatformAdapters() {
    var selected =
        PRODUCT_CLASSES.stream()
            .filter(javaClass -> !javaClass.getName().endsWith(".package-info"))
            .filter(
                javaClass ->
                    !isCompositionRoot(javaClass) && !hasPackageSegment(javaClass, "platform"))
            .toList();
    if (!selected.isEmpty()) {
      springBoundaryRule().check(PRODUCT_CLASSES);
      return;
    }
    assertTrue(
        selected.isEmpty(),
        "Spring boundary selector is explicitly scoped empty in Phase 1 scaffold");
  }

  @Test
  void isolatedForbiddenDomainDependencyIsRejected() {
    var classes =
        new ClassFileImporter()
            .importClasses(ForbiddenDomainToPlatform.class, PlatformAdapter.class);
    assertThrows(AssertionError.class, () -> domainAndApplicationAdapterRule().check(classes));
  }

  @Test
  void isolatedExternalLibraryBoundaryRejectsForbiddenAndAcceptsAllowed() {
    var forbidden = new ClassFileImporter().importClasses(ExternalLibraryForbiddenDomain.class);
    var allowed = new ClassFileImporter().importClasses(ExternalLibraryAllowedDomain.class);
    assertThrows(AssertionError.class, () -> externalLibraryRule().check(forbidden));
    externalLibraryRule().check(allowed);
  }

  @Test
  void isolatedDomainApplicationBoundaryRejectsForbiddenAndAcceptsAllowed() {
    var forbidden =
        new ClassFileImporter()
            .importClasses(ForbiddenDomainToApplication.class, AllowedApplication.class);
    var allowed =
        new ClassFileImporter().importClasses(ValidDomain.class, AllowedApplication.class);
    assertThrows(AssertionError.class, () -> domainApplicationRule().check(forbidden));
    domainApplicationRule().check(allowed);
  }

  @Test
  void isolatedSpringBoundaryRejectsDomainAndAcceptsCompositionRoot() {
    var forbidden = new ClassFileImporter().importClasses(SpringForbiddenDomain.class);
    var allowed =
        new ClassFileImporter()
            .importClasses(SpringAllowedCompositionRoot.class, ValidDomain.class);
    assertThrows(AssertionError.class, () -> springBoundaryRule().check(forbidden));
    springBoundaryRule().check(allowed);
  }

  @Test
  void springIsForbiddenInAnApiPackageOutsideTheActualCompositionRoots() {
    var classes = new ClassFileImporter().importClasses(SpringFalseCompositionRoot.class);
    assertThrows(AssertionError.class, () -> springBoundaryRule().check(classes));
  }

  @Test
  void isolatedValidDomainAndApplicationDependenciesPass() {
    var classes =
        new ClassFileImporter().importClasses(ValidDomain.class, AllowedApplication.class);
    domainAndApplicationAdapterRule().check(classes);
  }

  @Test
  void consolidatedModulesRetainBothLogicalLayers() {
    for (var domain : List.of("lexicon", "enrichment")) {
      for (var layer : List.of("domain", "application")) {
        var prefix = "io.lexiflow." + domain + "." + layer + ".";
        assertTrue(
            PRODUCT_CLASSES.stream()
                .anyMatch(
                    type ->
                        type.getName().startsWith(prefix)
                            && !type.getName().endsWith(".package-info")),
            "架构扫描缺少 " + prefix);
      }
    }
  }

  @Test
  void businessDomainsUseOnlyDeclaredPublicContracts() {
    enrichmentContractRule().check(PRODUCT_CLASSES);
    lexiconOwnershipRule().check(PRODUCT_CLASSES);
  }

  @Test
  void modelPackagesDoNotDependOnServicesOrPolicies() {
    modelResponsibilityRule().check(PRODUCT_CLASSES);
    importValidationRule().check(PRODUCT_CLASSES);
  }

  @Test
  void isolatedCrossDomainBoundariesRejectImplementationsAndReverseEdges() {
    for (var type :
        List.of(ForbiddenLexiconImplementation.class, ForbiddenLexiconApplication.class)) {
      var classes = new ClassFileImporter().importClasses(type);
      assertThrows(AssertionError.class, () -> enrichmentContractRule().check(classes));
    }
    enrichmentContractRule()
        .check(new ClassFileImporter().importClasses(AllowedLexiconContract.class));
    var reversed = new ClassFileImporter().importClasses(ForbiddenEnrichmentDependency.class);
    assertThrows(AssertionError.class, () -> lexiconOwnershipRule().check(reversed));
  }

  @Test
  void isolatedModelBoundaryRejectsServicesAndAcceptsValues() {
    var badHelper =
        new ClassFileImporter()
            .importClasses(
                io.lexiflow.architecture.fixtures.application.importing.validation
                    .ForbiddenValidationService.class);
    assertThrows(AssertionError.class, () -> importValidationRule().check(badHelper));
    var forbidden = new ClassFileImporter().importClasses(ForbiddenModelService.class);
    assertThrows(AssertionError.class, () -> modelResponsibilityRule().check(forbidden));
    modelResponsibilityRule().check(new ClassFileImporter().importClasses(AllowedModelValue.class));
  }

  private static ArchRule enrichmentContractRule() {
    return noClasses()
        .that()
        .resideInAPackage("..enrichment..")
        .should()
        .dependOnClassesThat(
            new DescribedPredicate<>("非公开词库合同") {
              @Override
              public boolean test(JavaClass type) {
                return hasPackageSegment(type, "lexicon")
                    && !(type.getPackageName().equals("io.lexiflow.lexicon.domain.model")
                        || type.getPackageName().startsWith("io.lexiflow.lexicon.domain.model."))
                    && !type.getPackageName().equals("io.lexiflow.lexicon.domain.port");
              }
            });
  }

  private static ArchRule lexiconOwnershipRule() {
    return noClasses()
        .that()
        .resideInAPackage("..lexicon..")
        .should()
        .dependOnClassesThat()
        .resideInAPackage("..enrichment..");
  }

  private static ArchRule importValidationRule() {
    return noClasses()
        .that()
        .resideInAPackage("..importing.validation..")
        .should()
        .dependOnClassesThat()
        .resideOutsideOfPackages("java..", "..importing.validation..");
  }

  private static ArchRule modelResponsibilityRule() {
    return noClasses()
        .that()
        .resideInAPackage("..model..")
        .should()
        .dependOnClassesThat(
            new DescribedPredicate<>("服务、策略或端口实现职责") {
              @Override
              public boolean test(JavaClass type) {
                return (hasPackageSegment(type, "application")
                        && !hasPackageSegment(type, "model")
                        && !type.getPackageName()
                            .equals("io.lexiflow.lexicon.application.importing.validation"))
                    || type.getPackageName().contains(".domain.policy")
                    || type.getPackageName().contains(".domain.catalog")
                    || type.getPackageName().contains(".domain.port");
              }
            });
  }

  private static ArchRule domainAndApplicationAdapterRule() {
    return noClasses()
        .that()
        .resideInAnyPackage("..domain..", "..application..")
        .should()
        .dependOnClassesThat()
        .resideInAnyPackage(
            "..platform..", "..adapter..", "..adapters..", "..provider..", "..api..", "..worker..");
  }

  private static ArchRule externalLibraryRule() {
    return noClasses()
        .that()
        .resideInAnyPackage("..domain..", "..application..")
        .should()
        .dependOnClassesThat()
        .resideInAnyPackage(
            "java.net..",
            "java.sql..",
            "javax.sql..",
            "jakarta.persistence..",
            "jakarta.servlet..",
            "org.postgresql..",
            "io.lettuce..",
            "redis.clients..",
            "com.openai..",
            "com.anthropic..",
            "com.google.genai..",
            "org.springframework..");
  }

  private static ArchRule domainApplicationRule() {
    return noClasses()
        .that()
        .resideInAnyPackage("..domain..")
        .should()
        .dependOnClassesThat()
        .resideInAnyPackage("..application..");
  }

  private static ArchRule springBoundaryRule() {
    return noClasses()
        .that()
        .resideOutsideOfPackages("io.lexiflow.api..", "io.lexiflow.worker..", "..platform..")
        .should()
        .dependOnClassesThat()
        .resideInAPackage("org.springframework..");
  }

  private static final Predicate<JavaClass> DOMAIN_OR_APPLICATION =
      javaClass ->
          !javaClass.getName().endsWith(".package-info")
              && (hasPackageSegment(javaClass, "domain")
                  || hasPackageSegment(javaClass, "application"));

  private static final Predicate<JavaClass> DOMAIN_ONLY =
      javaClass ->
          !javaClass.getName().endsWith(".package-info") && hasPackageSegment(javaClass, "domain");

  private static boolean hasPackageSegment(JavaClass javaClass, String segment) {
    return ("." + javaClass.getPackageName() + ".").contains("." + segment + ".");
  }

  private static boolean isCompositionRoot(JavaClass javaClass) {
    var packageName = javaClass.getPackageName();
    return List.of("io.lexiflow.api", "io.lexiflow.worker").stream()
        .anyMatch(root -> packageName.equals(root) || packageName.startsWith(root + "."));
  }

  private static void checkIfSelectorExists(
      String selector, ArchRule rule, JavaClasses classes, Predicate<JavaClass> predicate) {
    var selected = classes.stream().filter(predicate).toList();
    if (!selected.isEmpty()) {
      rule.check(classes);
      return;
    }
    assertTrue(
        selected.isEmpty(), selector + " selector is explicitly scoped empty in Phase 1 scaffold");
  }

  private static boolean containsClass(Class<?> expected) {
    return PRODUCT_CLASSES.stream()
        .anyMatch(javaClass -> javaClass.getName().equals(expected.getName()));
  }

  private static JavaClasses importProductionClasses() {
    var configuredRoot = System.getProperty("lexiflow.backend.root");
    if (configuredRoot == null || configuredRoot.isBlank()) {
      throw new IllegalStateException(
          "lexiflow.backend.root is required for bounded architecture import");
    }
    var backendRoot = Path.of(configuredRoot).toAbsolutePath().normalize();
    var classDirectories = productCompileDirectories(backendRoot);
    return new ClassFileImporter().importPaths(classDirectories);
  }

  private static List<Path> productCompileDirectories(Path backendRoot) {
    var directories = new ArrayList<Path>();
    for (var sourceRoot : PRODUCT_SOURCE_ROOTS) {
      var root = backendRoot.resolve(sourceRoot);
      if (!Files.isDirectory(root)) {
        throw new IllegalStateException("Missing product source root: " + root);
      }
      var projects = new ArrayList<Path>();
      projects.add(root);
      try (var children = Files.list(root)) {
        children.filter(Files::isDirectory).forEach(projects::add);
      } catch (java.io.IOException exception) {
        throw new IllegalStateException("Cannot enumerate product source root: " + root, exception);
      }
      projects.stream()
          .filter(LayerArchitectureTest::hasProductionJavaSource)
          .map(project -> project.resolve("build/classes/java/main"))
          .forEach(
              directory -> {
                if (!Files.isDirectory(directory)) {
                  throw new IllegalStateException(
                      "Missing expected product compile directory: " + directory);
                }
                if (!hasCompiledClass(directory)) {
                  throw new IllegalStateException(
                      "Expected product compile directory is empty: " + directory);
                }
                directories.add(directory);
              });
    }
    if (directories.isEmpty()) {
      throw new IllegalStateException("No product compile directories found below: " + backendRoot);
    }
    return List.copyOf(directories);
  }

  private static boolean hasProductionJavaSource(Path project) {
    var sourceRoot = project.resolve("src/main/java");
    if (!Files.isDirectory(sourceRoot)) {
      return false;
    }
    try (var files = Files.walk(sourceRoot)) {
      return files.anyMatch(
          path ->
              Files.isRegularFile(path)
                  && path.toString().endsWith(".java")
                  && !path.getFileName().toString().equals("package-info.java")
                  && !path.getFileName().toString().equals("module-info.java"));
    } catch (java.io.IOException exception) {
      throw new IllegalStateException(
          "Cannot inspect product source root: " + sourceRoot, exception);
    }
  }

  private static boolean hasCompiledClass(Path directory) {
    try (var files = Files.walk(directory)) {
      return files.anyMatch(
          path -> Files.isRegularFile(path) && path.toString().endsWith(".class"));
    } catch (java.io.IOException exception) {
      throw new IllegalStateException(
          "Cannot inspect product compile directory: " + directory, exception);
    }
  }
}
