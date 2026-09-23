from __future__ import annotations
import unittest
import yaml
from scripts.acceptance.records import RecordError
from scripts.acceptance.requirements import load_task_requirements
from scripts.repository.catalog import read_task_dependencies
from tests.acceptance.fixtures import AcceptanceFixture
class TestCatalogFailClosed(unittest.TestCase):
 def setUp(self):self.f=AcceptanceFixture()
 def tearDown(self):self.f.cleanup()
 def test_missing_or_unknown_task_is_not_zero_dependencies(self):
  self.assertEqual(read_task_dependencies(self.f.root,"LF-TSK-NOPE-0001"),(False,[]))
  (self.f.root/"planning/workstreams.yaml").unlink()
  self.assertEqual(read_task_dependencies(self.f.root,"LF-TSK-TEST-0001"),(False,[]))
 def test_missing_required_check_mapping_is_integration_blocked(self):
  path=self.f.root/"planning/workstreams.yaml";catalog=yaml.safe_load(path.read_text());del catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]["required_check_ids"];path.write_text(yaml.safe_dump(catalog,sort_keys=False))
  with self.assertRaisesRegex(RecordError,"task-required-checks-missing"):load_task_requirements(self.f.root,"LF-TSK-TEST-0001")
 def test_planning_only_catalog_cannot_reuse_retired_task_for_acceptance(self):
  catalog={"program":{"catalog_mode":"planning-only"},"workstreams":[]}
  (self.f.root/"planning/workstreams.yaml").write_text(yaml.safe_dump(catalog))
  with self.assertRaisesRegex(RecordError,"task-not-found"):
   load_task_requirements(self.f.root,"LF-TSK-ARCH-0008")
