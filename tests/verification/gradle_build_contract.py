"""Behavior tests for the actual Gradle model and its project-boundary gate."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def _copy_backend_fixture(source: Path, target: Path) -> None:
    """Copy the complete build inputs without generated state or Git metadata."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        del directory
        return {name for name in names if name in {"build", ".gradle", ".git"}}
    shutil.copytree(source, target, ignore=ignore)


def _fixture_gradle(backend: Path, *args: str, env: dict[str, str] | None = None,
                    timeout: int = 600, label: str = "fixture-gradle") -> subprocess.CompletedProcess[str]:
    """Run fixture Gradle directly so no caller checkout or Java selector is involved."""
    environment = os.environ.copy()
    environment.update(env or {})
    from scripts.environment.java_runtime import resolve_java_home
    jdk = resolve_java_home(ROOT, environment)
    environment["JAVA_HOME"] = str(jdk)
    environment["PATH"] = os.pathsep.join([str(jdk / "bin"), environment.get("PATH", "")])
    environment["JDBC_URL"] = "jdbc:postgresql://127.0.0.1:1/lexiflow_fixture"
    environment["STARDICT_CSV"] = str(ROOT / "tmp/synthetic-stardict.csv")
    try:
        return subprocess.run([str(backend / "gradlew"), "-p", str(backend), *args],
                              cwd=backend, env=environment, text=True, capture_output=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _record_timeout(label, error, timeout)
        raise


def _record_fixture_result(name: str, result: subprocess.CompletedProcess[str], elapsed_seconds: float) -> None:
    """Persist test command evidence outside the repository's tracked files."""
    log_dir = ROOT / "tmp/quality/gradle-node-free-configuration/fixture-tests"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{name}.log").write_text(
        f"ELAPSED_SECONDS={elapsed_seconds:.3f}\n\n" + result.stdout + "\n--- STDERR ---\n" + result.stderr,
                                         encoding="utf-8")


def _record_timeout(name: str, error: subprocess.TimeoutExpired, timeout: int) -> None:
    """Persist partial subprocess output when a bounded command times out."""
    log_dir = ROOT / "tmp/quality/gradle-node-free-configuration/fixture-tests"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
    stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
    (log_dir / f"{name}.log").write_text(stdout + "\n--- STDERR ---\n" + stderr +
                                         f"\nTIMEOUT after {timeout}s: {error.cmd!r}\n", encoding="utf-8")


