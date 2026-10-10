import io.lexiflow.buildlogic.BuildIdentity
import io.lexiflow.buildlogic.VerifyNoSkippedTestsTask
import io.lexiflow.buildlogic.VerifyProductLanguageTask
import io.lexiflow.buildlogic.VerifyProjectDependenciesTask
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.tasks.diagnostics.DependencyReportTask
import org.gradle.jvm.tasks.Jar
import org.gradle.testing.jacoco.tasks.JacocoReport

plugins {
    base
    jacoco
    id("lexiflow.java-library") apply false
    alias(libs.plugins.spring.boot) apply false
}

group = "io.lexiflow"
if (providers.gradleProperty("version").isPresent) throw GradleException("软件版本只读取 ops/release/version.txt，不接受 -Pversion 覆盖")
// 配置阶段仅读取基础版本，不启动 Node/Git；完整身份由类型化任务在执行阶段生成。
val requestedRelease = providers.gradleProperty("release").orNull
if (requestedRelease != null && requestedRelease !in setOf("true", "false")) throw GradleException("release 参数必须为 true 或 false")
val repoRoot = rootDir.parentFile
val baseVersion = BuildIdentity.readBaseVersion(repoRoot)
version = baseVersion

val deliveryRequested = gradle.startParameter.taskNames.any {
    it.substringAfterLast(':') in setOf("check", "deliveryFull")
}
if (deliveryRequested && (gradle.startParameter.excludedTaskNames.isNotEmpty() || gradle.startParameter.isDryRun)) {
    throw GradleException("Java delivery must execute all required tasks; exclusions and dry-run are diagnostics only")
}

dependencyLocking {
    lockAllConfigurations()
    lockMode.set(LockMode.STRICT)
}

val leafProjects = subprojects.filter { it.childProjects.isEmpty() }
val junitPlatformLauncher = libs.junit.platform.launcher
val junitJupiter = libs.junit.jupiter

configure(leafProjects) {
    group = rootProject.group
    version = rootProject.version
    pluginManager.apply("lexiflow.java-library")
    // 非 API 模块使用基础版本；API 模块在 api/build.gradle.kts 中绑定生成任务输出。
    if (project.path != ":api") {
        tasks.withType<Jar>().configureEach {
            manifest.attributes["Implementation-Version"] = rootProject.version.toString()
        }
    }
    dependencies.add("testImplementation", junitJupiter)
    dependencies.add("testRuntimeOnly", junitPlatformLauncher)
}

// 只调整已选任务顺序：单个 Test 诊断不额外选择质量任务，完整交付由 check 纳入全部质量检查。
// 不约束编译与资源生成，让静态检查继续共享测试、归档的正常前置任务。
val staticQualityTasks = leafProjects.flatMap { leaf ->
    listOf("spotlessCheck", "checkstyleMain", "checkstyleTest", "pmdMain", "pmdTest", "javadoc")
        .map { taskName -> leaf.tasks.named(taskName) }
}
leafProjects.forEach { leaf ->
    leaf.tasks.withType<org.gradle.api.tasks.testing.Test>().configureEach {
        mustRunAfter(staticQualityTasks)
    }
}
project(":api").tasks.withType<org.springframework.boot.gradle.tasks.bundling.BootJar>().configureEach {
    mustRunAfter(staticQualityTasks)
}

// API 插件在模块求值前装配，供 adapters 的集成测试引用 bootJar provider。
project(":api").pluginManager.apply("org.springframework.boot")

val productSourceFiles = fileTree(rootDir) {
    include("product/**")
    exclude {
        val segments = it.relativePath.segments.toList()
        val sourceIndex = segments.indexOf("src")
        val beforeSource = if (sourceIndex >= 0) segments.take(sourceIndex) else segments
        beforeSource.any { segment -> segment == "build" || segment == ".gradle" }
    }
}
val verifyProductLanguage = tasks.register<VerifyProductLanguageTask>("verifyProductLanguage") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "检查后端产品源码只使用 Java。"
    productSources.from(productSourceFiles)
    resultFile.set(layout.buildDirectory.file("reports/product-language/result.txt"))
}

// 模块完成配置后再采集完整项目依赖图，禁止空图漏检。
val verifyProjectDependencies = tasks.register<VerifyProjectDependenciesTask>("verifyProjectDependencies") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "拒绝违反 Modular Monolith 层级方向的项目依赖。"
    resultFile.set(layout.buildDirectory.file("reports/project-dependencies/result.txt"))
}
gradle.projectsEvaluated {
    val projectDependencyGraph = leafProjects.associate { source ->
        source.path to source.configurations
            .flatMap { it.dependencies.withType(ProjectDependency::class.java) }
            .map { it.path }
            .distinct()
            .sorted()
    }
    verifyProjectDependencies.configure {
        dependencyGraph.set(projectDependencyGraph.toSortedMap())
    }
}

val architectureTest = tasks.register("architectureTest") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行 Java 架构边界测试。"
    dependsOn(":architecture-tests:test")
}

val javaSourceGates = tasks.register("javaSourceGates") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行确定性的 Java Source Gate。"
    dependsOn(":quality-gates:runJavaSourceGates")
}

val encodedTestEntries = leafProjects.map { leaf ->
    listOf(
        leaf.path,
        leaf.layout.buildDirectory.dir("test-results/test").get().asFile.absolutePath,
        leaf.file("src/test/java").absolutePath,
    ).joinToString("\u0000")
}
val verifyNoSkippedJavaTests = tasks.register<VerifyNoSkippedTestsTask>("verifyNoSkippedJavaTests") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Java 测试结果缺失、跳过或中止时失败。"
    dependsOn(leafProjects.map { "${it.path}:test" })
    testEntries.set(encodedTestEntries)
    resultFile.set(layout.buildDirectory.file("reports/verify-no-skipped-tests/result.txt"))
    outputs.upToDateWhen { false }
}

val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "显式汇总各 Java 子项目的覆盖率报告；不参与质量判定。"
    dependsOn(leafProjects.map { "${it.path}:jacocoTestReport" })
    executionData.from(leafProjects.map { it.layout.buildDirectory.file("jacoco/test.exec") })
    sourceDirectories.from(leafProjects.map { it.layout.projectDirectory.dir("src/main/java") })
    classDirectories.from(leafProjects.map { it.layout.buildDirectory.dir("classes/java/main") })
    reports {
        xml.required.set(true)
        html.required.set(providers.gradleProperty("qualityHtmlReports").map { it.toBoolean() }.getOrElse(false))
    }
}

tasks.named("check") {
    dependsOn(gradle.includedBuild("build-logic").task(":check"))
    dependsOn(leafProjects.map { "${it.path}:check" })
    dependsOn(
        verifyProductLanguage,
        verifyProjectDependencies,
        architectureTest,
        javaSourceGates,
        verifyNoSkippedJavaTests,
    )
}

tasks.register("deliveryFull") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行唯一完整 Java 交付聚合：质量检查、集成测试及 API boot JAR。"
    dependsOn("check", ":adapters:postgresIntegrationTest", ":integration-tests:runtimeSmokeTest", ":api:bootJar")
}

tasks.register("spotlessApply") {
    group = "formatting"
    description = "按共享 convention 格式化所有 Java 子项目。"
    dependsOn(leafProjects.map { "${it.path}:spotlessApply" })
}

tasks.named<DependencyReportTask>("dependencies") {
    dependsOn(leafProjects.map { "${it.path}:dependencies" })
}
