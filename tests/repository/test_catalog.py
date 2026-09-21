"""Tests for scripts.repository.catalog read-only lookup."""
from __future__ import annotations
import tempfile
import unittest
import yaml
from pathlib import Path

from scripts.repository.catalog import catalog_tasks, read_task_dependencies


def _minimal_catalog():
    return {
        "workstreams": [{
            "id": "LF-WS-TEST",
            "epics": [{
                "id": "LF-EP-TEST-001",
                "capabilities": [{
                    "id": "LF-CP-TEST-001",
                    "seed_tasks": [{
                        "id": "LF-TSK-TEST-0001",
                        "dependencies": ["LF-TSK-TEST-0002"],
                    }, {
                        "id": "LF-TSK-TEST-0002",
                        "dependencies": [],
                    }],
                }],
            }],
        }],
    }


class CatalogTasksTest(unittest.TestCase):
    def test_extracts_tasks_from_valid_catalog(self):
        tasks = catalog_tasks(_minimal_catalog())
        self.assertIn("LF-TSK-TEST-0001", tasks)
        self.assertIn("LF-TSK-TEST-0002", tasks)
        self.assertEqual(tasks["LF-TSK-TEST-0001"]["owner"], "LF-WS-TEST")

    def test_rejects_invalid_catalog_structure(self):
        with self.assertRaises(ValueError):
            catalog_tasks({"workstreams": "not-a-list"})

    def test_rejects_duplicate_task_ids(self):
        catalog = {
            "workstreams": [{
                "id": "LF-WS-TEST",
                "epics": [{
                    "id": "LF-EP-TEST-001",
                    "capabilities": [{
                        "id": "LF-CP-TEST-001",
                        "seed_tasks": [
                            {"id": "LF-TSK-TEST-0001"},
                            {"id": "LF-TSK-TEST-0001"},
                        ],
                    }],
                }],
            }],
        }
        with self.assertRaises(ValueError):
            catalog_tasks(catalog)


class ReadTaskDependenciesTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self.root = Path(self._tmpdir)
        catalog = _minimal_catalog()
        (self.root / "planning").mkdir()
        (self.root / "planning" / "workstreams.yaml").write_text(
            yaml.safe_dump(catalog, sort_keys=False)
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_known_task_returns_dependencies(self):
        found, deps = read_task_dependencies(self.root, "LF-TSK-TEST-0001")
        self.assertTrue(found)
        self.assertEqual(deps, ["LF-TSK-TEST-0002"])

    def test_unknown_task_is_not_zero_dependencies(self):
        found, deps = read_task_dependencies(self.root, "LF-TSK-NOPE-0001")
        self.assertFalse(found)
        self.assertEqual(deps, [])

    def test_missing_catalog_is_not_zero_dependencies(self):
        (self.root / "planning" / "workstreams.yaml").unlink()
        found, deps = read_task_dependencies(self.root, "LF-TSK-TEST-0001")
        self.assertFalse(found)
        self.assertEqual(deps, [])


if __name__ == "__main__":
    unittest.main()
