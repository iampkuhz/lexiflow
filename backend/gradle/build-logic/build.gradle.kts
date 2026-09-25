plugins {
    `kotlin-dsl`
}

dependencies {
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.platform.launcher)
    implementation("com.diffplug.spotless:com.diffplug.spotless.gradle.plugin:${libs.versions.spotless.get()}")
}

dependencyLocking {
    lockAllConfigurations()
    lockMode.set(LockMode.STRICT)
}

tasks.test {
    useJUnitPlatform()
    jvmArgs("--add-opens=java.base/java.lang=ALL-UNNAMED")
}
