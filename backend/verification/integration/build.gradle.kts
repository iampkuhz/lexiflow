import org.gradle.api.tasks.SourceSetContainer

val repoRoot = rootProject.projectDir.parentFile
val springBootBom = "org.springframework.boot:spring-boot-dependencies:${libs.versions.spring.boot.get()}"
val junitPlatformLauncher = libs.junit.platform.launcher
val junitJupiter = libs.junit.jupiter

val integrationSourceSets = extensions.getByType<SourceSetContainer>()
val runtimeSmoke = integrationSourceSets.create("runtimeSmoke")
configurations["runtimeSmokeImplementation"].extendsFrom(configurations["testImplementation"])
configurations["runtimeSmokeRuntimeOnly"].extendsFrom(configurations["testRuntimeOnly"])

dependencies {
    "runtimeSmokeImplementation"(platform(springBootBom))
    "runtimeSmokeImplementation"(junitJupiter)
    "runtimeSmokeImplementation"("tools.jackson.core:jackson-databind")
    "runtimeSmokeImplementation"(project(":adapters"))
    "runtimeSmokeRuntimeOnly"(libs.sqlite.jdbc)
    "runtimeSmokeRuntimeOnly"(junitPlatformLauncher)
}

tasks.register<Test>("runtimeSmokeTest") {
    group = LifecycleBasePlugin.VERIFICATION_GROUP
    description = "执行隔离的 SQLite/Redis 协议、schema 初始化与 API 健康 smoke 测试。"
    dependsOn(":api:bootJar")
    testClassesDirs = runtimeSmoke.output.classesDirs
    classpath = runtimeSmoke.runtimeClasspath
    useJUnitPlatform()
    systemProperty("lexiflow.redis.test.endpoint", providers.environmentVariable("LEXIFLOW_REDIS_TEST_ENDPOINT").getOrElse(""))
    systemProperty("lexiflow.sqlite.schema.file", repoRoot.resolve("infra/sqlite/schema.sql").absolutePath)
    systemProperty("lexiflow.repository.root", repoRoot.absolutePath)
    systemProperty("lexiflow.build.version", rootProject.version.toString())
    systemProperty("lexiflow.runtimeSmoke.classpath", runtimeSmoke.runtimeClasspath.asPath)
    systemProperty("lexiflow.runtimeSmoke.bootJar", project(":api").tasks.named<org.springframework.boot.gradle.tasks.bundling.BootJar>("bootJar").flatMap { it.archiveFile }.get().asFile.absolutePath)
    testLogging { events("failed") }
}

tasks.register<JavaExec>("produceSyntheticReleaseDatasets") {
    val fixtureOutput = providers.systemProperty("lexiflow.release.fixture.output").getOrElse("")
    group = "verification"
    description = "通过真实合成发布链生产 runtime Check 专用资料包。"
    dependsOn(":api:bootJar")
    classpath = runtimeSmoke.runtimeClasspath
    mainClass.set("io.lexiflow.integration.SyntheticReleaseDatasetProducer")
    systemProperty("lexiflow.sqlite.schema.file", repoRoot.resolve("infra/sqlite/schema.sql").absolutePath)
    systemProperty("lexiflow.repository.root", repoRoot.absolutePath)
    systemProperty("lexiflow.build.version", rootProject.version.toString())
    systemProperty("lexiflow.runtimeSmoke.classpath", runtimeSmoke.runtimeClasspath.asPath)
    systemProperty("lexiflow.runtimeSmoke.bootJar", project(":api").tasks.named<org.springframework.boot.gradle.tasks.bundling.BootJar>("bootJar").flatMap { it.archiveFile }.get().asFile.absolutePath)
    systemProperty("lexiflow.release.fixture.output", fixtureOutput)
    doFirst {
        if (fixtureOutput.isBlank()) {
            throw GradleException("必须显式指定 -Dlexiflow.release.fixture.output 绝对输出路径。")
        }
    }
}
