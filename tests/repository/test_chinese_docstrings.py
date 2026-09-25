"""用真实 Pylint CLI 验证语言规则、公共边界和源码位置，防止字符串夹具冒充说明。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ChineseDocstringTest(unittest.TestCase):
    """隔离源码夹具并经官方插件加载路径运行规则，不 mock AST 或规则结果。"""

    def lint(self, source: str) -> tuple[int, list]:
        """运行限时 Pylint 并解析结构化诊断，保证真实插件能加载。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(source)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pylint",
                    "--rcfile",
                    str(ROOT / "harness/python-docstrings.toml"),
                    str(path),
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        self.assertEqual("", result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_english_adapter_is_rejected_with_module_and_function_locations(self):
        """模块英文说明和公共函数缺失说明分别定位，不被正文中文数据掩盖。"""
        code, findings = self.lint(
            '"""English adapter."""\nVALUE = "中文测试数据"\ndef run():\n    return VALUE\n'
        )
        self.assertNotEqual(0, code)
        self.assertEqual(
            [("C9002", 1), ("C9001", 3)],
            [(f["message-id"], f["line"]) for f in findings],
        )

    def test_public_definitions_include_test_methods_properties_and_async(self):
        """公共测试方法、属性和异步函数同样要求自身说明。"""
        source = '''"""模块职责说明。"""
class Public:
    """公共类型职责。"""
    def test_case(self):
        pass
    @property
    def value(self):
        """English value."""
        return 1
    @classmethod
    def create(cls):
        pass
    @staticmethod
    def build():
        pass
async def execute():
    pass
'''
        code, findings = self.lint(source)
        self.assertNotEqual(0, code)
        self.assertEqual(5, len(findings))
        self.assertEqual(
            {
                "Public.test_case",
                "Public.value",
                "Public.create",
                "Public.build",
                "execute",
            },
            {f["obj"] for f in findings},
        )

    def test_chinese_docs_and_private_or_local_helpers_pass(self):
        """公开说明均含中文时，私有和局部辅助定义不额外增加阻断。"""
        source = '''"""模块说明。"""
class Public:
    """公共类型说明。"""
    def run(self):
        """执行公共行为。"""
        def local():
            pass
    def _helper(self):
        pass
class _Private:
    def helper(self):
        pass
'''
        self.assertEqual((0, []), self.lint(source))

    def test_comments_and_nested_fixture_docstrings_do_not_cover_missing_docs(self):
        """中文注释和内嵌源码不是模块文档，不得造成错误通过。"""
        source = (
            '# 中文注释\nFIXTURE = \'"""中文夹具模块。"""\'\nclass Public:\n    pass\n'
        )
        code, findings = self.lint(source)
        self.assertNotEqual(0, code)
        self.assertEqual(["C9001", "C9001"], [f["message-id"] for f in findings])

    def test_empty_package_marker_passes_but_syntax_error_fails(self):
        """空包不强加模板说明，语法错误不能因无语言诊断而通过。"""
        self.assertEqual((0, []), self.lint(""))
        code, findings = self.lint("def broken(:\n")
        self.assertNotEqual(0, code)
        self.assertIn("syntax-error", [f["symbol"] for f in findings])