def _manifest_main_attribute(manifest_text: str, name: str) -> str | None:
    """Read a main Manifest attribute after unfolding RFC-style continuation lines."""
    unfolded = re.sub(r"\r?\n[ \t]", "", manifest_text)
    prefix = name + ": "
    return next((line[len(prefix):] for line in unfolded.splitlines() if line.startswith(prefix)), None)


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
      def orderAfter = { String path -> task(path).mustRunAfter.getDependencies(task(path)).collect { it.path }.sort() }
      def dependencyNames = { String path -> task(path).taskDependencies.getDependencies(task(path)).collect { it.path }.sort() }
      def graph = task(":verifyProjectDependencies").dependencyGraph.get()
      def payload = [
        deliveryFull: direct(task(":deliveryFull")), check: direct(task(":check")),
        orderAfter: [":api:test":orderAfter(":api:test"),
          ":adapters:postgresIntegrationTest":orderAfter(":adapters:postgresIntegrationTest"),
          ":integration-tests:runtimeSmokeTest":orderAfter(":integration-tests:runtimeSmokeTest"),
          ":api:bootJar":orderAfter(":api:bootJar")],
        apiTestDependencies: dependencyNames(":api:test"),
        graph: graph, identity: [baseVersion:rootProject.version.toString()],
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
        check = self.model["check"]
        self.assertEqual(len(check), 7)
        self.assertEqual(check[0], "[:adapters:check, :api:check, :architecture-tests:check, "
                                  ":enrichment:check, :integration-tests:check, :lexicon:check, "
                                  ":quality-gates:check]")
        self.assertTrue(check[1].startswith("org.gradle.api.internal.tasks.DefaultTaskReference@"))
        self.assertEqual(set(check[2:]), {
            "provider(task 'architectureTest', class org.gradle.api.DefaultTask)",
            "provider(task 'javaSourceGates', class org.gradle.api.DefaultTask)",
            "provider(task 'verifyNoSkippedJavaTests', class io.lexiflow.buildlogic.VerifyNoSkippedTestsTask)",
            "provider(task 'verifyProductLanguage', class io.lexiflow.buildlogic.VerifyProductLanguageTask)",
            "provider(task 'verifyProjectDependencies', class io.lexiflow.buildlogic.VerifyProjectDependenciesTask)",
        })

    def test_existing_static_checks_order_before_selected_delivery_work(self):
        expected = {f":{project}:{task}" for project in
                    ("adapters", "api", "enrichment", "lexicon", "architecture-tests",
                     "quality-gates", "integration-tests") for task in
                    ("spotlessCheck", "checkstyleMain", "checkstyleTest", "pmdMain", "pmdTest", "javadoc")}
        for path in (":api:test", ":adapters:postgresIntegrationTest",
                     ":integration-tests:runtimeSmokeTest", ":api:bootJar"):
            self.assertTrue(expected.issubset(set(self.model["orderAfter"][path])), path)
        # mustRunAfter is not a dependency edge: a targeted diagnostic stays targeted.
        self.assertFalse(any(name in expected for name in self.model["apiTestDependencies"]))

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

    def test_configuration_phase_exposes_base_version_without_node(self):
        identity = self.model["identity"]
        self.assertIn("baseVersion", identity)
        self.assertRegex(identity["baseVersion"], r"^\d+\.\d+\.\d+(-SNAPSHOT)?$")

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
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="fixture-", dir=ROOT / "tmp")
        cls.root = Path(cls.temp.name)
        cls.backend = cls.root / "backend"
        _copy_backend_fixture(ROOT / "backend", cls.backend)
        if (ROOT / ".gitignore").exists():
            shutil.copy2(ROOT / ".gitignore", cls.root / ".gitignore")
        for relative in (
            "ops/release", "ops/dataset", "infra/postgres/schema.sql",
            "scripts/environment/ecdict_bundle.py", "ops/podman/fetch-ecdict.sh",
            "ops/podman/source-tools.Containerfile",
        ):
            source, target = ROOT / relative, cls.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("build", ".gradle", ".git"))
            else:
                shutil.copy2(source, target)
        cls.git = shutil.which("git")
        if not cls.git:
            raise RuntimeError("git executable is required for fixture identity tests")
        subprocess.run([cls.git, "init", "-q"], cwd=cls.root, check=True)
        subprocess.run([cls.git, "add", "backend", "ops", "scripts", "infra", ".gitignore"],
                       cwd=cls.root, check=True)
        subprocess.run([cls.git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=cls.root, check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _run(self, label: str, *args: str, env: dict[str, str] | None = None,
             timeout: int = 600) -> subprocess.CompletedProcess[str]:
        started = time.perf_counter()
        result = _fixture_gradle(self.backend, *args, env=env, timeout=timeout, label=label)
        _record_fixture_result(label, result, time.perf_counter() - started)
        return result

    def test_cold_help_and_tasks_do_not_call_node_or_git(self):
        sentinels_temp = tempfile.TemporaryDirectory(prefix="sentinels-", dir=ROOT / "tmp")
        self.addCleanup(sentinels_temp.cleanup)
        sentinels = Path(sentinels_temp.name)
        calls = sentinels / "calls"
        for executable in ("node", "git"):
            path = sentinels / executable
            path.write_text(f"#!/bin/sh\necho {executable} >> '{calls}'\nexit 97\n", encoding="utf-8")
            path.chmod(0o755)
        # Keep shell utilities and the explicitly selected JDK, but expose no real Node/Git.
        from scripts.environment.java_runtime import resolve_java_home
        java_home = str(resolve_java_home(ROOT, os.environ))
        environment = {"PATH": os.pathsep.join([str(sentinels), "/bin", "/usr/bin", f"{java_home}/bin"]),
                       "JAVA_HOME": java_home}
        result = self._run("cold-help-tasks", "help", "tasks", "--no-configuration-cache",
                           "--no-daemon", env=environment)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-5000:])
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")

    def test_idea_project_model_loads_without_node_or_generated_identity(self):
        probe = self.root / "IdeaModelProbe.java"
        probe.write_text('''import java.io.File;
import org.gradle.tooling.GradleConnector;
import org.gradle.tooling.ProjectConnection;
import org.gradle.tooling.model.idea.IdeaProject;
public class IdeaModelProbe {
  public static void main(String[] args) {
    try (ProjectConnection c = GradleConnector.newConnector().forProjectDirectory(new File(args[0])).connect()) {
      IdeaProject p = c.getModel(IdeaProject.class);
      System.out.println("IDEA_MODEL=" + p.getName() + ", modules=" + p.getChildren().size());
    }
  }
}''', encoding="utf-8")
        gradle_home = Path.home() / ".gradle/wrapper/dists"
        distributions = list(gradle_home.glob("gradle-*-bin/*/gradle-*/lib/gradle-tooling-api-*.jar"))
        wrapper = (self.backend / "gradle/wrapper/gradle-wrapper.properties").read_text(encoding="utf-8")
        match = re.search(r"distributionUrl=.*?/gradle-([0-9][^/]*?)-bin\.zip", wrapper)
        expected_version = match.group(1) if match else None
        tooling = [path for path in distributions if path.name == f"gradle-tooling-api-{expected_version}.jar"]
        self.assertTrue(tooling, "Gradle wrapper distribution tooling API jar is not cached")
        gradle_lib = tooling[0].parent
        from scripts.environment.java_runtime import resolve_java_home
        compiler = resolve_java_home(ROOT, os.environ) / "bin/javac"
        self.assertTrue(compiler.is_file(), "javac from the configured JDK is required")
        classpath = os.pathsep.join([str(gradle_lib / "*"), str(gradle_lib / "plugins/*")])
        compiled = subprocess.run([str(compiler), "-cp", classpath, str(probe)], text=True,
                                  capture_output=True, timeout=60)
        self.assertEqual(compiled.returncode, 0, compiled.stderr[-3000:])
        sentinels_temp = tempfile.TemporaryDirectory(prefix="idea-sentinels-", dir=ROOT / "tmp")
        self.addCleanup(sentinels_temp.cleanup)
        sentinels = Path(sentinels_temp.name)
        calls = sentinels / "calls"
        for executable in ("node", "git"):
            path = sentinels / executable
            path.write_text(f"#!/bin/sh\necho {executable} >> '{calls}'\nexit 97\n", encoding="utf-8")
            path.chmod(0o755)
        from scripts.environment.java_runtime import resolve_java_home
        java_home = str(resolve_java_home(ROOT, os.environ))
        env = {"PATH": os.pathsep.join([str(sentinels), "/bin", "/usr/bin", f"{java_home}/bin"]),
               "JAVA_HOME": java_home}
        generated = self.backend / "product/api/build/generated/software-identity"
        self.assertFalse(generated.exists(), "model test must start without generated identity")
        started = time.perf_counter()
        try:
            result = subprocess.run([str(Path(java_home) / "bin/java"), "-cp",
                                     os.pathsep.join([str(self.root), classpath]), "IdeaModelProbe", str(self.backend)],
                                    cwd=self.root, env={**os.environ, **env}, text=True,
                                    capture_output=True, timeout=600)
        except subprocess.TimeoutExpired as error:
            _record_timeout("idea-project-model", error, 600)
            raise
        _record_fixture_result("idea-project-model", result, time.perf_counter() - started)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-5000:])
        root_name = re.search(r'rootProject\.name\s*=\s*"([^"]+)"',
                              (self.backend / "settings.gradle.kts").read_text(encoding="utf-8"))
        model = re.fullmatch(r"IDEA_MODEL=(.+), modules=(\d+)\n?", result.stdout.strip())
        self.assertIsNotNone(root_name, "fixture settings must declare the root project name")
        self.assertIsNotNone(model, result.stdout)
        self.assertEqual(model.group(1), root_name.group(1))
        self.assertGreater(int(model.group(2)), 1, "IDE model must include the backend subprojects")
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")
        self.assertFalse(generated.exists(), "IDE model must not create identity task outputs")

    def test_identity_task_fails_without_node(self):
        no_node_temp = tempfile.TemporaryDirectory(prefix="no-node-", dir=ROOT / "tmp")
        self.addCleanup(no_node_temp.cleanup)
        no_node = Path(no_node_temp.name)
        from scripts.environment.java_runtime import resolve_java_home
        java_home = str(resolve_java_home(ROOT, os.environ))
        env = {"PATH": os.pathsep.join([str(no_node), "/bin", "/usr/bin", f"{java_home}/bin"]),
               "JAVA_HOME": java_home}
        result = self._run("identity-no-node", ":api:generateSoftwareIdentity", "--no-configuration-cache",
                           "--no-daemon", "--rerun-tasks", env=env)
        self.assertNotEqual(result.returncode, 0, "identity generation must fail without Node")
        self.assertRegex(result.stdout + result.stderr, r"(?i)(node|not found|executable)")

    def test_identity_and_bootjar_are_coherent_and_cache_is_reused(self):
        marker = self.backend / "product/api/src/main/java/io/lexiflow/api/IdentityFixtureMarker.java"
        marker.write_text("package io.lexiflow.api; final class IdentityFixtureMarker {}\n", encoding="utf-8")
        subprocess.run([self.git, "add", "backend"], cwd=self.root, check=True)
        subprocess.run([self.git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "source marker"], cwd=self.root, check=True)

        # Prime output directories before the cache pair so only intended task inputs vary.
        warmup = self._run("identity-bootjar-warmup", ":api:bootJar", "--no-configuration-cache",
                           "--no-build-cache")
        self.assertEqual(warmup.returncode, 0, (warmup.stdout + warmup.stderr)[-5000:])
        command = (":api:generateSoftwareIdentity", ":api:bootJar", "--configuration-cache", "--no-build-cache")
        first = self._run("identity-bootjar-first", *command)
        self.assertEqual(first.returncode, 0, (first.stdout + first.stderr)[-5000:])
        second = self._run("identity-bootjar-second", *command)
        self.assertEqual(second.returncode, 0, (second.stdout + second.stderr)[-5000:])
        self.assertIn("Configuration cache entry reused", second.stdout)
        self._assert_identity_in_artifact("base-identity-artifact")

        output = self.backend / "product/api/build/generated/software-identity/META-INF/lexiflow-build.json"
        original = json.loads(output.read_text(encoding="utf-8"))
        generation_file = self.backend / "product/api/build/generated/data-generation/META-INF/lexiflow-generation.json"
        original_generation = json.loads(generation_file.read_text(encoding="utf-8"))
        marker.write_text("package io.lexiflow.api; final class IdentityFixtureMarker { int changed() { return 1; } }\n",
                          encoding="utf-8")
        changed = self._run("source-change-bootjar", *command)
        self.assertEqual(changed.returncode, 0, (changed.stdout + changed.stderr)[-5000:])
        self.assertIn("Configuration cache entry reused", changed.stdout)
        refreshed = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(original["baseVersion"], refreshed["baseVersion"])
        self.assertNotEqual(original["sourceSha256"], refreshed["sourceSha256"])
        self.assertNotEqual(original["softwareVersion"], refreshed["softwareVersion"])
        self.assertEqual(original_generation, json.loads(generation_file.read_text(encoding="utf-8")),
                         "unrelated application code must not change data generation identity")
        self._assert_identity_in_artifact("changed-identity-artifact")

        schema = self.root / "infra/postgres/schema.sql"
        schema.write_text(schema.read_text(encoding="utf-8") + "\n-- generation fixture comment\n", encoding="utf-8")
        sql_changed = self._run("sql-change-bootjar", *command)
        self.assertEqual(sql_changed.returncode, 0, (sql_changed.stdout + sql_changed.stderr)[-5000:])
        self.assertNotEqual(original_generation, json.loads(generation_file.read_text(encoding="utf-8")),
                            "SQL bytes must refresh generation identity even with configuration cache")
        self._assert_identity_in_artifact("sql-identity-artifact")

    def _assert_identity_in_artifact(self, label: str) -> None:
        import zipfile

        output = self.backend / "product/api/build/generated/software-identity/META-INF"
        identity = json.loads((output / "lexiflow-build.json").read_text(encoding="utf-8"))
        version_text = (output / "lexiflow-version.txt").read_text(encoding="utf-8")
        self.assertEqual(len(identity), 9)
        self.assertEqual(identity["softwareVersion"] + "\n", version_text)
        jar_path = self.backend / "product/api/build/libs" / f"api-{identity['softwareVersion']}.jar"
        self.assertTrue(jar_path.is_file(), f"missing versioned JAR {jar_path.name}")
        with zipfile.ZipFile(jar_path) as jar:
            manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
            embedded_json = json.loads(jar.read("META-INF/lexiflow-build.json"))
            embedded_version = jar.read("META-INF/lexiflow-version.txt").decode("utf-8")
            embedded_generation = json.loads(jar.read("META-INF/lexiflow-generation.json"))
        self.assertEqual(_manifest_main_attribute(manifest, "Implementation-Version"), identity["softwareVersion"])
        self.assertEqual(embedded_json, identity)
        self.assertEqual(embedded_version, version_text)
        api_generation = json.loads((self.backend / "product/api/build/generated/data-generation/META-INF/lexiflow-generation.json").read_text(encoding="utf-8"))
        adapter_generation = json.loads((self.backend / "product/adapters/build/generated/data-generation/META-INF/lexiflow-generation.json").read_text(encoding="utf-8"))
        self.assertEqual(embedded_generation, api_generation)
        self.assertEqual(embedded_generation, adapter_generation)
        self.assertEqual(set(embedded_generation), {"codeVersion"})
        self.assertRegex(embedded_generation["codeVersion"], r"^[0-9a-f]{64}$")
        node = shutil.which("node")
        self.assertIsNotNone(node, "real Node is required for source-of-truth comparison")
        started = time.perf_counter()
        canonical = subprocess.run([node, "ops/release/version.mjs"], cwd=self.root, text=True,
                                   capture_output=True, timeout=30)
        _record_fixture_result(label + "-node-source", canonical, time.perf_counter() - started)
        self.assertEqual(canonical.returncode, 0, canonical.stderr)
        self.assertEqual(identity, json.loads(canonical.stdout))


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


