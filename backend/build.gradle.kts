import io.lexiflow.buildlogic.VerifyNoSkippedTestsTask
import io.lexiflow.buildlogic.VerifyProductLanguageTask
import io.lexiflow.buildlogic.VerifyProjectDependenciesTask
import org.gradle.api.artifacts.ProjectDependency
import org.gradle.api.tasks.diagnostics.DependencyReportTask
import org.gradle.api.tasks.testing.Test
import org.gradle.api.tasks.SourceSetContainer
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

val businessProjectPaths = listOf(":modules:lexicon", ":modules:enrichment")
val platformProjectPaths = listOf(":platform:adapters")
val productProjectPaths = businessProjectPaths + platformProjectPaths
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
    dependencies.add("testImplementation", junitJupiter)
    dependencies.add("testRuntimeOnly", junitPlatformLauncher)
}

configure(appProjectPaths.map(::project)) {
    pluginManager.apply("org.springframework.boot")
    dependencies {
        add("implementation", platform(springBootBom))
        add("implementation", "org.springframework.boot:spring-boot-starter-actuator")
        add("testImplementation", "org.springframework.boot:spring-boot-starter-test")
    }
}

project(":modules:enrichment") {
    dependencies.add("implementation", dependencies.project(":modules:lexicon"))
}

project(":platform:adapters") {
    dependencies {
        add("implementation", platform(springBootBom))
        add("implementation", "org.springframework.boot:spring-boot-starter-jdbc")
        add("runtimeOnly", "org.postgresql:postgresql")
        add("implementation", dependencies.project(":modules:lexicon"))
    }
    val platformSourceSets = extensions.getByType<SourceSetContainer>()
    tasks.named<Test>("test") {
        useJUnitPlatform {
            excludeTags("postgres")
        }
    }
    tasks.register<Test>("postgresIntegrationTest") {
        group = LifecycleBasePlugin.VERIFICATION_GROUP
        description = "执行基于 PostgreSQL 的持久化集成测试。"
        testClassesDirs = platformSourceSets["test"].output.classesDirs
        classpath = platformSourceSets["test"].runtimeClasspath
        useJUnitPlatform {
            includeTags("postgres")
        }
        systemProperty(
            "lexiflow.postgres.test.jdbcUrl",
            providers.environmentVariable("LEXIFLOW_POSTGRES_TEST_JDBC_URL").getOrElse(""),
        )
        systemProperty(
            "lexiflow.postgres.schema.file",
            rootProject.projectDir.parentFile.resolve("infra/postgres/schema.sql").absolutePath,
        )
    }
    tasks.register<JavaExec>("postgresInit") {
        group = "application"
        description = "使用 JDBC_URL 初始化空 schema；不会清空已有数据。"
        classpath = platformSourceSets["main"].runtimeClasspath
        mainClass.set("io.lexiflow.lexicon.platform.persistence.PostgresSchemaMain")
        workingDir(rootProject.projectDir.parentFile)
        if (providers.gradleProperty("postgresInitArgs").isPresent) {
            throw GradleException("postgresInit 只读取 JDBC_URL；请移除 -PpostgresInitArgs。")
        }
        val jdbcUrl = providers.environmentVariable("JDBC_URL").getOrElse("")
        doFirst {
            if (jdbcUrl.isBlank()) throw GradleException("请先设置 JDBC_URL，指向本项目开发库。")
        }
        args(jdbcUrl, rootProject.projectDir.parentFile.resolve("infra/postgres/schema.sql").absolutePath)
    }

    // 一个任务只对应一个动作，避免把 validate 误认为已导入；来源路径不经过 shell 拆词。
    mapOf(
        "lexiconValidate" to "validate",
        "lexiconPublish" to "publish",
        "lexiconBasicReport" to "basic-report",
        "lexiconPrewarmReport" to "prewarm-report",
    ).forEach { (taskName, action) ->
        tasks.register<JavaExec>(taskName) {
            group = "application"
            description = if (action == "publish") "导入并发布 ECDICT StarDict 词库。" else "只读执行词库 $action，不写数据库。"
            classpath = platformSourceSets["main"].runtimeClasspath
            mainClass.set("io.lexiflow.lexicon.platform.importer.LexiconImportMain")
            workingDir(rootProject.projectDir.parentFile)
            val input = providers.environmentVariable("STARDICT_CSV").getOrElse("")
            doFirst {
                if (input.isBlank()) throw GradleException("请先设置 STARDICT_CSV，指向本机 stardict.csv。")
            }
            args(action, "--input", input)
            if (action == "publish") {
                val jdbcUrl = providers.environmentVariable("JDBC_URL").getOrElse("")
                doFirst {
                    if (jdbcUrl.isBlank()) throw GradleException("请先设置 JDBC_URL，指向本项目开发库。")
                }
                args("--database-url", jdbcUrl, "--batch-source-id", "ecdict-stardict", "--batch-license-id", "MIT")
            }
        }
    }

}

project(":apps:api") {
    (businessProjectPaths + platformProjectPaths).forEach { path ->
        dependencies.add("implementation", dependencies.project(path))
    }
    dependencies.add("implementation", "org.springframework.boot:spring-boot-starter-webmvc")
    dependencies.add("implementation", "org.springframework.boot:spring-boot-starter-jdbc")
    dependencies.add("runtimeOnly", "org.postgresql:postgresql")
}

