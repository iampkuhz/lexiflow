import io.lexiflow.buildlogic.GenerateJavadocAnchorTask
import org.gradle.api.plugins.quality.Checkstyle
import org.gradle.api.plugins.quality.Pmd
import org.gradle.api.tasks.javadoc.Javadoc
import org.gradle.api.tasks.testing.Test
import org.gradle.external.javadoc.JavadocMemberLevel
import org.gradle.external.javadoc.StandardJavadocDocletOptions
import org.gradle.testing.jacoco.tasks.JacocoReport

plugins {
    id("com.diffplug.spotless")
    checkstyle
    pmd
    jacoco
}

val qualityConfig = rootProject.layout.projectDirectory.dir("gradle/config")
// HTML 仅供人工浏览，不参与合格判定；检查结果 XML 保留，浏览产物显式开启。
val htmlQualityReports = providers.gradleProperty("qualityHtmlReports")
    .map { it.toBoolean() }
    .getOrElse(false)
val generateJavadocAnchor = tasks.register<GenerateJavadocAnchorTask>("generateJavadocAnchor") {
    outputFile.set(layout.buildDirectory.file("generated/sources/javadoc/JavadocAnchor.java"))
}

spotless {
    java {
        target("src/*/java/**/*.java")
        googleJavaFormat("1.28.0")
        removeUnusedImports()
        trimTrailingWhitespace()
        endWithNewline()
    }
}

checkstyle {
    toolVersion = "10.21.4"
    configDirectory.set(qualityConfig.dir("checkstyle"))
    configFile = qualityConfig.file("checkstyle/checkstyle.xml").asFile
    isIgnoreFailures = false
    maxErrors = 0
    maxWarnings = 0
}

tasks.withType<Checkstyle>().configureEach {
    reports {
        xml.required.set(true)
        html.required.set(htmlQualityReports)
    }
}

pmd {
    toolVersion = "7.25.0"
    ruleSetFiles = files(qualityConfig.file("pmd/pmd.xml"))
    ruleSets = listOf()
    isIgnoreFailures = false
    incrementalAnalysis = true
}

tasks.withType<Pmd>().configureEach {
    reports {
        xml.required.set(true)
        html.required.set(htmlQualityReports)
    }
}

tasks.withType<Javadoc>().configureEach {
    source(generateJavadocAnchor.flatMap { it.outputFile })
    val docOptions = options as StandardJavadocDocletOptions
    docOptions.encoding = "UTF-8"
    docOptions.docEncoding = "UTF-8"
    docOptions.charSet = "UTF-8"
    docOptions.memberLevel = JavadocMemberLevel.PRIVATE
    docOptions.addBooleanOption("Xdoclint:all,-missing", true)
    docOptions.addBooleanOption("Werror", true)
    docOptions.noTimestamp(true)
}

jacoco {
    toolVersion = "0.8.14"
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
    jvmArgs("--add-modules", "jdk.compiler")
    reports {
        junitXml.required.set(true)
        html.required.set(htmlQualityReports)
    }
    testLogging {
        events("passed", "skipped", "failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

tasks.withType<JacocoReport>().configureEach {
    reports {
        xml.required.set(true)
        html.required.set(htmlQualityReports)
    }
}

tasks.named<JacocoReport>("jacocoTestReport") {
    dependsOn(tasks.named("test"))
}

tasks.named("check") {
    // DocLint 是质量判定；coverage report 仅供显式浏览，不参与 check 判定。
    dependsOn("javadoc", "spotlessCheck")
}
