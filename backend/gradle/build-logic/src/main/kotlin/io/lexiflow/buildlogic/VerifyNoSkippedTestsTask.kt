package io.lexiflow.buildlogic

import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.OutputFile
import org.gradle.api.tasks.TaskAction
import org.gradle.work.DisableCachingByDefault
import java.io.File
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element

/** 验证所有包含测试源码的 Java 项目都生成结果，且没有跳过或中止的测试。 */
@DisableCachingByDefault(because = "验证当前测试 XML，不复用历史执行结果")
abstract class VerifyNoSkippedTestsTask : DefaultTask() {

    /** 每项依次编码 project path、测试结果目录和测试源码目录。 */
    @get:Input
    abstract val testEntries: ListProperty<String>

    /** 保存当前验证摘要，失败时移除历史通过记录。 */
    @get:OutputFile
    abstract val resultFile: RegularFileProperty

    /** 扫描 Gradle JUnit XML 并在结果不完整或含跳过项时失败。 */
    @TaskAction
    fun verify() {
        val output = resultFile.get().asFile
        if (output.exists() && !output.delete()) {
            throw GradleException("Cannot remove stale Java test validation: $output")
        }
        var totalTests = 0
        var totalSkipped = 0
        var totalFailures = 0
        var totalErrors = 0
        val missing = mutableListOf<String>()
        testEntries.get().forEach { encoded ->
            val parts = encoded.split('\u0000')
            if (parts.size != 3) {
                throw GradleException("Invalid Java test result binding")
            }
            val projectPath = parts[0]
            val resultDirectory = File(parts[1])
            val sourceDirectory = File(parts[2])
            val hasTests = sourceDirectory.exists() &&
                sourceDirectory.walkTopDown().any { it.isFile && it.extension == "java" }
            val reports = if (resultDirectory.exists()) {
                resultDirectory.walkTopDown()
                    .filter { it.isFile && it.name.startsWith("TEST-") && it.extension == "xml" }
                    .toList()
            } else {
                emptyList()
            }
            if (hasTests && reports.isEmpty()) {
                missing.add(projectPath)
            }
            reports.forEach { report ->
                val suite = parseSuite(report)
                val count = attribute(suite, "tests", report)
                val cases = suite.getElementsByTagName("testcase").length
                if (count != cases) {
                    throw GradleException("Java test count differs from testcase elements: $report")
                }
                totalTests += count
                totalSkipped += maxOf(attribute(suite, "skipped", report), suite.getElementsByTagName("skipped").length)
                totalFailures += maxOf(attribute(suite, "failures", report), suite.getElementsByTagName("failure").length)
                totalErrors += maxOf(attribute(suite, "errors", report), suite.getElementsByTagName("error").length)
                if (hasTests && count == 0) {
                    throw GradleException("No Java tests were executed for: $projectPath")
                }
            }
        }
        if (missing.isNotEmpty()) {
            throw GradleException("Missing Java test result XML for: ${missing.joinToString()}")
        }
        if (totalTests == 0) {
            throw GradleException("No Java tests were executed")
        }
        if (totalSkipped > 0) {
            throw GradleException("Found $totalSkipped skipped or aborted Java test(s)")
        }
        if (totalFailures > 0 || totalErrors > 0) {
            throw GradleException("Found $totalFailures failed and $totalErrors erroneous Java test(s)")
        }
        output.parentFile.mkdirs()
        output.writeText("PASS tests=$totalTests skipped=0 failures=0 errors=0\n", Charsets.UTF_8)
    }

    private fun attribute(suite: Element, name: String, report: File): Int {
        val value = suite.getAttribute(name)
        return value.toIntOrNull()?.takeIf { it >= 0 }
            ?: throw GradleException("Invalid or missing $name in Java test result: $report")
    }

    private fun parseSuite(report: File): Element {
        try {
            val factory = DocumentBuilderFactory.newInstance()
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "")
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "")
            factory.isXIncludeAware = false
            factory.isExpandEntityReferences = false
            val suite = factory.newDocumentBuilder().parse(report).documentElement
            if (suite.tagName != "testsuite") {
                throw GradleException("Unexpected Java test result root: $report")
            }
            return suite
        } catch (exception: Exception) {
            throw GradleException("Cannot validate Java test result XML: $report", exception)
        }
    }
}
