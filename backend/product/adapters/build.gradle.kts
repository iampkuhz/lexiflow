import org.gradle.api.tasks.SourceSetContainer
import org.gradle.language.jvm.tasks.ProcessResources

val repoRoot = rootProject.projectDir.parentFile
val generateDataGeneration = rootProject.tasks.named<io.lexiflow.buildlogic.GenerateDataGenerationTask>("generateDataGeneration")
val springBootBom = "org.springframework.boot:spring-boot-dependencies:${libs.versions.spring.boot.get()}"

dependencies {
    "implementation"(platform(springBootBom))
    "implementation"("ch.qos.logback:logback-classic")
    "implementation"("tools.jackson.core:jackson-core")
    "implementation"("org.springframework.boot:spring-boot-starter-jdbc")
    "runtimeOnly"(libs.sqlite.jdbc)
    "implementation"(project(":lexicon"))
}

val platformSourceSets = extensions.getByType<SourceSetContainer>()

// 源码导入入口与 API JAR 使用同一生成器，不依赖 API 才能获得身份资源。
tasks.named<ProcessResources>("processResources") {
    from(generateDataGeneration.flatMap { it.outputDirectory })
}

tasks.named<Test>("test") {
    useJUnitPlatform {
        excludeTags("sqlite")
    }
    systemProperty("lexiflow.sqlite.schema.file", repoRoot.resolve("infra/sqlite/schema.sql").absolutePath)
}

tasks.register<Test>("sqliteIntegrationTest") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行基于 SQLite 的持久化集成测试。"
    dependsOn(":api:bootJar")
    systemProperty(
        "lexiflow.release.test.bootJar",
        project(":api").tasks.named<org.springframework.boot.gradle.tasks.bundling.BootJar>("bootJar")
            .flatMap { it.archiveFile }.get().asFile.absolutePath,
    )
    testClassesDirs = platformSourceSets["test"].output.classesDirs
    classpath = platformSourceSets["test"].runtimeClasspath
    useJUnitPlatform {
        includeTags("sqlite")
    }
    systemProperty(
        "lexiflow.sqlite.schema.file",
        repoRoot.resolve("infra/sqlite/schema.sql").absolutePath,
    )
}

tasks.register<JavaExec>("sqliteInit") {
    group = "application"
    description = "使用 JDBC_URL 初始化空 schema；不会清空已有数据。"
    classpath = platformSourceSets["main"].runtimeClasspath
    mainClass.set("io.lexiflow.lexicon.platform.persistence.SqliteSchemaMain")
    workingDir(repoRoot)
    if (providers.gradleProperty("sqliteInitArgs").isPresent) {
        throw GradleException("sqliteInit 只读取 JDBC_URL；请移除 -PsqliteInitArgs。")
    }
    val jdbcUrl = providers.environmentVariable("JDBC_URL").getOrElse("")
    doFirst {
        if (jdbcUrl.isBlank()) throw GradleException("请先设置 JDBC_URL，指向本项目开发库。")
    }
    args(jdbcUrl, repoRoot.resolve("infra/sqlite/schema.sql").absolutePath)
}

tasks.register<JavaExec>("lexiconRebuild") {
    group = "application"
    description = "预检来源，交互确认后仅重建本项目词库表并完整导入。"
    classpath = platformSourceSets["main"].runtimeClasspath
    mainClass.set("io.lexiflow.lexicon.platform.importer.LexiconRebuildMain")
    workingDir(repoRoot)
    standardInput = System.`in`
    val jdbcUrl = providers.environmentVariable("JDBC_URL").getOrElse("")
    val input = providers.environmentVariable("STARDICT_CSV").getOrElse("")
    doFirst {
        if (jdbcUrl.isBlank()) throw GradleException("请先设置 JDBC_URL，指向本项目开发库。")
        if (input.isBlank()) throw GradleException("请先设置 STARDICT_CSV，指向本机 stardict.csv。")
    }
    args(jdbcUrl, repoRoot.resolve("infra/sqlite/schema.sql").absolutePath, input)
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
        workingDir(repoRoot)
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
