"""Direct contract checks for Gradle quality determination versus browse-only reports."""

from pathlib import Path
import unittest
import tomllib

import yaml
from scripts.verification.planning.check_definitions import load_declarations
from scripts.verification.planning.source_file_snapshot import snapshot_inputs

from scripts.checks.release.source.frozen_source_fixture import FIXED_FILES


ROOT = Path(__file__).resolve().parents[2]
QUALITY_PLUGIN = (
    ROOT / "backend/gradle/build-logic/src/main/kotlin/lexiflow.java-quality.gradle.kts"
)
ROOT_BUILD = ROOT / "backend/build.gradle.kts"


class GradleReportContractTest(unittest.TestCase):
    def test_coverage_report_is_not_part_of_check_but_quality_html_is_opt_in(self):
        source = QUALITY_PLUGIN.read_text(encoding="utf-8")
        self.assertIn('gradleProperty("qualityHtmlReports")', source)
        self.assertGreaterEqual(
            source.count("html.required.set(htmlQualityReports)"), 4
        )
        self.assertGreaterEqual(source.count("xml.required.set(true)"), 2)
        self.assertIn(
            'docOptions.addBooleanOption("Xdoclint:all,-missing", true)', source
        )
        self.assertIn('docOptions.addBooleanOption("Werror", true)', source)
        check = source.split('tasks.named("check")', 1)[1]
        self.assertIn('dependsOn("javadoc", "spotlessCheck")', check)
        self.assertNotIn("jacocoTestReport", check)
        self.assertIn('tasks.named<JacocoReport>("jacocoTestReport")', source)
        report = source.split('tasks.named<JacocoReport>("jacocoTestReport")', 1)[1]
        self.assertIn('dependsOn(tasks.named("test"))', report)

    def test_root_coverage_xml_stays_browse_only_and_html_is_opt_in(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        task = source.split(
            'val jacocoRootReport = tasks.register<JacocoReport>("jacocoRootReport")', 1
        )[1]
        task = task.split('tasks.named("check")', 1)[0]
        self.assertIn("xml.required.set(true)", task)
        self.assertIn('gradleProperty("qualityHtmlReports")', task)
        self.assertIn(
            'dependsOn(leafProjects.map { "${it.path}:jacocoTestReport" })', task
        )
        check = source.split('tasks.named("check")', 1)[1]
        check = check.split('tasks.register("deliveryFull")', 1)[0]
        self.assertIn("verifyNoSkippedJavaTests", check)
        self.assertIn('leafProjects.map { "${it.path}:check" }', check)

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
        self.assertIn("BuildIdentity.readBaseVersion", source)
        self.assertIn("version = baseVersion", source)
        self.assertNotIn("BuildIdentityExtension", source)
        self.assertNotIn('"buildIdentity"', source)
        self.assertNotIn("BuildIdentity.resolve", source)

    def test_version_catalog_contains_only_consumed_tool_versions(self):
        catalog = tomllib.loads(
            (ROOT / "backend/gradle/libs.versions.toml").read_text(encoding="utf-8")
        )
        for key in ("checkstyle", "google-java-format", "jacoco", "pmd"):
            self.assertNotIn(key, catalog["versions"])
        self.assertNotIn("spotless", catalog["plugins"])
        self.assertEqual(
            catalog["libraries"]["junit-platform-launcher"]["version"], {"ref": "junit"}
        )
        self.assertIn("spotless", catalog["versions"])

    def test_runtime_switch_defaults_remain_at_injection_points(self):
        config = yaml.safe_load(
            (
                ROOT / "backend/product/api/src/main/resources/application.yaml"
            ).read_text(encoding="utf-8")
        )
        for section in ("segment-analysis", "caption-debug"):
            self.assertNotIn("enabled", config.get("lexiflow", {}).get(section, {}))
        api = (
            ROOT
            / "backend/product/api/src/main/java/io/lexiflow/api/ApiApplication.java"
        ).read_text(encoding="utf-8")
        debug = (
            ROOT
            / "backend/product/api/src/main/java/io/lexiflow/api/captiondebug/CaptionDebugController.java"
        ).read_text(encoding="utf-8")
        self.assertIn("${lexiflow.segment-analysis.enabled:false}", api)
        self.assertIn("${lexiflow.caption-debug.enabled:true}", debug)

    def test_configuration_phase_does_not_call_node(self):
        source = ROOT_BUILD.read_text(encoding="utf-8")
        self.assertNotIn('"node"', source)
        self.assertNotIn("providers.exec", source)
        api_source = (ROOT / "backend/product/api/build.gradle.kts").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"node"', api_source)
        self.assertNotIn("providers.exec", api_source)
        self.assertNotIn("doFirst", api_source)

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
        registry = load_declarations(ROOT)
        checks = {item["check_id"]: item for item in registry["checks"]}
        for check_id in (
            "eng.backend.delivery",
            "eng.release.lifecycle-runtime",
        ):
            with self.subTest(check_id=check_id):
                files, missing = snapshot_inputs(ROOT, checks[check_id])
                self.assertEqual(missing, [])
                frozen_paths = {item["locator"] for item in files}
                self.assertTrue(
                    paths.issubset(frozen_paths), sorted(paths - frozen_paths)
                )
        for suffix in ("",):
            check = checks["eng.verification.module-tests" + suffix]
            self.assertNotIn("java-25-temurin", check["required_environment"])
            self.assertTrue(check["transaction_reuse"])
            contract = checks["eng.backend.build-contract" + suffix]
            self.assertIn("backend", contract["input_paths"])
            self.assertIn("java-25-temurin", contract["required_environment"])
            self.assertFalse(contract["transaction_reuse"])
            self.assertEqual(
                contract["command"],
                ["python3", "-m", "tests.verification.gradle_build_contract"],
            )
        self.assertFalse(
            (ROOT / "tests/verification/test_gradle_task_graph.py").exists()
        )

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
