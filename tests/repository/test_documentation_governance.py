"""文档链接、安全、围栏与固定规范的直接回归。"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.checks.repository.documentation import check_documentation as docs_check


class DocumentationTests(unittest.TestCase):
    def test_product_roadmap_has_current_phases_and_future_backlog(self):
        root = Path(__file__).resolve().parents[2]
        entry = root / "docs/roadmap/master-plan.md"
        content = entry.read_text()
        lifecycle = (root / "docs/README.md").read_text()
        phase = (root / "docs/roadmap/master-plan/phase2.md").read_text()
        self.assertIn("roadmap/master-plan.md", lifecycle)
        for name in ("phase2", "future"):
            relative = f"docs/roadmap/master-plan/{name}.md"
            self.assertTrue((root / relative).is_file())
            self.assertIn(f"master-plan/{name}.md", content)
        for name in ("design", "tasks", "status"):
            relative = f"docs/roadmap/master-plan/phase2/{name}.md"
            self.assertTrue((root / relative).is_file())
            self.assertIn(f"phase2/{name}.md", phase)
        for obsolete in (
            "foundation.md",
            "foundation/experience.md",
            "foundation/acceptance.md",
            "status.md",
        ):
            self.assertFalse((root / "docs/roadmap/master-plan" / obsolete).exists())
        self.assertIn("master-plan/phase2/status.md", content)
        for obsolete in ("phase-1-status.md", "phase-2-status.md"):
            self.assertFalse((root / "docs/roadmap" / obsolete).exists())
        self.assertIn("永不调用大模型", content)
        self.assertIn("事后分析", content)
        self.assertIn("方向确认不等于功能验收", content)

    def test_build_session_artifacts_are_ignored_but_product_sources_are_not(self):
        root = Path(__file__).resolve().parents[2]
        ignored = "backend/gradle/build-logic/.kotlin/sessions/compiler.salive"
        source = "backend/product/adapters/src/main/java/io/lexiflow/lexicon/platform/importer/RebuildProgress.java"
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--stdin"],
            cwd=root,
            input=f"{ignored}\n{source}\n",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual([ignored], result.stdout.splitlines())
        self.assertEqual("", result.stderr)

    def test_documentation_rejects_candidate_solution_headings(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            document = root / "docs/design.md"
            document.parent.mkdir()
            document.write_text("# 1. 方案比较\n")
            self.assertEqual(
                len(docs_check.check_selected_solution_only(docs_check.DocIndex(root))),
                1,
            )
            document.write_text("# 1. 已确定的合同\n| 方案 | 说明 |\n| --- | --- |\n")
            self.assertEqual(
                len(docs_check.check_selected_solution_only(docs_check.DocIndex(root))),
                1,
            )
            document.write_text("# 1. 已确定的合同\n")
            self.assertEqual(
                docs_check.check_selected_solution_only(docs_check.DocIndex(root)),
                [],
            )

    def test_latest_only_rejects_history_and_dated_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs/reviews/history").mkdir(parents=True)
            (root / "docs/reviews/state-20260918.md").write_text("# 状态\n")
            (root / "docs/reviews/state-2026-09-18.md").write_text("# 状态\n")
            (root / "docs/phase-1.md").write_text("# 当前设计\n")
            self.assertEqual(
                len(docs_check.check_latest_only(docs_check.DocIndex(root))), 3
            )
            for file in (root / "docs/reviews").glob("state-*.md"):
                file.unlink()
            (root / "docs/reviews/history").rmdir()
            (root / "tmp/history").mkdir(parents=True)
            (root / "tmp/history/receipt-20260918.json").write_text("{}")
            self.assertEqual(
                docs_check.check_latest_only(docs_check.DocIndex(root)), []
            )

    def test_version_comparison_requires_comparison_path_and_title(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            ordinary = root / "docs/architecture.md"
            ordinary.write_text("# 1. 架构\n待审核目标\n")
            self.assertEqual(
                len(docs_check.check_version_comparisons(docs_check.DocIndex(root))),
                1,
            )

    def test_version_comparison_blocks_chinese_context_in_headings_tables_and_prose(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            doc = root / "docs/status.md"
            for content in (
                "# 1. 改后设计\n",
                "| 阶段 | 当前版本 |\n",
                "现在采用目标版本配置\n",
            ):
                doc.write_text(content)
                self.assertEqual(
                    len(
                        docs_check.check_version_comparisons(docs_check.DocIndex(root))
                    ),
                    1,
                )

    def test_version_phrase_matching_avoids_embedded_substrings_but_still_blocks_comparisons(
        self,
    ):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            status = root / "docs/status.md"
            for verb in (
                "修改",
                "更改",
                "整改",
                "变改",
                "涂改",
                "篡改",
                "批改",
                "删改",
            ):
                for when in ("前", "后"):
                    status.write_text(f"# 1. 状态\n{verb}{when}运行验证\n")
                    self.assertEqual(
                        docs_check.check_version_comparisons(docs_check.DocIndex(root)),
                        [],
                    )
            status.write_text("# 1. 状态\n改后版本使用新流程\n")
            self.assertEqual(
                len(docs_check.check_version_comparisons(docs_check.DocIndex(root))),
                1,
            )

    def test_run_reuses_each_markdown_parse_within_one_invocation(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            (root / "harness").mkdir()
            target = root / "docs/target.md"
            target.write_text("# 1. target\n")
            source = root / "docs/source.md"
            source.write_text("[a](target.md#1-target) [b](target.md#1-target)\n")
            (root / "AGENTS.md").write_text("ok")
            (root / "README.md").write_text("ok")
            (root / "harness/README.md").write_text("ok")
            original = docs_check.parse
            calls = []

            def counted(text):
                calls.append(text)
                return original(text)

            with patch.object(docs_check, "parse", side_effect=counted):
                result = docs_check.run(root)
            self.assertEqual(result["issues"], [])
            # 两个正文只在 run 开始时解析；重复锚点链接复用 target 索引。
            self.assertEqual(len(calls), 5)

    def test_fixed_document_scope_cannot_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "openspec").mkdir()
            document = root / "docs/a.md"
            document.write_text("# 无编号\n候选方案\n")
            (root / "openspec/spec.md").write_text("# 1. 设计\n当前版本\n")
            (root / "docs/history").mkdir()
            index = docs_check.DocIndex(root)
            self.assertTrue(docs_check.check_heading_numbering(index, document))
            self.assertTrue(docs_check.check_selected_solution_only(index))
            self.assertTrue(docs_check.check_latest_only(index))
            self.assertTrue(docs_check.check_version_comparisons(index))

    def test_one_index_lazily_parses_cross_scope_fragment_once(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "openspec").mkdir()
            target = root / "openspec/target.md"
            target.write_text("# 外部目标\n")
            document = root / "docs/a.md"
            document.write_text(
                "[a](../openspec/target.md#外部目标) [b](../openspec/target.md#外部目标)\n"
            )
            index = docs_check.DocIndex(root)
            with patch.object(docs_check, "parse", wraps=docs_check.parse) as parse:
                self.assertEqual([], docs_check.check_file(index, document)[0])
                self.assertEqual(2, parse.call_count)

    def test_root_budget_is_fixed_bytes_and_harness_markdown_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "harness").mkdir()
            (root / "README.md").write_text("readme")
            instruction = root / "AGENTS.md"
            instruction.write_text("a" * 4000)
            rule = root / "harness/rules.md"
            rule.write_text("# 1. 规则\n")
            self.assertEqual([], docs_check.run(root)["issues"])
            instruction.write_text("a" * 3999 + "中")
            self.assertIn("AGENTS.md 超出读取预算", docs_check.run(root)["issues"])
            instruction.write_text("ok")
            rule.write_text("[缺失](missing.md)\n")
            self.assertTrue(
                any(
                    "harness/rules.md" in issue
                    for issue in docs_check.run(root)["issues"]
                )
            )

    def test_local_link_escape_and_private_runtime_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            outside = Path(directory) / "outside.md"
            outside.write_text("# 外部\n")
            (root / "linked.md").symlink_to(outside)
            (root / "tmp").mkdir()
            (root / "tmp/report.md").write_text("report")
            document = root / "read.md"
            document.write_text(
                "[直接越界](../outside.md)\n[链接越界](linked.md)\n[私有运行](tmp/report.md)\n"
            )
            issues = docs_check.check_file(docs_check.DocIndex(root), document)[0]
            self.assertEqual(2, sum("仓库外" in issue for issue in issues))
            self.assertEqual(1, sum("不随仓库" in issue for issue in issues))

    def test_all_fixed_generated_extensions_and_tracked_artifacts_are_rejected(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "harness").mkdir()
            (root / ".git").mkdir()
            (root / "AGENTS.md").write_text("ok")
            (root / "README.md").write_text("ok")
            document = root / "docs/a.md"
            for extension in (".puml", ".svg", ".png"):
                (root / "docs" / ("diagram" + extension)).write_text("generated")
            document.write_text(
                "\n".join(
                    f"[图](diagram{extension})"
                    for extension in (".puml", ".svg", ".png")
                )
            )
            with patch.object(
                docs_check.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, b"docs/tracked.SVG\0", b""
                ),
            ):
                issues = docs_check.run(root)["issues"]
            self.assertEqual(3, sum("正文依赖生成图" in issue for issue in issues))
            self.assertIn("生成图文件仍被跟踪：docs/tracked.SVG", issues)

    def test_latest_docs_run_clean(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(docs_check.run(root)["issues"], [])

    def test_docs_headings_require_hierarchical_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "doc.md"
            file.write_text("# 1. 总览\n## 1.1. 细节\n```text\n# 不检查代码\n```\n")
            self.assertEqual(
                docs_check.check_heading_numbering(
                    docs_check.DocIndex(file.parent), file
                ),
                [],
            )
            file.write_text("# 总览\n## 1.1. 细节\n")
            self.assertEqual(
                len(
                    docs_check.check_heading_numbering(
                        docs_check.DocIndex(file.parent), file
                    )
                ),
                1,
            )

    def test_fences_and_anchors(self):
        text = '# 中文标题\n\n<a id="stable"></a>\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n'
        _outside, diagrams, errors = docs_check.parse(text)
        self.assertFalse(errors)
        self.assertEqual(len(diagrams), 1)
        self.assertEqual(docs_check.anchors(text), {"中文标题", "stable"})
        self.assertTrue(docs_check.parse("```plantuml\n@startuml")[2])

    def test_links_and_diagram_failures(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            doc = root / "docs/a.md"
            (root / "docs/b.md").write_text("# 标题\n")
            doc.write_text(
                "[有效](b.md#标题)\n[失效](b.md#missing)\n[不存在](lost.md)\n"
                "```plantuml\n@startuml\n@endmindmap\n```\n"
            )
            issues, count = docs_check.check_file(
                docs_check.DocIndex(root),
                doc,
            )
            self.assertEqual(count, 1)
            self.assertEqual(len(issues), 3)
            doc.write_text("```text\n[只是示例](lost.md)\n```\n")
            self.assertEqual(
                docs_check.check_file(
                    docs_check.DocIndex(root),
                    doc,
                )[0],
                [],
            )

    def test_reference_html_and_generated_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            doc = root / "a.md"
            (root / "b.md").write_text('# 标题\n```html\n<a id="fake"></a>\n```\n')
            (root / "diagram.svg").write_text("<svg/>")
            doc.write_text(
                "[有效][ref]\n[ref]: b.md#标题\n[缺失][missing]\n"
                '<a href="b.md#fake">错误锚点</a>\n![图](diagram.svg)\n'
                "`[不是真链接](missing.md)`\n"
            )
            issues, _ = docs_check.check_file(
                docs_check.DocIndex(root),
                doc,
            )
            self.assertEqual(len(issues), 3)
            self.assertTrue(any("未定义" in x for x in issues))
            self.assertTrue(any("失效锚点" in x for x in issues))
            self.assertTrue(any("生成图" in x for x in issues))

    def test_duplicate_anchor_and_external_diagram_source_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            doc = root / "a.md"
            doc.write_text(
                '<a id="same"></a>\n<a id="same"></a>\n'
                "```plantuml\n@startuml\n!include outside.puml\n@enduml\n```\n"
            )
            self.assertEqual(
                len(
                    docs_check.check_file(
                        docs_check.DocIndex(root),
                        doc,
                    )[0]
                ),
                2,
            )


if __name__ == "__main__":
    unittest.main()
