import io.lexiflow.buildlogic.VerifyNoSkippedTestsTask
import io.lexiflow.buildlogic.VerifyProductLanguageTask
import io.lexiflow.buildlogic.VerifyProjectDependenciesTask
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.tasks.diagnostics.DependencyReportTask
import org.gradle.api.tasks.testing.Test
import org.gradle.testing.jacoco.tasks.JacocoReport

plugins {
    base
    jacoco
    id("lexiflow.java-library") apply false
    alias(libs.plugins.spring.boot) apply false
}

group = "io.lexiflow"
version = "0.1.0-SNAPSHOT"

val deliveryRequested = gradle.startParameter.taskNames.any {
    it.substringAfterLast(':') in setOf("check", "qualityFull", "deliveryFull")
}
if (deliveryRequested && (gradle.startParameter.excludedTaskNames.isNotEmpty() || gradle.startParameter.isDryRun)) {
    throw GradleException("Java delivery must execute all required tasks; exclusions and dry-run are diagnostics only")
}

dependencyLocking {
    lockAllConfigurations()
    lockMode.set(LockMode.STRICT)
}

val productProjectPaths = listOf(
    ":modules:foundation",
    ":modules:identity",
    ":modules:lexicon",
    ":modules:vocabulary",
    ":modules:content",
    ":modules:learning",
    ":modules:semantic",
    ":modules:enrichment",
    ":application:client-delivery",
    ":application:workflow",
    ":platform:persistence",
    ":platform:cache",
    ":platform:security",
    ":platform:observability",
)
val appProjectPaths = listOf(":apps:api", ":apps:worker")
val leafProjects = subprojects.filter { it.childProjects.isEmpty() }
val junitPlatformLauncher = libs.junit.platform.launcher
val junitJupiter = libs.junit.jupiter
val archunitJunit5 = libs.archunit.junit5
val springBootBom = "org.springframework.boot:spring-boot-dependencies:${libs.versions.spring.boot.get()}"

configure(leafProjects) {
    group = rootProject.group
    version = rootProject.version
    pluginManager.apply("lexiflow.java-library")
    dependencies.add("testRuntimeOnly", junitPlatformLauncher)
}

configure(appProjectPaths.map(::project)) {
    pluginManager.apply("org.springframework.boot")
    dependencies {
        add("implementation", platform(springBootBom))
        add("implementation", "org.springframework.boot:spring-boot-starter-actuator")
        add("testImplementation", "org.springframework.boot:spring-boot-starter-test")
        productProjectPaths.forEach { path -> add("implementation", project(path)) }
    }
}

project(":apps:api") {
    dependencies.add("implementation", "org.springframework.boot:spring-boot-starter-webmvc")
}

project(":tests:architecture") {
    dependencies {
        add("testImplementation", junitJupiter)
        add("testImplementation", archunitJunit5)
        add("testImplementation", platform(springBootBom))
        add("testImplementation", "org.springframework:spring-context")
        (productProjectPaths + appProjectPaths).forEach { path ->
            add("testImplementation", project(path))
        }
    }
    tasks.withType<Test>().configureEach {
        systemProperty("lexiflow.backend.root", rootDir.absolutePath)
    }
}

project(":tests:quality-gates") {
    dependencies.add("testImplementation", junitJupiter)
}

val productSourceFiles = fileTree(rootDir) {
    include("apps/**", "application/**", "modules/**", "platform/**")
    exclude {
        val segments = it.relativePath.segments.toList()
        val sourceIndex = segments.indexOf("src")
        val beforeSource = if (sourceIndex >= 0) segments.take(sourceIndex) else segments
        beforeSource.any { segment -> segment == "build" || segment == ".gradle" }
    }
}
val verifyProductLanguage = tasks.register<VerifyProductLanguageTask>("verifyProductLanguage") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Ensures backend product source remains Java."
    productSources.from(productSourceFiles)
    resultFile.set(layout.buildDirectory.file("reports/product-language/result.txt"))
}

val projectDependencyGraph = leafProjects.associate { source ->
    source.path to source.configurations
        .flatMap { it.dependencies.withType(ProjectDependency::class.java) }
        .map { it.path }
        .distinct()
        .sorted()
}
val verifyProjectDependencies = tasks.register<VerifyProjectDependenciesTask>("verifyProjectDependencies") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Rejects project dependencies that point against modular-monolith layers."
    dependencyGraph.set(projectDependencyGraph.toSortedMap())
    resultFile.set(layout.buildDirectory.file("reports/project-dependencies/result.txt"))
}

val architectureTest = tasks.register("architectureTest") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Runs the Java architecture boundary suite."
    dependsOn(":tests:architecture:test")
}

val javaSourceGates = tasks.register("javaSourceGates") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Runs deterministic Java source gates."
    dependsOn(":tests:quality-gates:runJavaSourceGates")
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
    description = "Fails when Java test results are absent, skipped, or aborted."
    dependsOn(leafProjects.map { "${it.path}:test" })
    testEntries.set(encodedTestEntries)
    resultFile.set(layout.buildDirectory.file("reports/verify-no-skipped-tests/result.txt"))
    outputs.upToDateWhen { false }
}

val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Aggregates JaCoCo XML and HTML reports across Java leaves."
    dependsOn(leafProjects.map { "${it.path}:jacocoTestReport" })
    executionData.from(leafProjects.map { it.layout.buildDirectory.file("jacoco/test.exec") })
    sourceDirectories.from(leafProjects.map { it.layout.projectDirectory.dir("src/main/java") })
    classDirectories.from(leafProjects.map { it.layout.buildDirectory.dir("classes/java/main") })
    reports {
        xml.required.set(true)
        html.required.set(true)
    }
}

tasks.named("check") {
    dependsOn(leafProjects.map { "${it.path}:check" })
    dependsOn(
        verifyProductLanguage,
        verifyProjectDependencies,
        architectureTest,
        javaSourceGates,
        verifyNoSkippedJavaTests,
    )
}

tasks.register("qualityFull") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Runs the fail-closed Java quality suite and aggregate coverage report."
    dependsOn("check", jacocoRootReport)
}

tasks.register("deliveryFull") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "Runs the single full Java delivery aggregate: quality checks and both boot jars."
    dependsOn("qualityFull", "productBootJar")
}

tasks.register("spotlessApply") {
    group = "formatting"
    description = "Formats every Java leaf through the shared convention."
    dependsOn(leafProjects.map { "${it.path}:spotlessApply" })
}

tasks.named<DependencyReportTask>("dependencies") {
    dependsOn(leafProjects.map { "${it.path}:dependencies" })
}

tasks.register("productBootJar") {
    group = LifecycleBasePlugin.BUILD_GROUP
    dependsOn(":apps:api:bootJar", ":apps:worker:bootJar")
}
