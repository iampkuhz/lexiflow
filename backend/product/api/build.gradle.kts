import io.lexiflow.buildlogic.GenerateSoftwareIdentityTask
import io.lexiflow.buildlogic.GenerateDataGenerationTask
import io.lexiflow.buildlogic.IdentityVersionArgumentProvider
import io.lexiflow.buildlogic.PublishVersionedBootJarAction
import org.gradle.jvm.tasks.Jar
import org.gradle.language.jvm.tasks.ProcessResources
import org.springframework.boot.gradle.tasks.bundling.BootJar

val repositoryRoot = rootProject.projectDir.parentFile
val springBootBom = "org.springframework.boot:spring-boot-dependencies:${libs.versions.spring.boot.get()}"

dependencies {
    "implementation"(platform(springBootBom))
    "implementation"("org.springframework.boot:spring-boot-starter-actuator")
    "implementation"("org.springframework.boot:spring-boot-starter-webmvc")
    "implementation"("org.springframework.boot:spring-boot-starter-jdbc")
    "runtimeOnly"("org.postgresql:postgresql")
    "implementation"(project(":lexicon"))
    "implementation"(project(":enrichment"))
    "implementation"(project(":adapters"))
    "testImplementation"("org.springframework.boot:spring-boot-starter-test")
}

// 类型化身份生成任务：执行阶段调用 Node，配置阶段不触发。
val releaseMode = providers.gradleProperty("release").map { it == "true" }.getOrElse(false)
val versionFilePath = File(repositoryRoot, "ops/release/version.txt").absolutePath
val generateSoftwareIdentity = tasks.register<GenerateSoftwareIdentityTask>("generateSoftwareIdentity") {
    repoRoot.set(repositoryRoot)
    this.release.set(releaseMode)
    versionFile.set(layout.projectDirectory.file(versionFilePath))
    versionScript.set(layout.projectDirectory.file(File(repositoryRoot, "ops/release/version.mjs").absolutePath))
    outputDirectory.set(layout.buildDirectory.dir("generated/software-identity"))
    manifestFile.set(layout.buildDirectory.file("generated/software-identity-manifest/MANIFEST.MF"))
}

val generateDataGeneration = tasks.register<GenerateDataGenerationTask>("generateDataGeneration") {
    repoRoot.set(repositoryRoot)
    script.set(File(repositoryRoot, "ops/dataset/generation-version.mjs"))
    outputDirectory.set(layout.buildDirectory.dir("generated/data-generation"))
}

tasks.named<ProcessResources>("processResources") {
    from(generateSoftwareIdentity.flatMap { it.outputDirectory })
    from(generateDataGeneration.flatMap { it.outputDirectory })
    from(repositoryRoot.resolve("infra/postgres/schema.sql")) {
        into("META-INF")
        rename { "lexiflow-schema.sql" }
    }
}

// IDE 模型只需稳定的内部归档路径；完整身份只在任务执行时读取。
val generatedVersionFile = generateSoftwareIdentity.flatMap { it.outputDirectory.file("META-INF/lexiflow-version.txt") }
val identityJsonFile = generateSoftwareIdentity.flatMap { it.outputDirectory.file("META-INF/lexiflow-build.json") }
tasks.named<Jar>("jar") {
    dependsOn(generateSoftwareIdentity)
    dependsOn(generateDataGeneration)
    archiveFileName.set("api-plain.jar")
    destinationDirectory.set(layout.buildDirectory.dir("internal/plain"))
    manifest.from(generateSoftwareIdentity.flatMap { it.manifestFile })
}
tasks.named<BootJar>("bootJar") {
    dependsOn(generateSoftwareIdentity)
    dependsOn(generateDataGeneration)
    archiveFileName.set("api-internal.jar")
    destinationDirectory.set(layout.buildDirectory.dir("internal/boot"))
    manifest.from(generateSoftwareIdentity.flatMap { it.manifestFile })
    inputs.file(generatedVersionFile)
    inputs.file(identityJsonFile)
    val publicDirectory = layout.buildDirectory.dir("libs")
    outputs.dir(publicDirectory)
    doLast(PublishVersionedBootJarAction(
        layout.buildDirectory.dir("generated/software-identity").get().asFile.absolutePath,
        publicDirectory.get().asFile.absolutePath,
    ))
}

// JVM 参数由类型化 provider 在执行期读取生成资源，配置阶段不读取完整身份。
tasks.withType<Test>().configureEach {
    dependsOn(generateSoftwareIdentity)
    systemProperty("lexiflow.repository.root", repositoryRoot.absolutePath)
    jvmArgumentProviders.add(objects.newInstance(IdentityVersionArgumentProvider::class.java).apply {
        identityVersionFile.set(generatedVersionFile)
    })
}
