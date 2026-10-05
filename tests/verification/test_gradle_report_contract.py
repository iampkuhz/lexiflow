"""Direct contract checks for Gradle quality determination versus browse-only reports."""
from pathlib import Path
import unittest

import yaml

from scripts.verification.release_source_bridge import FIXED_FILES


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

    def test_root_coverage_xml_stays_browse_only_and_html_is_opt_in(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        task = source.split('val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport")', 1)[1]
        task = task.split('tasks.named("check")', 1)[0]
        self.assertIn("xml.required.set(true)", task)
        self.assertIn('gradleProperty("qualityHtmlReports")', task)
        self.assertIn('dependsOn(leafProjects.map { "${it.path}:jacocoTestReport" })', task)
        check = source.split('tasks.named("check")', 1)[1]
        check = check.split('tasks.register("deliveryFull")', 1)[0]
        self.assertIn("verifyNoSkippedJavaTests", check)
        self.assertIn("leafProjects.map { \"${it.path}:check\" }", check)

    def test_quality_full_and_product_boot_jar_aliases_removed(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        self.assertNotIn('"qualityFull"', source)
        self.assertNotIn('"productBootJar"', source)

    def test_delivery_full_depends_on_actual_tasks_not_aliases(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        delivery = source.split('tasks.register("deliveryFull")', 1)[1]
        delivery = delivery.split("\n}", 1)[0]
        self.assertIn('"check"', delivery)
        self.assertIn('":adapters:postgresIntegrationTest"', delivery)
        self.assertIn('":integration-tests:runtimeSmokeTest"', delivery)
        self.assertIn('":api:bootJar"', delivery)

    def test_root_does_not_execute_git_commands(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        self.assertNotIn('"git"', source)
        self.assertNotIn("rev-parse", source)
        self.assertNotIn("--porcelain", source)

    def test_identity_resolved_via_build_logic(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        self.assertIn("BuildIdentity.resolve", source)
        self.assertIn("BuildIdentityExtension", source)

    def test_dependency_graph_collected_after_projects_evaluated(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        self.assertIn("gradle.projectsEvaluated", source)
        self.assertIn("projectDependencyGraph", source)

    def test_module_build_inputs_are_frozen_for_backend_and_release(self):
        paths = {
            "backend/product/api/build.gradle.kts",
            "backend/product/adapters/build.gradle.kts",
            "backend/product/enrichment/build.gradle.kts",
            "backend/verification/architecture/build.gradle.kts",
            "backend/verification/integration/build.gradle.kts",
        }
        self.assertTrue(paths.issubset(FIXED_FILES))
        registry = yaml.safe_load((ROOT / "harness/module-checks.yaml").read_text())
        checks = {item["check_id"]: item for item in registry["checks"]}
        for check_id in (
            "eng.backend.delivery", "eng.backend.delivery-on-change",
            "eng.release.lifecycle-runtime", "eng.release.lifecycle-runtime-on-change",
        ):
            with self.subTest(check_id=check_id):
                self.assertTrue(paths.issubset(checks[check_id]["input_paths"]))
        for suffix in ("", "-on-change"):
            check = checks["eng.verification.module-tests" + suffix]
            self.assertNotIn("java-25-temurin", check["required_environment"])
            self.assertTrue(check["transaction_reuse"])
            contract = checks["eng.backend.build-contract" + suffix]
            self.assertIn("backend", contract["input_paths"])
            self.assertIn("java-25-temurin", contract["required_environment"])
            self.assertFalse(contract["transaction_reuse"])
            self.assertEqual(contract["command"],
                             ["python3", "-m", "tests.verification.gradle_build_contract"])
        self.assertFalse((ROOT / "tests/verification/test_gradle_task_graph.py").exists())

    def test_module_build_files_exist(self):
        for module_path in [
            "backend/product/api/build.gradle.kts",
            "backend/product/adapters/build.gradle.kts",
            "backend/product/enrichment/build.gradle.kts",
            "backend/verification/architecture/build.gradle.kts",
            "backend/verification/integration/build.gradle.kts",
        ]:
            self.assertTrue(
                (ROOT / module_path).exists(),
                f"Module build file missing: {module_path}",
            )


if __name__ == "__main__":
    unittest.main()
