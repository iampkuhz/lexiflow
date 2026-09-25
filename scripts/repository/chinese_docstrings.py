"""通过 Pylint/Astroid 的文档节点检查中文说明，不自行解析或遍历源码文本。"""

from __future__ import annotations

import re

from astroid import nodes
from pylint.checkers import BaseChecker
from pylint.lint import PyLinter


class ChineseDocstringChecker(BaseChecker):
    """约束模块及公共接口的真实文档字符串；说明语义仍由代码审阅负责。"""

    name = "chinese-docstrings"
    msgs = {
        "C9001": (
            "%s 缺少中文文档字符串（docstring）",
            "missing-chinese-docstring",
            "非空模块及公共类、函数、方法必须提供职责说明。",
        ),
        "C9002": (
            "%s 的文档字符串缺少中文说明",
            "non-chinese-docstring",
            "普通字符串或行注释不能代替所属定义的中文文档字符串。",
        ),
    }
    options = (
        (
            "docstring-language-pattern",
            {
                "default": r"[\u3400-\u4dbf\u4e00-\u9fff]",
                "type": "regexp",
                "help": "文档说明须匹配的语言表达式；只检查存在性，不宣称语义质量。",
            },
        ),
    )

    def _check(self, node, label: str) -> None:
        doc = node.doc_node
        if doc is None or not doc.value.strip():
            self.add_message("missing-chinese-docstring", node=node, args=(label,))
        elif not re.search(self.linter.config.docstring_language_pattern, doc.value):
            self.add_message("non-chinese-docstring", node=doc, args=(label,))

    @staticmethod
    def _public(node) -> bool:
        current = node
        while isinstance(
            current, (nodes.ClassDef, nodes.FunctionDef, nodes.AsyncFunctionDef)
        ):
            if current.name.startswith("_"):
                return False
            parent = current.parent.frame()
            if isinstance(parent, (nodes.FunctionDef, nodes.AsyncFunctionDef)):
                return False
            current = parent
        return isinstance(current, nodes.Module)

    def visit_module(self, node: nodes.Module) -> None:
        """检查非空模块，空包标记不强制添加无意义说明。"""
        if node.body or node.doc_node:
            self._check(node, "模块")

    def visit_classdef(self, node: nodes.ClassDef) -> None:
        """检查公共类本身，不能拿某个方法的中文说明代替。"""
        if self._public(node):
            self._check(node, f"类 {node.name}")

    def visit_functiondef(self, node: nodes.FunctionDef) -> None:
        """检查公共函数和方法，包括测试、属性及类方法。"""
        if self._public(node):
            self._check(node, f"函数/方法 {node.name}")

    visit_asyncfunctiondef = visit_functiondef


def register(linter: PyLinter) -> None:
    """由 Pylint 插件加载器注册规则，保持标准 CLI 和诊断协议。"""
    linter.register_checker(ChineseDocstringChecker(linter))
