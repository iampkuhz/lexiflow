import org.gradle.api.tasks.SourceSetContainer
import org.gradle.api.attributes.Bundling
import org.gradle.api.attributes.Category
import org.gradle.api.attributes.LibraryElements
import org.gradle.api.attributes.Usage
import org.gradle.jvm.tasks.Jar
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
    "implementation"(project(":enrichment"))
}

val platformSourceSets = extensions.getByType<SourceSetContainer>()

// A release-only outgoing artifact keeps preparation evidence available to
// source imports and the development JAR while removing it from runtime.
val releaseRuntimeJar = tasks.register<Jar>("releaseRuntimeJar") {
    from(platformSourceSets["main"].output)
    archiveFileName.set(tasks.named<Jar>("jar").flatMap { it.archiveFileName })
    destinationDirectory.set(layout.buildDirectory.dir("release-runtime"))
    exclude(
        "lexicon/reviewed-phrases-review.properties",
        "lexicon/reviewed-phrases-review.md",
        "lexicon/reviewed-pos-defaults.tsv",
        "lexicon/reviewed-source-defaults.tsv",
        "lexicon/reviewed-source-defaults.sha256",
        "lexicon/reviewed-phrases.tsv",
        "lexicon/reviewed-phrases.sha256",
        "lexicon/wordnet-lexical-evidence.tsv",
        "lexicon/wordnet-lexical-evidence.sha256",
    )
}

configurations.create("releaseRuntimeElements") {
    isCanBeConsumed = true
    isCanBeResolved = false
    extendsFrom(configurations.getByName("runtimeElements"))
    attributes {
        attribute(Usage.USAGE_ATTRIBUTE, objects.named(Usage.JAVA_RUNTIME))
        attribute(Category.CATEGORY_ATTRIBUTE, objects.named(Category.LIBRARY))
        attribute(LibraryElements.LIBRARY_ELEMENTS_ATTRIBUTE, objects.named(LibraryElements.JAR))
        attribute(Bundling.BUNDLING_ATTRIBUTE, objects.named(Bundling.EXTERNAL))
    }
    outgoing.artifact(releaseRuntimeJar)
}

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

// 来源准备与持久化分开；两个原件只读，修复产物必须是新文件。
tasks.register<JavaExec>("lexiconPrepare") {
    group = "application"
    description = "用固定 ECDICT 原件补齐独立词义，创建新的准备 CSV。"
    classpath = platformSourceSets["main"].runtimeClasspath
    mainClass.set("io.lexiflow.lexicon.platform.importer.StardictSourcePreparationMain")
    workingDir(repoRoot)
    val prepared = providers.environmentVariable("STARDICT_CSV").getOrElse("")
    val full = providers.environmentVariable("ECDICT_BASE_CSV").getOrElse("")
    val output = providers.environmentVariable("LEXICON_PREPARED_OUTPUT").getOrElse("")
    doFirst {
        if (listOf(prepared, full, output).any { it.isBlank() }) {
            throw GradleException("请设置 STARDICT_CSV、ECDICT_BASE_CSV 与 LEXICON_PREPARED_OUTPUT。")
        }
    }
    args(prepared, full, output)
}
