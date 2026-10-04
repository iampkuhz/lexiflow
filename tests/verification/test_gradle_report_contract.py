"""Direct contract checks for Gradle quality determination versus browse-only reports."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
QUALITY_PLUGIN = ROOT / "backend/gradle/build-logic/src/main/kotlin/lexiflow.java-quality.gradle.kts"
ROOT_BUILD = ROOT / "backend/build.gradle.kts"


class GradleReportContractTest(unittest.TestCase):
    def test_coverage_report_is_not_part_of_check_but_quality_html_is_opt_in(self):
        source = QUALITY_PLUGIN.read_text(encoding="utf-8")
        self.assertIn('gradleProperty("qualityHtmlReports")', source)
        self.assertGreaterEqual(source.count("html.required.set(htmlQualityReports)"), 4)
        self.assertGreaterEqual(source.count("xml.required.set(true)"), 2)
        self.assertIn('docOptions.addBooleanOption("Xdoclint:all,-missing", true)', source)
        self.assertIn('docOptions.addBooleanOption("Werror", true)', source)
        check = source.split('tasks.named("check")', 1)[1]
        self.assertIn('dependsOn("javadoc", "spotlessCheck")', check)
        self.assertNotIn("jacocoTestReport", check)
        self.assertIn('tasks.named<JacocoReport>("jacocoTestReport")', source)
        report = source.split('tasks.named<JacocoReport>("jacocoTestReport")', 1)[1]
        self.assertIn('dependsOn(tasks.named("test"))', report)

    def test_root_coverage_xml_stays_in_quality_full_and_html_is_opt_in(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        task = source.split('val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport")', 1)[1]
        task = task.split('tasks.named("check")', 1)[0]
        self.assertIn("xml.required.set(true)", task)
        self.assertIn('gradleProperty("qualityHtmlReports")', task)
        self.assertIn('dependsOn(leafProjects.map { "${it.path}:jacocoTestReport" })', task)
        quality_full = source.split('tasks.register("qualityFull")', 1)[1]
        quality_full = quality_full.split('tasks.register("deliveryFull")', 1)[0]
        self.assertIn('dependsOn("check")', quality_full)
        self.assertNotIn("jacocoRootReport", quality_full)
        check = source.split('tasks.named("check")', 1)[1]
        check = check.split('tasks.register("qualityFull")', 1)[0]
        self.assertIn("verifyNoSkippedJavaTests", check)
        self.assertIn("leafProjects.map { \"${it.path}:check\" }", check)


if __name__ == "__main__":
    unittest.main()
