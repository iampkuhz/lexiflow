import io.lexiflow.buildlogic.BuildIdentityExtension
import io.lexiflow.buildlogic.GenerateSoftwareIdentityTask
import org.gradle.language.jvm.tasks.ProcessResources

val repoRoot = rootProject.projectDir.parentFile
val identity = rootProject.extensions.getByType(BuildIdentityExtension::class.java)
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

val generateSoftwareIdentity = tasks.register<GenerateSoftwareIdentityTask>("generateSoftwareIdentity") {
    identityJson.set(identity.json)
    softwareVersion.set(identity.softwareVersion)
    outputDirectory.set(layout.buildDirectory.dir("generated/software-identity"))
}

tasks.named<ProcessResources>("processResources") {
    from(generateSoftwareIdentity)
    from(repoRoot.resolve("infra/postgres/schema.sql")) {
        into("META-INF")
        rename { "lexiflow-schema.sql" }
    }
}

tasks.withType<Test>().configureEach {
    systemProperty("lexiflow.repository.root", repoRoot.absolutePath)
    systemProperty("lexiflow.build.version", rootProject.version.toString())
}
