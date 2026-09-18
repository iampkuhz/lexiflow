"""文档治理、本机 skill 与共享策略投影的独立回归。"""
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.harness import docs_check, local_skills, policy_projection


class DocumentationTests(unittest.TestCase):
    def test_latest_only_rejects_history_and_dated_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs/reviews/history').mkdir(parents=True)
            (root / 'docs/reviews/state-20260918.md').write_text('# 状态\n')
            (root / 'docs/reviews/state-2026-09-18.md').write_text('# 状态\n')
            (root / 'docs/phase-1.md').write_text('# 当前设计\n')
            policy = {'maintenance': {'mode': 'latest-only',
                                      'forbidden_directory_names': ['history', 'archive', 'archives']}}
            self.assertEqual(len(docs_check.check_latest_only(root, policy)), 3)
            for file in (root / 'docs/reviews').glob('state-*.md'):
                file.unlink()
            (root / 'docs/reviews/history').rmdir()
            (root / 'tmp/history').mkdir(parents=True)
            (root / 'tmp/history/receipt-20260918.json').write_text('{}')
            self.assertEqual(docs_check.check_latest_only(root, policy), [])

    def test_latest_docs_and_current_input_bindings(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(docs_check.run(root)['issues'], [])
        profile = yaml.safe_load((root / 'harness/g1-task-contract-profiles.yaml').read_text())
        for entry in profile['profiles'].values():
            for locator in entry['required_inputs']:
                if locator.startswith('docs/'):
                    self.assertNotIn('/history/', locator)
                    self.assertTrue((root / locator).is_file(), locator)
        self.assertIn('docs/development/gate-control-plane-design.md',
                      profile['profiles']['LF-TSK-ARCH-0008']['required_inputs'])

    def test_fences_and_anchors(self):
        text = '# 中文标题\n\n<a id="stable"></a>\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n'
        outside, diagrams, errors = docs_check.parse(text)
        self.assertFalse(errors)
        self.assertEqual(len(diagrams), 1)
        self.assertEqual(docs_check.anchors(text), {'中文标题', 'stable'})
        self.assertTrue(docs_check.parse('```plantuml\n@startuml')[2])

    def test_links_and_diagram_failures(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs').mkdir()
            doc = root / 'docs/a.md'
            (root / 'docs/b.md').write_text('# 标题\n')
            doc.write_text('[有效](b.md#标题)\n[失效](b.md#missing)\n[不存在](lost.md)\n'
                           '```plantuml\n@startuml\n@endmindmap\n```\n')
            issues, count = docs_check.check_file(root, doc)
            self.assertEqual(count, 1)
            self.assertEqual(len(issues), 3)
            doc.write_text('```text\n[只是示例](lost.md)\n```\n')
            self.assertEqual(docs_check.check_file(root, doc)[0], [])

    def test_reference_html_and_generated_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            doc = root / 'a.md'
            (root / 'b.md').write_text('# 标题\n```html\n<a id="fake"></a>\n```\n')
            (root / 'diagram.svg').write_text('<svg/>')
            doc.write_text('[有效][ref]\n[ref]: b.md#标题\n[缺失][missing]\n'
                           '<a href="b.md#fake">错误锚点</a>\n![图](diagram.svg)\n'
                           '`[不是真链接](missing.md)`\n')
            issues, _ = docs_check.check_file(root, doc)
            self.assertEqual(len(issues), 3)
            self.assertTrue(any('未定义' in x for x in issues))
            self.assertTrue(any('失效锚点' in x for x in issues))
            self.assertTrue(any('生成图' in x for x in issues))

    def test_duplicate_anchor_and_external_diagram_source_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            doc = root / 'a.md'
            doc.write_text('<a id="same"></a>\n<a id="same"></a>\n'
                           '```plantuml\n@startuml\n!include outside.puml\n@enduml\n```\n')
            self.assertEqual(len(docs_check.check_file(root, doc)[0]), 2)

    def test_local_skill_link_idempotency_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'repo'
            source = Path(d) / 'source'
            (root / 'harness').mkdir(parents=True)
            (root / 'harness/documentation-policy.yaml').write_text(yaml.safe_dump({
                'skill_links': '.agents/skills', 'skills': [{'name': 'demo'}]}))
            self.assertEqual(local_skills.run(root, source, 'check')['status'], 'BLOCKED')
            (source / 'demo').mkdir(parents=True)
            (source / 'demo/SKILL.md').write_text('---\nname: demo\ndescription: 示例\n---\n')
            self.assertEqual(local_skills.run(root, source, 'link')['status'], 'PASS')
            self.assertEqual(local_skills.run(root, source, 'link')['status'], 'PASS')
            link = root / '.agents/skills/demo'
            link.unlink()
            link.mkdir()
            self.assertEqual(local_skills.run(root, source, 'link')['status'], 'FAIL')
            self.assertTrue(link.is_dir())

    def test_skill_missing_blocks_all_links(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'harness').mkdir()
            (root / 'harness/documentation-policy.yaml').write_text(yaml.safe_dump({
                'skill_links': '.agents/skills', 'skills': [{'name': 'a'}, {'name': 'b'}]}))
            (root / 'source/a').mkdir(parents=True)
            (root / 'source/a/SKILL.md').write_text('---\nname: a\n---\n')
            self.assertEqual(local_skills.run(root, root / 'source', 'link')['status'], 'BLOCKED')
            self.assertFalse((root / '.agents').exists())

    def test_skill_source_and_container_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'repo'
            source = Path(d) / 'source'
            (root / 'harness').mkdir(parents=True)
            (root / 'harness/documentation-policy.yaml').write_text(yaml.safe_dump({
                'skill_links': '.agents/skills', 'skills': [{'name': 'demo'}]}))
            (source / 'demo').mkdir(parents=True)
            (source / 'demo/SKILL.md').write_text('---\n[]\n---\n')
            result = local_skills.run(root, source, 'link')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['skills'][0]['status'], 'source-invalid')
            (root / '.agents').symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, '父链接'):
                local_skills.run(root, source, 'link')

    def test_skill_entry_symlink_keeps_installed_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'repo'
            source = Path(d) / 'source'
            upstream = Path(d) / 'upstream'
            (root / 'harness').mkdir(parents=True)
            (source / 'demo').mkdir(parents=True)
            upstream.mkdir()
            (root / 'harness/documentation-policy.yaml').write_text(yaml.safe_dump({
                'skill_links': '.agents/skills', 'skills': [{'name': 'demo'}]}))
            (upstream / 'SKILL.md').write_text('---\nname: demo\n---\n')
            (source / 'demo/SKILL.md').symlink_to(upstream / 'SKILL.md')
            (source / 'demo/installed-resource').write_text('安装目录中的资源')
            self.assertEqual(local_skills.run(root, source, 'link')['status'], 'PASS')
            self.assertEqual(local_skills.run(root, source, 'check')['status'], 'PASS')
            self.assertEqual(local_skills.run(root, source, 'link')['status'], 'PASS')
            self.assertEqual((root / '.agents/skills/demo').resolve(), (source / 'demo').resolve())
            self.assertTrue((root / '.agents/skills/demo/installed-resource').is_file())

    def test_projection_no_mutation_and_explicit_write(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'harness').mkdir()
            (root / 'harness/policy.yaml').write_text('model:\n  name: luna\n  values: [1, 2]\n')
            (root / 'harness/policy-projections.yaml').write_text(yaml.safe_dump({
                'source': 'harness/policy.yaml', 'projections': [
                    {'file': 'target.yaml', 'target': 'execution.model', 'source': 'model'}]}))
            target = root / 'target.yaml'
            original = '# 保留\nexecution:\n  model:\n    name: wrong\n    values: [3]\nother: hand-written\n'
            target.write_text(original)
            self.assertTrue(policy_projection.run(root))
            self.assertEqual(target.read_text(), original)
            policy_projection.run(root, write=True)
            self.assertEqual(policy_projection.run(root), [])
            self.assertIn('# 保留', target.read_text())
            self.assertEqual(yaml.safe_load(target.read_text())['other'], 'hand-written')
            self.assertEqual(yaml.safe_load(target.read_text())['execution']['model']['values'], [1, 2])

    def test_repository_projections_match(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(policy_projection.run(root), [])


if __name__ == '__main__':
    unittest.main()
