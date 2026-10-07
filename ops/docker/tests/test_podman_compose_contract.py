"""Podman 使用者验证模板的静态安全合同。"""
import json
import pathlib
import subprocess
import unittest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]

class PodmanComposeContractTest(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / "ops/podman/compose.validation.yaml").read_text())

    def test_local_only_ports_and_separate_database(self):
        services = self.config["services"]
        self.assertEqual(set(services), {"postgres", "api"})
        self.assertEqual(services["postgres"]["ports"], ["127.0.0.1:15432:5432"])
        self.assertEqual(services["api"]["ports"], ["127.0.0.1:18080:8080"])
        self.assertIn("pgdata:/var/lib/postgresql/data", services["postgres"]["volumes"])
        for service in services.values():
            self.assertNotIn("build", service)
            self.assertEqual(service["pull_policy"], "never")
            self.assertIn("lexiflow.installation", service["labels"])

    def test_published_network_has_route_without_changing_loopback_ports(self):
        self.assertTrue(self.config['networks']['private']['internal'])
        self.assertFalse(self.config['networks']['published'].get('internal', False))
        for service in ['api', 'postgres']:
            self.assertEqual(self.config['services'][service]['networks'], ['private', 'published'])

    def test_file_secrets_not_plaintext_or_environment_sources(self):
        self.assertEqual(self.config["secrets"], {
            "postgres-password": {"file": "./secrets/postgres-password"},
            "app-password": {"file": "./secrets/app-password"},
        })
        self.assertNotIn("POSTGRES_PASSWORD", self.config["services"]["postgres"]["environment"])

    def test_template_matches_shared_managed_deployment_compose(self):
        command = (
            "import {renderCompose} from './ops/podman/unified-local.mjs';"
            "console.log(renderCompose(process.cwd(),"
            "{apiPort:18080,dbPort:15432,external:false,dbUser:'lexiflow'}));"
        )
        output = subprocess.run(
            ["node", "--input-type=module", "-e", command], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=10,
        )
        self.assertEqual(self.config, json.loads(output.stdout))
        self.assertNotIn("initialize", self.config["services"])
        self.assertEqual(self.config["services"]["api"]["command"], ["api"])
        self.assertEqual(self.config["services"]["api"]["depends_on"], {
            "postgres": {"condition": "service_healthy"},
        })

if __name__ == "__main__":
    unittest.main()
