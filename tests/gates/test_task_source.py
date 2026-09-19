import copy
import unittest

from scripts.gates.task_source import task_source_hash_from_catalog


class TaskSourceFingerprintTest(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "workstreams": [{
                "id": "LF-WS-QLT",
                "epics": [{"id": "LF-EP-QLT-001", "capabilities": [{
                    "id": "LF-CP-QLT-001",
                    "seed_tasks": [
                        {"id": "LF-TSK-QLT-0001", "task_version": 1, "change_version": "1.0.0"},
                        {"id": "LF-TSK-QLT-0002", "task_version": 1, "change_version": "1.0.0"},
                    ],
                }]}],
            }],
        }

    def test_unrelated_catalog_edit_does_not_stale_task_source(self):
        before = task_source_hash_from_catalog(self.catalog, "LF-TSK-QLT-0001")
        changed = copy.deepcopy(self.catalog)
        changed["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][1]["task_version"] = 2
        self.assertEqual(before, task_source_hash_from_catalog(changed, "LF-TSK-QLT-0001"))

    def test_subject_task_edit_stales_task_source(self):
        before = task_source_hash_from_catalog(self.catalog, "LF-TSK-QLT-0001")
        changed = copy.deepcopy(self.catalog)
        changed["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]["task_version"] = 2
        self.assertNotEqual(before, task_source_hash_from_catalog(changed, "LF-TSK-QLT-0001"))


if __name__ == "__main__":
    unittest.main()