project(":tests:architecture") {
    dependencies {
        add("testImplementation", junitJupiter)
        add("testImplementation", archunitJunit5)
        add("testImplementation", platform(springBootBom))
        add("testImplementation", "org.springframework.boot:spring-boot-autoconfigure")
        add("testImplementation", "org.springframework.boot:spring-boot-jdbc")
        add("testImplementation", "org.springframework:spring-context")
        (productProjectPaths + appProjectPaths).forEach { path ->
            add("testImplementation", dependencies.project(path))
        }
    }
    tasks.withType<Test>().configureEach {
        systemProperty("lexiflow.backend.root", rootDir.absolutePath)
    }
}

project(":tests:quality-gates") {
    dependencies.add("testImplementation", junitJupiter)
}

project(":tests:integration") {
    val integrationSourceSets = extensions.getByType<SourceSetContainer>()
    val runtimeSmoke = integrationSourceSets.create("runtimeSmoke")
    configurations["runtimeSmokeImplementation"].extendsFrom(configurations["testImplementation"])
    configurations["runtimeSmokeRuntimeOnly"].extendsFrom(configurations["testRuntimeOnly"])
    dependencies {
        add("runtimeSmokeImplementation", junitJupiter)
        add("runtimeSmokeImplementation", dependencies.project(":platform:adapters"))
        add("runtimeSmokeRuntimeOnly", "org.postgresql:postgresql")
        add("runtimeSmokeRuntimeOnly", junitPlatformLauncher)
    }
    tasks.register<Test>("runtimeSmokeTest") {
        group = LifecycleBasePlugin.VERIFICATION_GROUP
        description = "执行隔离的 PostgreSQL/Redis 协议、schema 初始化、API 健康与 worker 启动 smoke 测试。"
        dependsOn(":apps:api:bootJar", ":apps:worker:bootJar")
        testClassesDirs = runtimeSmoke.output.classesDirs
        classpath = runtimeSmoke.runtimeClasspath
        useJUnitPlatform()
        systemProperty("lexiflow.postgres.test.jdbcUrl", providers.environmentVariable("LEXIFLOW_POSTGRES_TEST_JDBC_URL").getOrElse(""))
        systemProperty("lexiflow.redis.test.endpoint", providers.environmentVariable("LEXIFLOW_REDIS_TEST_ENDPOINT").getOrElse(""))
        systemProperty("lexiflow.postgres.schema.file", rootProject.projectDir.parentFile.resolve("infra/postgres/schema.sql").absolutePath)
        systemProperty("lexiflow.repository.root", rootProject.projectDir.parentFile.absolutePath)
        testLogging { events("failed") }
    }
}

val productSourceFiles = fileTree(rootDir) {
    include("apps/**", "modules/**", "platform/**")
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

val projectDependencyGraph = leafProjects.associate { source ->
    source.path to source.configurations
        .flatMap { it.dependencies.withType(ProjectDependency::class.java) }
        .map { it.path }
        .distinct()
        .sorted()
}
val verifyProjectDependencies = tasks.register<VerifyProjectDependenciesTask>("verifyProjectDependencies") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "拒绝违反 Modular Monolith 层级方向的项目依赖。"
    dependencyGraph.set(projectDependencyGraph.toSortedMap())
    resultFile.set(layout.buildDirectory.file("reports/project-dependencies/result.txt"))
}

val architectureTest = tasks.register("architectureTest") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行 Java 架构边界测试。"
    dependsOn(":tests:architecture:test")
}

val javaSourceGates = tasks.register("javaSourceGates") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行确定性的 Java Source Gate。"
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
    description = "Java 测试结果缺失、跳过或中止时失败。"
    dependsOn(leafProjects.map { "${it.path}:test" })
    testEntries.set(encodedTestEntries)
    resultFile.set(layout.buildDirectory.file("reports/verify-no-skipped-tests/result.txt"))
    outputs.upToDateWhen { false }
}

val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "汇总各 Java 子项目的 JaCoCo XML 与 HTML 报告。"
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

tasks.register("qualityFull") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行 fail-closed 的 Java 质量检查并汇总覆盖率报告。"
    dependsOn("check", jacocoRootReport)
}

tasks.register("deliveryFull") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行唯一完整 Java 交付聚合：质量检查及两个 boot JAR。"
    dependsOn("qualityFull", ":platform:adapters:postgresIntegrationTest", ":tests:integration:runtimeSmokeTest", "productBootJar")
}

tasks.register("spotlessApply") {
    group = "formatting"
    description = "按共享 convention 格式化所有 Java 子项目。"
    dependsOn(leafProjects.map { "${it.path}:spotlessApply" })
}

tasks.named<DependencyReportTask>("dependencies") {
    dependsOn(leafProjects.map { "${it.path}:dependencies" })
}

tasks.register("productBootJar") {
    group = LifecycleBasePlugin.BUILD_GROUP
    dependsOn(":apps:api:bootJar", ":apps:worker:bootJar")
}
