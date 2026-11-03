"""Podman 使用者验证模板的静态安全合同。"""

import json
import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]


class PodmanComposeContractTest(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load(
            (ROOT / "ops/podman/compose.validation.yaml").read_text()
        )

    def test_local_only_api_and_read_only_database_file(self):
        services = self.config["services"]
        self.assertEqual(set(services), {"api"})
        self.assertEqual(services["api"]["ports"], ["127.0.0.1:18081:8080"])
        self.assertEqual(
            services["api"]["volumes"],
            [
                "${LEXIFLOW_DATABASE_FILE:?LEXIFLOW_DATABASE_FILE is required}:/var/lib/lexiflow/lexicon.db:ro"
            ],
        )
        self.assertTrue(services["api"]["read_only"])
        self.assertEqual(services["api"]["cap_drop"], ["ALL"])
        self.assertEqual(services["api"]["security_opt"], ["no-new-privileges:true"])
        self.assertNotIn("volumes", self.config)
        for service in services.values():
            self.assertNotIn("build", service)
            self.assertEqual(service["pull_policy"], "never")
            self.assertIn("lexiflow.installation", service["labels"])
            self.assertEqual(service["labels"]["lexiflow.slot"], "source")
        for network in self.config["networks"].values():
            self.assertEqual(network["labels"]["lexiflow.slot"], "source")

    def test_published_network_has_route_without_changing_loopback_ports(self):
        self.assertEqual(set(self.config["networks"]), {"published"})
        self.assertFalse(self.config["networks"]["published"].get("internal", False))
        for service in ["api"]:
            self.assertEqual(
                self.config["services"][service]["networks"], ["published"]
            )

    def test_database_has_no_credentials_or_database_service(self):
        self.assertNotIn("secrets", self.config)
        self.assertEqual(
            self.config["services"]["api"]["environment"],
            {
                "SPRING_DATASOURCE_URL": "jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro"
            },
        )
        self.assertNotIn("depends_on", self.config["services"]["api"])

    def test_template_matches_shared_managed_deployment_compose(self):
        command = (
            "import {renderCompose} from './ops/podman/installation/installation-upgrade-workflow.mjs';"
            "import {deploymentSlot} from './ops/podman/deployment-slots.mjs';"
            "const source=deploymentSlot('source',process.cwd());"
            "console.log(renderCompose(process.cwd(),"
            "{apiPort:source.apiPort,databaseFile:source.directory+'/data/lexicon.db',slot:'source'}));"
        )
        output = subprocess.run(
            ["node", "--input-type=module", "-e", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(self.config, json.loads(output.stdout))
        self.assertNotIn("initialize", self.config["services"])
        self.assertEqual(self.config["services"]["api"]["command"], ["api"])
        self.assertNotIn("depends_on", self.config["services"]["api"])


if __name__ == "__main__":
    unittest.main()
