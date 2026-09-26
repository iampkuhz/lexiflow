import org.gradle.api.tasks.JavaExec
import org.gradle.api.tasks.PathSensitivity

val backendRoot = rootProject.layout.projectDirectory
val reportFile = layout.buildDirectory.file("reports/java-source-gates/report.json")
val javaSources = rootProject.fileTree(backendRoot) {
    include("**/src/main/java/**/*.java", "**/src/test/java/**/*.java")
    exclude {
        val segments = it.relativePath.segments.toList()
        val sourceIndex = segments.indexOf("src")
        val beforeSource = if (sourceIndex >= 0) segments.take(sourceIndex) else segments
        beforeSource.any { segment -> segment == "build" || segment == ".gradle" }
    }
}

tasks.register<JavaExec>("runJavaSourceGates") {
    group = "verification"
    description = "执行确定性的 LexiFlow Java Source Gate。"
    dependsOn(tasks.named("classes"))
    mainClass.set("io.lexiflow.quality.QualityGateMain")
    classpath = sourceSets["main"].runtimeClasspath
    jvmArgs("--add-modules", "jdk.compiler")
    inputs.files(javaSources)
        .withPropertyName("javaSources")
        .withPathSensitivity(PathSensitivity.RELATIVE)
    outputs.file(reportFile).withPropertyName("report")
    args(backendRoot.asFile.absolutePath, reportFile.get().asFile.absolutePath)
}

tasks.named("check") {
    dependsOn("runJavaSourceGates")
}
