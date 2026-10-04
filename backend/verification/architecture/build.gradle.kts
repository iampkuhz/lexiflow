val springBootBom = "org.springframework.boot:spring-boot-dependencies:${libs.versions.spring.boot.get()}"
val archunitJunit5 = libs.archunit.junit5

dependencies {
    "testImplementation"(archunitJunit5)
    "testImplementation"(platform(springBootBom))
    "testImplementation"("org.springframework.boot:spring-boot-autoconfigure")
    "testImplementation"("org.springframework.boot:spring-boot-jdbc")
    "testImplementation"("org.springframework:spring-context")
    "testImplementation"(project(":lexicon"))
    "testImplementation"(project(":enrichment"))
    "testImplementation"(project(":adapters"))
    "testImplementation"(project(":api"))
}

tasks.withType<Test>().configureEach {
    systemProperty("lexiflow.backend.root", rootDir.absolutePath)
}
