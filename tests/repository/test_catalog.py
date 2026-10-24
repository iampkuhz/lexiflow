"""通过真实 Delivery Gate loader 验证 Catalog 来源、范围和失败关闭。"""

from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

import yaml

from scripts.acceptance.evidence.acceptance_record_store import RecordError
from scripts.acceptance.task_definition.task_acceptance_requirements import load_task_requirements
from scripts.acceptance.task_definition.task_acceptance_scope import derive_task_subject

ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "LF-TSK-TEST-0001"


def _minimal_catalog():
    task = {
        "id": TASK_ID,
        "task_version": 1,
        "change_version": "1.0.0",
        "allowed_files": ["src/**"],
        "forbidden_files": ["secrets/**"],
        "required_check_ids": ["fixture.check"],
        "depends_on": [
            {
                "task_id": "LF-TSK-TEST-0002",
                "type": "hard",
                "required_task_version": 2,
                "required_change_version": "2.0.0",
                "required_result": "PASS",
            }
        ],
    }
    other = {
        **copy.deepcopy(task),
        "id": "LF-TSK-TEST-0002",
        "task_version": 2,
        "change_version": "2.0.0",
        "depends_on": [],
    }
    return {
        "workstreams": [
            {
                "id": "LF-WS-TEST",
                "epics": [
                    {
                        "id": "LF-EP-TEST-001",
                        "capabilities": [
                            {
                                "id": "LF-CP-TEST-001",
                                "seed_tasks": [task, other],
                            }
                        ],
                    }
                ],
            }
        ]
    }