class GradleStaticBeforeDeliveryFailureTest(unittest.TestCase):
    """Exercise the frozen quality contract in an isolated, intentionally-invalid fixture."""

    def test_missing_javadoc_stops_delivery_before_behavior_tasks_and_rules_are_unchanged(self):
        quality_files = [path for path in (ROOT / "backend/gradle/config").rglob("*") if path.is_file()]
        quality_files.append(ROOT / "backend/gradle/build-logic/src/main/kotlin/lexiflow.java-quality.gradle.kts")
        quality_hashes = {path.relative_to(ROOT): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in quality_files}
        temporary = tempfile.TemporaryDirectory(prefix="lvo-gradle-failure-", dir=ROOT / "tmp")
        self.addCleanup(temporary.cleanup)
        fixture_root = Path(temporary.name)
        backend = fixture_root / "backend"
        _copy_backend_fixture(ROOT / "backend", backend)
        # Build scripts resolve repository inputs relative to backend's parent directory.
        for relative in ("ops/release", "ops/dataset", "infra/postgres/schema.sql",
                         "scripts/environment/ecdict_bundle.py", "ops/podman/fetch-ecdict.sh",
                         "ops/podman/source-tools.Containerfile"):
            source, target = ROOT / relative, fixture_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("build", ".gradle", ".git"))
            else:
                shutil.copy2(source, target)
        if (ROOT / ".gitignore").exists():
            shutil.copy2(ROOT / ".gitignore", fixture_root / ".gitignore")
        git = shutil.which("git")
        self.assertIsNotNone(git, "git executable is required for identity fixture")
        subprocess.run([git, "init", "-q"], cwd=fixture_root, check=True)
        subprocess.run([git, "add", "backend", "ops", "scripts", "infra", ".gitignore"],
                       cwd=fixture_root, check=True)
        subprocess.run([git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "fixture"], cwd=fixture_root, check=True)

        bad_source = backend / "product/api/src/main/java/io/lexiflow/api/MissingDocumentationFixture.java"
        bad_source.write_text("package io.lexiflow.api;\n\nfinal class MissingDocumentationFixture {}\n",
                              encoding="utf-8")
        subprocess.run([git, "add", str(bad_source.relative_to(fixture_root))], cwd=fixture_root, check=True)
        subprocess.run([git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "synthetic javadoc failure"], cwd=fixture_root, check=True)
        marker = fixture_root / "executed-delivery-task.txt"
        init = fixture_root / "mark-delivery.init.gradle"
        init.write_text(r'''
gradle.projectsEvaluated {
  if (rootProject.name == "lexiflow-backend") {
    println("LVO_MONITOR_READY")
    allprojects { project ->
      project.tasks.configureEach { task ->
        if (task instanceof org.gradle.api.tasks.testing.Test || task.name == "bootJar") task.doFirst {
          new File(System.getProperty("lexiflow.marker")).append(task.path + "\\n")
          throw new GradleException("unexpected delivery work before static failure")
        }
      }
    }
  }
}
''', encoding="utf-8")
        environment = {"JDBC_URL": "jdbc:postgresql://127.0.0.1:1/lexiflow_fixture",
                      "STARDICT_CSV": str(ROOT / "tmp/synthetic-stardict.csv"),
                      "JAVA_TOOL_OPTIONS": f"-Dlexiflow.marker={marker}"}
        started = time.perf_counter()
        result = _fixture_gradle(backend, "--no-configuration-cache", "--init-script", str(init),
                                 "deliveryFull", "--rerun-tasks", "--no-build-cache",
                                 env=environment, timeout=900)
        _record_fixture_result("lvo-javadoc-failure-before-delivery", result,
                               time.perf_counter() - started)
        self.assertNotEqual(result.returncode, 0, (result.stdout + result.stderr)[-5000:])
        self.assertIn("LVO_MONITOR_READY", result.stdout)
        self.assertIn("MissingDocumentationFixture", result.stdout + result.stderr)
        self.assertIn("MissingJavadocType", result.stdout + result.stderr)
        self.assertFalse(marker.exists(), marker.read_text(encoding="utf-8") if marker.exists() else "")

        # 测试自包含，不以 ignored 的阶段证据为测试输入；检查源码与 fixture 均未修改质量规则。
        for relative, digest in quality_hashes.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest, str(relative))
            self.assertEqual(hashlib.sha256((fixture_root / relative).read_bytes()).hexdigest(), digest, str(relative))


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
