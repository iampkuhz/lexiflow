"""文档治理、本机 skill 与共享策略的独立回归。"""
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.repository import docs_check, local_skills


class DocumentationTests(unittest.TestCase):
    def test_product_roadmap_has_current_phases_and_future_backlog(self):
        root = Path(__file__).resolve().parents[2]
        entry = root / 'docs/roadmap/master-plan.md'
        content = entry.read_text()
        lifecycle = (root / 'docs/README.md').read_text()
        phase = (root / 'docs/roadmap/master-plan/phase2.md').read_text()
        self.assertIn('roadmap/master-plan.md', lifecycle)
        for name in ('phase2', 'future'):
            relative = f'docs/roadmap/master-plan/{name}.md'
            self.assertTrue((root / relative).is_file())
            self.assertIn(f'master-plan/{name}.md', content)
        for name in ('design', 'tasks', 'status'):
            relative = f'docs/roadmap/master-plan/phase2/{name}.md'
            self.assertTrue((root / relative).is_file())
            self.assertIn(f'phase2/{name}.md', phase)
        for obsolete in ('foundation.md', 'foundation/experience.md', 'foundation/acceptance.md', 'status.md'):
            self.assertFalse((root / 'docs/roadmap/master-plan' / obsolete).exists())
        self.assertIn('master-plan/phase2/status.md', content)
        for obsolete in ('phase-1-status.md', 'phase-2-status.md'):
            self.assertFalse((root / 'docs/roadmap' / obsolete).exists())
        self.assertIn('永不调用大模型', content)
        self.assertIn('事后分析', content)
        self.assertIn('方向确认不等于功能验收', content)

    def test_build_session_artifacts_are_ignored_but_product_sources_are_not(self):
        root = Path(__file__).resolve().parents[2]
        ignored = 'backend/gradle/build-logic/.kotlin/sessions/compiler.salive'
        source = 'backend/product/adapters/src/main/java/io/lexiflow/lexicon/platform/importer/RebuildProgress.java'
        result = subprocess.run(
            ['git', 'check-ignore', '--no-index', '--stdin'], cwd=root,
            input=f'{ignored}\n{source}\n', text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode)
        self.assertEqual([ignored], result.stdout.splitlines())
        self.assertEqual('', result.stderr)
        registry = yaml.safe_load((root / 'harness/module-checks.yaml').read_text())
        for check_id in ('eng.repository.module-tests', 'eng.repository.module-tests-on-change'):
            check = next(item for item in registry['checks'] if item['check_id'] == check_id)
            self.assertIn({'path': '.gitignore'}, check['triggers'])
            self.assertIn('.gitignore', registry['executions'][check['execution']]['input_paths'])

    def test_diagram_workflow_uses_tmp_drafts_and_markdown_final_source(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / 'harness/documentation-policy.yaml').read_text())
        workflow = policy['diagram_workflow']
        self.assertEqual(policy['canonical_diagram_source'], 'markdown-plantuml-fence')
        self.assertEqual(policy['local_artifact_root'], 'tmp/diagrams/')
        self.assertNotIn('draft_root', workflow)
        self.assertEqual(workflow['draft_source'], 'source.puml')
        self.assertNotIn('final_source', workflow)
        self.assertEqual(len(workflow['required_order']), 5)
        self.assertIn('不得存放新的 PUML', workflow['docs_artifact_rule'])
        self.assertIn('/tmp/', (root / '.gitignore').read_text())

    def test_documentation_policy_leaves_detail_shape_to_the_subject(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / 'harness/documentation-policy.yaml').read_text())
        architecture = policy['information_architecture']
        self.assertEqual(architecture['detail_placement'],
                         'sibling-directory-with-parent-stem-when-needed')
        self.assertNotIn('overview_roots', architecture)
        self.assertNotIn('overview_max_bytes', architecture)
        self.assertIn('不设固定模板、数量或字节预算', architecture['rule'])

    def test_documentation_policy_consolidation_contract(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / 'harness/documentation-policy.yaml').read_text())
        architecture = policy['information_architecture']
        self.assertEqual(architecture['lifecycle_entry'], 'docs/README.md')
        self.assertEqual(architecture['canonical_roots'], {
            'system': 'docs/system.md', 'system_details': 'docs/system/',
            'scenarios': 'docs/scenarios/', 'reference': 'docs/reference.md',
            'reference_details': 'docs/reference/', 'roadmap': 'docs/roadmap/',
            'user': 'docs/user/'})
        self.assertEqual(architecture['source_of_truth']['engineering_rules'],
                         ['AGENTS.md', 'harness/'])
        self.assertEqual(architecture['source_of_truth']['execution_state'], 'status-page-only')
        self.assertEqual(architecture['organization']['principle'], 'unique-body-per-fact')
        self.assertTrue(architecture['organization'][
            'optimized_workflows_reference_execution_workflows_without_copying_subflows'])
        self.assertEqual(architecture['note_contract']['abstract_stage'], ['input', 'action', 'output'])
        self.assertEqual(architecture['note_contract']['concrete_execution'],
                         ['entrypoint', 'purpose', 'expected-duration'])
        self.assertTrue(architecture['note_contract']['unknown_is_not_expected'])
        self.assertTrue(architecture['organization']['omit_empty_helper_branches'])
        for locator in architecture['canonical_roots'].values():
            self.assertTrue((root / locator).exists(), locator)
        for obsolete in ('architecture', 'product', 'development'):
            self.assertFalse((root / 'docs' / obsolete).exists(), obsolete)
        self.assertEqual(policy['terminology']['engineering_glossary'],
                         'docs/reference/engineering-glossary.md')
        self.assertEqual(policy['terminology']['product_glossary'],
                         'docs/reference/product-glossary.md')

    def test_documentation_rejects_candidate_solution_headings(self):
        policy = {'selected_solution_only': {
            'documentation_roots': ['docs'],
            'prohibited_heading_patterns': ['(?:候选|替代)方案', '方案比较', '方案\\s*[A-Z]'],
            'prohibited_prose_patterns': ['(?:候选|替代)方案', '方案\\s*[A-Z]\\s*[：:]', '^\\s*\\|\\s*方案\\s*\\|']}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            document = root / 'docs/design.md'
            document.parent.mkdir()
            document.write_text('# 1. 方案比较\n')
            self.assertEqual(len(docs_check.check_selected_solution_only(root, policy)), 1)
            document.write_text('# 1. 已确定的合同\n| 方案 | 说明 |\n| --- | --- |\n')
            self.assertEqual(len(docs_check.check_selected_solution_only(root, policy)), 1)
            document.write_text('# 1. 已确定的合同\n')
            self.assertEqual(docs_check.check_selected_solution_only(root, policy), [])

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

    def test_version_comparison_requires_comparison_path_and_title(self):
        policy = {'maintenance': {'version_comparison': {
            'documentation_roots': ['docs'],
            'allowed_path_tokens': ['comparison', 'diff'],
            'allowed_title_tokens': ['对比', '差异', 'comparison', 'diff'],
            'prohibited_phrases': ['待审核目标', '改前', '改后']}}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs').mkdir()
            ordinary = root / 'docs/architecture.md'
            ordinary.write_text('# 1. 架构\n待审核目标\n')
            self.assertEqual(len(docs_check.check_version_comparisons(root, policy)), 1)

    def test_version_comparison_blocks_chinese_context_in_headings_tables_and_prose(self):
        policy = {'maintenance': {'version_comparison': {
            'documentation_roots': ['docs'], 'allowed_path_tokens': ['comparison', 'diff'],
            'allowed_title_tokens': ['对比', '差异', 'comparison', 'diff'],
            'prohibited_phrases': ['改前', '改后', '当前版本', '目标版本']}}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs').mkdir()
            doc = root / 'docs/status.md'
            for content in ('# 1. 改后设计\n', '| 阶段 | 当前版本 |\n', '现在采用目标版本配置\n'):
                doc.write_text(content)
                self.assertEqual(len(docs_check.check_version_comparisons(root, policy)), 1)

    def test_version_phrase_matching_avoids_embedded_substrings_but_still_blocks_comparisons(self):
        policy = {'maintenance': {'version_comparison': {
            'documentation_roots': ['docs'], 'allowed_path_tokens': ['comparison', 'diff'],
            'allowed_title_tokens': ['对比', '差异', 'comparison', 'diff'],
            'prohibited_phrases': ['改后', '改前', '当前版本', '目标版本']}}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs').mkdir()
            status = root / 'docs/status.md'
            for verb in ('修改', '更改', '整改', '变改', '涂改', '篡改', '批改', '删改'):
                for when in ('前', '后'):
                    status.write_text(f'# 1. 状态\n{verb}{when}运行验证\n')
                    self.assertEqual(docs_check.check_version_comparisons(root, policy), [])
            status.write_text('# 1. 状态\n改后版本使用新流程\n')
            self.assertEqual(len(docs_check.check_version_comparisons(root, policy)), 1)

    def test_run_reuses_each_markdown_parse_within_one_invocation(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'docs').mkdir()
            (root / 'harness').mkdir()
            (root / 'harness/documentation-policy.yaml').write_text(yaml.safe_dump({
                'forbidden_diagram_link_extensions': ['.puml'],
                'root_instruction_budget_bytes': 10000,
                'heading_numbering': {'required': False},
                'selected_solution_only': {'documentation_roots': []},
                'maintenance': {'mode': 'latest-only', 'forbidden_directory_names': [],
                    'version_comparison': {'documentation_roots': ['docs'], 'prohibited_phrases': []}},
            }))
            target = root / 'docs/target.md'
            target.write_text('# target\n')
            source = root / 'docs/source.md'
            source.write_text('[a](target.md#target) [b](target.md#target)\n')
            (root / 'AGENTS.md').write_text('ok')
            (root / 'README.md').write_text('ok')
            (root / 'harness/README.md').write_text('ok')
            original = docs_check.parse
            calls = []
            def counted(text):
                calls.append(text)
                return original(text)
            with patch.object(docs_check, 'parse', side_effect=counted):
                result = docs_check.run(root)
            self.assertEqual(result['issues'], [])
            # 两个正文只在 run 开始时解析；重复锚点链接复用 target 索引。
            self.assertEqual(len(calls), 5)

    def test_latest_docs_run_clean(self):
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(docs_check.run(root)['issues'], [])

    def test_docs_headings_require_hierarchical_numbers(self):
        policy = {'heading_numbering': {'required': True}}
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'doc.md'
            file.write_text('# 1. 总览\n## 1.1. 细节\n```text\n# 不检查代码\n```\n')
            self.assertEqual(docs_check.check_heading_numbering(file, policy), [])
            file.write_text('# 总览\n## 1.1. 细节\n')
            self.assertEqual(len(docs_check.check_heading_numbering(file, policy)), 1)

    def test_fences_and_anchors(self):
        text = '# 中文标题\n\n<a id="stable"></a>\n```plantuml\n@startuml\nA -> B\n@enduml\n```\n'
        _outside, diagrams, errors = docs_check.parse(text)
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

    def test_dispatch_policy_is_the_single_runtime_source(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / 'harness/agent-policy.manifest.yaml').read_text())
        dispatch = policy['agent_dispatch']
        self.assertEqual(dispatch['primary'], 'native-codex')
        self.assertEqual(dispatch['fallback_model'], 'gpt-6-luna')
        self.assertEqual(dispatch['max_consecutive_failures'], 3)
        self.assertFalse((root / 'harness/agent-runtime.manifest.yaml').exists())
        policy_text = (root / 'harness/agent-policy.manifest.yaml').read_text()
        self.assertIn('Session 生命周期与 checkout 由客户端管理', policy_text)
        self.assertIn('读写不要求统一仓库状态', policy_text)

    def test_policy_has_actual_acceptance_identity_boundary(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load((root / 'harness/agent-policy.manifest.yaml').read_text())
        acceptance = policy['codex_dispatch']['acceptance']
        self.assertTrue(acceptance['implementer_must_not_validate_or_review'])
        self.assertTrue(acceptance['reviewer_must_differ_from_validator'])
        exception = policy['codex_dispatch']['new_session_exception']
        self.assertEqual(exception['default'], 'forbidden-for-internal-delegation')
        self.assertTrue(exception['verify_actual_model_after_start'])


if __name__ == '__main__':
    unittest.main()