class CatalogRequirementsTest(unittest.TestCase):
    """测试真实要求投影而非无消费者的字符串依赖兼容接口。"""

    def setUp(self):
        """创建最小完整 Catalog，所有读取都使用实际路径和解析器。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "planning").mkdir()
        self.catalog = _minimal_catalog()
        self.path = self.root / "planning/workstreams.yaml"
        self._write()

    def _write(self):
        self.path.write_text(yaml.safe_dump(self.catalog, sort_keys=False))

    def test_known_task_preserves_dependencies_versions_and_scope(self):
        """依赖保留精确 producer 版本，范围、必要检查和来源均可消费。"""
        value = load_task_requirements(self.root, TASK_ID)
        self.assertEqual(["src/**"], value["allowed_files"])
        self.assertEqual(
            [
                {
                    "task_id": "LF-TSK-TEST-0002",
                    "type": "hard",
                    "required_task_version": 2,
                    "required_change_version": "2.0.0",
                    "required_result": "PASS",
                }
            ],
            value["dependencies"],
        )
        self.assertEqual(["src/**"], value["allowed_files"])
        self.assertEqual(["secrets/**"], value["forbidden_files"])
        self.assertEqual(["fixture.check"], value["required_check_ids"])
        self.assertEqual("planning/workstreams.yaml", value["source"]["locator"])
        self.assertEqual(64, len(value["source"]["sha256"]))

    def test_non_pass_dependency_source_is_rejected(self):
        """硬依赖的精确投影与来源均只接受 PASS 要求。"""
        task = self.catalog["workstreams"][0]["epics"][0]["capabilities"][0][
            "seed_tasks"
        ][0]
        for result in ("FAIL", "BLOCKED", "SKIPPED"):
            with self.subTest(result=result):
                task["depends_on"][0]["required_result"] = result
                self._write()
                with self.assertRaisesRegex(RecordError, "task-dependencies-invalid"):
                    load_task_requirements(self.root, TASK_ID)

    def test_invalid_topology_and_duplicate_ids_fail_closed(self):
        """无效拓扑或重复身份不能降级为无依赖。"""
        for bad in ({"workstreams": "not-a-list"}, _minimal_catalog()):
            with self.subTest(catalog=bad):
                if isinstance(bad["workstreams"], list):
                    tasks = bad["workstreams"][0]["epics"][0]["capabilities"][0][
                        "seed_tasks"
                    ]
                    tasks.append(copy.deepcopy(tasks[0]))
                self.catalog = bad
                self._write()
                with self.assertRaisesRegex(RecordError, "task-catalog-invalid"):
                    load_task_requirements(self.root, TASK_ID)

    def test_unknown_task_has_no_source(self):
        """缺失目标不可生成指纹或空依赖要求。"""
        with self.assertRaisesRegex(RecordError, "task-not-found"):
            load_task_requirements(self.root, "LF-TSK-NOPE-0001")

    def test_missing_and_malformed_catalog_fail_closed(self):
        """缺文件及损坏 YAML 都被当前来源读取层拒绝。"""
        self.path.unlink()
        with self.assertRaises(RecordError):
            load_task_requirements(self.root, TASK_ID)
        self.path.write_text("workstreams: [\n")
        with self.assertRaisesRegex(RecordError, "task-catalog-invalid"):
            load_task_requirements(self.root, TASK_ID)

    def test_planning_only_catalog_has_no_dispatchable_task_or_source(self):
        """纯计划 Catalog 不虚构执行 Task 或来源。"""
        self.catalog = {"program": {"catalog_mode": "planning-only"}, "workstreams": []}
        self._write()
        with self.assertRaisesRegex(RecordError, "task-not-found"):
            load_task_requirements(self.root, TASK_ID)

    def test_source_binds_task_scope_but_not_unrelated_task(self):
        """真实 v2 投影绑定目标范围，不把另一 Task 的修改当目标漂移。"""
        initial = load_task_requirements(self.root, TASK_ID)["source"]
        tasks = self.catalog["workstreams"][0]["epics"][0]["capabilities"][0][
            "seed_tasks"
        ]
        tasks[1]["title"] = "无关 Task 说明改变"
        self._write()
        self.assertEqual(initial, load_task_requirements(self.root, TASK_ID)["source"])
        tasks[0]["allowed_files"].append("another/**")
        self._write()
        self.assertNotEqual(
            initial, load_task_requirements(self.root, TASK_ID)["source"]
        )

    def test_explicit_owner_override_is_not_lost(self):
        """不同于 WorkStream 的真实 Task owner 不被默认继承覆盖。"""
        task = self.catalog["workstreams"][0]["epics"][0]["capabilities"][0][
            "seed_tasks"
        ][0]
        task["owner"] = "LF-WS-ARCH"
        self._write()
        inherited_source = load_task_requirements(self.root, TASK_ID)["source"]
        task["owner"] = "LF-WS-TEST"
        self._write()
        self.assertNotEqual(
            inherited_source, load_task_requirements(self.root, TASK_ID)["source"]
        )

    def test_scalar_scope_and_missing_typed_dependencies_are_rejected(self):
        """最新 Catalog 只接收数组 scope 与显式 typed depends_on，无旧标量回退。"""
        task = self.catalog["workstreams"][0]["epics"][0]["capabilities"][0][
            "seed_tasks"
        ][0]
        task["allowed_files"] = "src/**"
        self._write()
        with self.assertRaisesRegex(RecordError, "task-scope-invalid"):
            load_task_requirements(self.root, TASK_ID)
        task["allowed_files"] = ["src/**"]
        task.pop("depends_on")
        self._write()
        with self.assertRaisesRegex(RecordError, "task-dependencies-invalid"):
            load_task_requirements(self.root, TASK_ID)

    def test_normalized_legacy_tasks_cannot_claim_outside_allowed_paths(self):
        """七项来源规范化不会把不在 allowed 集的文件纳入验收主体。"""
        for task_id in (
            "LF-TSK-LEX-1001",
            "LF-TSK-ENR-1001",
            "LF-TSK-ENR-1002",
            "LF-TSK-API-1001",
            "LF-TSK-EXT-1001",
            "LF-TSK-EXT-1002",
            "LF-TSK-OPS-1001",
        ):
            with self.subTest(task_id=task_id):
                requirements = load_task_requirements(ROOT, task_id)
                self.assertIsInstance(requirements["allowed_files"], list)
                self.assertIsInstance(requirements["forbidden_files"], list)
                with self.assertRaisesRegex(ValueError, "task-subject-empty"):
                    derive_task_subject(ROOT, requirements, ["unowned/other-writer.py"])


if __name__ == "__main__":
    unittest.main()
