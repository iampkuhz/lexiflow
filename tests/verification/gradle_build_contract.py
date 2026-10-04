"""Behavior tests for the actual Gradle model and its project-boundary gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_GRAPH = {
    ":adapters": [":lexicon"],
    ":api": [":adapters", ":enrichment", ":lexicon"],
    ":architecture-tests": [":adapters", ":api", ":enrichment", ":lexicon"],
    ":enrichment": [":lexicon"],
    ":integration-tests": [":adapters"],
    ":lexicon": [],
    ":quality-gates": [],
}


def _gradle(init_script: str, *tasks: str) -> subprocess.CompletedProcess[str]:
    """Run one real Gradle invocation with a temporary init script."""
    with tempfile.TemporaryDirectory(prefix="lexiflow-gradle-test-") as directory:
        init = Path(directory) / "inspect.gradle"
        init.write_text(init_script, encoding="utf-8")
        environment = os.environ.copy()
        # Explicit synthetic values outrank the machine-private local runtime config.
        environment["JDBC_URL"] = "jdbc:postgresql://127.0.0.1:1/lexiflow_test"
        environment["STARDICT_CSV"] = str(ROOT / "tmp" / "synthetic-stardict.csv")
        return subprocess.run(
            [sys.executable, "-m", "scripts.environment.java_exec", "backend/gradlew",
             "-p", "backend", "--no-configuration-cache", "--init-script", str(init), *tasks],
            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=600,
        )


INSPECT_INIT = r'''
import groovy.json.JsonOutput
gradle.projectsEvaluated {
  rootProject.tasks.register("inspectLexiflowModel") {
    doLast {
      def task = { String path -> rootProject.tasks.getByPath(path) }
      def direct = { t -> t.dependsOn.collect { it instanceof org.gradle.api.Task ? it.path : it.toString() }.sort() }
      def exec = { String path ->
        def t = task(path)
        [name:t.name, args:t.args ?: [], mainClass:t.mainClass.orNull,
         classpath:t.classpath.files.collect { it.absolutePath }.sort(), usesTerminalStdin:(t.standardInput.is(System.in))]
      }
      def identity = rootProject.extensions.getByName("buildIdentity")
      def graph = task(":verifyProjectDependencies").dependencyGraph.get()
      def payload = [
        deliveryFull: direct(task(":deliveryFull")), check: direct(task(":check")),
        graph: graph, identity: [json:identity.json, softwareVersion:rootProject.version.toString()],
        javaExec: [":adapters:lexiconValidate":exec(":adapters:lexiconValidate"),
          ":adapters:lexiconPublish":exec(":adapters:lexiconPublish"),
          ":adapters:lexiconBasicReport":exec(":adapters:lexiconBasicReport"),
          ":adapters:lexiconPrewarmReport":exec(":adapters:lexiconPrewarmReport"),
          ":adapters:lexiconRebuild":exec(":adapters:lexiconRebuild"),
          ":adapters:postgresInit":exec(":adapters:postgresInit")],
        aliases: [qualityFull:rootProject.tasks.findByName("qualityFull") != null,
          productBootJar:rootProject.tasks.findByName("productBootJar") != null]
      ]
      println("LEXIFLOW_MODEL_JSON=" + JsonOutput.toJson(payload))
    }
  }
}
'''

VIOLATION_INIT = r'''
gradle.afterProject { p, state ->
  if (p.path == ":lexicon") {
    p.dependencies.add("implementation", rootProject.project(":enrichment"))
  }
}
'''


class GradleTaskGraphTest(unittest.TestCase):
    """Inspect configuration once, then assert behavior from emitted model data."""

    @classmethod
    def setUpClass(cls) -> None:
        result = _gradle(INSPECT_INIT, "inspectLexiflowModel")
        if result.returncode:
            raise RuntimeError(f"Gradle model inspection failed ({result.returncode}): {result.stderr[-5000:]}")
        marker = next((line.partition("=")[2] for line in result.stdout.splitlines()
                       if line.startswith("LEXIFLOW_MODEL_JSON=")), None)
        if marker is None:
            raise RuntimeError("Gradle inspect task did not emit its JSON payload")
        cls.model = json.loads(marker)

    def test_delivery_aggregator_has_exact_direct_dependencies(self):
        self.assertEqual(self.model["deliveryFull"], [":adapters:postgresIntegrationTest",
                         ":api:bootJar", ":integration-tests:runtimeSmokeTest", "check"])

    def test_complete_nonempty_dependency_graph_matches_contract(self):
        graph = self.model["graph"]
        self.assertEqual(set(graph), set(EXPECTED_GRAPH))
        self.assertTrue(all(isinstance(v, list) for v in graph.values()))
        self.assertTrue(any(graph.values()), "dependency graph must be populated")
        self.assertEqual(graph, EXPECTED_GRAPH)

    def test_coverage_report_is_not_a_check_dependency_and_aliases_are_absent(self):
        check = self.model["check"]
        self.assertFalse(any("jacoco" in dep.lower() or "report" in dep.lower() for dep in check))
        self.assertEqual(self.model["aliases"], {"qualityFull": False, "productBootJar": False})

    def test_consumer_receives_complete_identity(self):
        identity = self.model["identity"]
        value = json.loads(identity["json"])
        self.assertEqual(set(value), {"schemaVersion", "baseVersion", "softwareVersion",
                                     "chromeVersion", "sourceCommit", "sourceSha256",
                                     "buildId", "dirty", "channel"})
        self.assertEqual(value["softwareVersion"], identity["softwareVersion"])

    def test_javaexec_entrypoints_preserve_names_arguments_and_launch_contract(self):
        tasks = self.model["javaExec"]
        self.assertEqual(set(tasks), {":adapters:lexiconValidate", ":adapters:lexiconPublish",
                                      ":adapters:lexiconRebuild", ":adapters:postgresInit",
                                      ":adapters:lexiconBasicReport", ":adapters:lexiconPrewarmReport"})
        self.assertEqual(tasks[":adapters:lexiconValidate"]["args"][0], "validate")
        self.assertEqual(tasks[":adapters:lexiconPublish"]["args"][0], "publish")
        self.assertEqual(tasks[":adapters:lexiconRebuild"]["mainClass"],
                         "io.lexiflow.lexicon.platform.importer.LexiconRebuildMain")
        self.assertTrue(tasks[":adapters:lexiconRebuild"]["usesTerminalStdin"])
        source = str(ROOT / "tmp" / "synthetic-stardict.csv")
        jdbc = "jdbc:postgresql://127.0.0.1:1/lexiflow_test"
        schema = str(ROOT / "infra/postgres/schema.sql")
        for name, action in (("lexiconValidate", "validate"),
                             ("lexiconBasicReport", "basic-report"),
                             ("lexiconPrewarmReport", "prewarm-report")):
            self.assertEqual(tasks[f":adapters:{name}"]["args"], [action, "--input", source])
        self.assertEqual(tasks[":adapters:lexiconPublish"]["args"],
                         ["publish", "--input", source, "--database-url", jdbc,
                          "--batch-source-id", "ecdict-stardict", "--batch-license-id", "MIT"])
        self.assertEqual(tasks[":adapters:postgresInit"]["args"], [jdbc, schema])
        self.assertEqual(tasks[":adapters:lexiconRebuild"]["args"], [jdbc, schema, source])
        for task in tasks.values():
            self.assertTrue(task["classpath"], f"{task['name']} has no assembled classpath")


class GradleIdentityResourceTest(unittest.TestCase):
    def test_identity_generation_supports_configuration_cache_reuse(self):
        command = [sys.executable, "-m", "scripts.environment.java_exec", "backend/gradlew",
                   "-p", "backend", ":api:generateSoftwareIdentity", "--configuration-cache",
                   "--rerun-tasks", "--no-build-cache"]
        for attempt in range(2):
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600)
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-5000:])
            if attempt == 1:
                self.assertIn("Configuration cache entry reused", result.stdout)
        output = ROOT / "backend/product/api/build/generated/software-identity/META-INF"
        identity = json.loads((output / "lexiflow-build.json").read_text())
        self.assertEqual(identity["softwareVersion"] + "\n",
                         (output / "lexiflow-version.txt").read_text())
        self.assertEqual(len(identity), 9)


class GradleDependencyViolationTest(unittest.TestCase):
    def test_injected_reverse_edge_is_rejected_by_real_verification_task(self):
        result = _gradle(VIOLATION_INIT, "verifyProjectDependencies")
        self.assertNotEqual(result.returncode, 0, result.stdout[-3000:])
        self.assertIn("Forbidden project dependency", result.stdout + result.stderr)

    def test_invalid_version_release_and_delivery_diagnostics_are_rejected(self):
        cases = [
            ("-Pversion=9.9.9", "help", "不接受 -Pversion"),
            ("-Prelease=invalid", "help", "release 参数必须为 true 或 false"),
            ("check", "--dry-run", "Java delivery must execute all required tasks"),
            ("check", "-x", "verifyProjectDependencies", "Java delivery must execute all required tasks"),
        ]
        for *arguments, reason in cases:
            with self.subTest(arguments=arguments):
                result = _gradle("", *arguments)
                self.assertNotEqual(result.returncode, 0, result.stdout[-2000:])
                self.assertIn(reason, result.stdout + result.stderr)


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    complete = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
    print(json.dumps({
        "status": "PASS" if complete else "FAIL",
        "checks_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "reason": "" if complete else "gradle-build-contract-incomplete",
    }, sort_keys=True))
    raise SystemExit(0 if complete else 1)
