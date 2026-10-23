"""Podman fake transport 的字节语义、隔离和失败关闭回归。"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from podman_fake_transport import (
    _CANDIDATE_ROUTE,
    _COMMANDS,
    FINITE_TEST_CASES,
    FINITE_ROUTES,
    MAX_REQUEST_BYTES,
    PodmanFakeTransport,
    TransportError,
    _decode_args,
    _decode_env,
)


MODEL = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
counter = globals().get('counter', 0) + 1
mode = Path(os.environ['MODE_FILE']).read_text()
state = Path(os.environ['STATE_FILE'])
with state.open('a', encoding='utf-8') as output:
    output.write(json.dumps({'args': args, 'mode': mode, 'cwd': os.getcwd(), 'ctx': os.environ.get('CTX'), 'counter': counter}, ensure_ascii=False) + '\\n')
stdout = bytes.fromhex(os.environ['RAW_STDOUT_HEX']) if 'RAW_STDOUT_HEX' in os.environ else json.dumps({'args': args, 'mode': mode, 'cwd': os.getcwd(), 'ctx': os.environ.get('CTX'), 'counter': counter}, ensure_ascii=False).encode('utf-8')
stderr = bytes.fromhex(os.environ['RAW_STDERR_HEX']) if 'RAW_STDERR_HEX' in os.environ else ('err:' + mode).encode('utf-8')
sys.stdout.buffer.write(stdout)
sys.stderr.buffer.write(stderr)
kind = os.environ.get('EXIT_KIND', 'int')
if kind == 'large': sys.stdout.buffer.write(b'x' * 300000)
if kind == 'none': sys.exit()
if kind == 'text': sys.exit('synthetic-exit')
if kind == 'exception': raise ValueError('synthetic-model-error')
sys.exit(int(os.environ.get('EXIT_STATUS', '0')))
'''


class PodmanFakeTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lf transport 雪\n')
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.model = self.bin / 'podman'
        self.model.write_text('#!/usr/bin/env python3\n' + MODEL)
        self.model.chmod(0o700)
        self.mode = self.root / 'mode file'
        self.mode.write_text('first')
        self.state = self.root / 'state file'
        self.state.write_text('')
        self.case = 'test_transport_equivalence_and_context'
        self.transport = PodmanFakeTransport(self.model, self.bin, self.root, self.case)
        self.transport.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.transport.close()
        finally:
            self.temp.cleanup()

    def _context(self):
        return {**os.environ, 'MODE_FILE': str(self.mode), 'STATE_FILE': str(self.state),
                'CTX': '雪 "quoted"\nline'}

    def _call(self, argv, env):
        return subprocess.run([str(self.bin / 'podman'), *argv], cwd=self.root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)

    def _standalone(self, model, argv, env):
        return subprocess.run([sys.executable, str(model), *argv], cwd=self.root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)

    def _install_cat_spy(self):
        actual_cat = shutil.which('cat', path=os.defpath)
        self.assertIsNotNone(actual_cat)
        spy = self.root / 'cat-spy'
        calls = self.root / 'cat-calls'
        spy.write_text('#!/bin/sh\nprintf \'cat\\n\' >> ' + shlex.quote(str(calls)) + '\nexec '
                       + shlex.quote(actual_cat) + ' "$@"\n')
        spy.chmod(0o700)
        wrapper = self.bin / 'podman'
        content = wrapper.read_text()
        old = 'catb=' + shlex.quote(actual_cat)
        self.assertIn(old, content)
        wrapper.write_text(content.replace(old, 'catb=' + shlex.quote(str(spy))))
        wrapper.chmod(0o700)
        return calls

    def _await_drained(self, transport):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and list(transport.mailbox.glob('q.*')):
            time.sleep(.002)
        self.assertEqual(list(transport.mailbox.glob('q.*')), [])

    def test_transport_equivalence_and_context(self):
        original = self.transport.model
        baseline_mode = self.root / 'baseline-mode'
        baseline_state = self.root / 'baseline-state'
        transport_state = self.state
        args = ['probe', 'space value', '雪', '"quoted"', "back\\slash", 'line1\nline2', '']
        baseline_env = {**self._context(), 'MODE_FILE': str(baseline_mode), 'STATE_FILE': str(baseline_state)}
        transported_env = self._context()
        for mode in ('first', 'changed'):
            baseline_mode.write_text(mode)
            self.mode.write_text(mode)
            expected = self._standalone(original, args, baseline_env)
            actual = self._call(args, transported_env)
            self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                             (expected.stdout, expected.stderr, expected.returncode))
        self.assertEqual(baseline_state.read_bytes(), transport_state.read_bytes())
        self._await_drained(self.transport)
        self.assertNotIn('test_cancel_during_image_pull_releases_lock_and_retries', FINITE_TEST_CASES)
        self.assertNotIn('test_cancel_during_source_run_removes_only_owned_untagged_tool_image', FINITE_TEST_CASES)

    def test_output_fast_path_is_byte_equivalent_and_skips_cat_only_for_one_lf_line(self):
        calls = self._install_cat_spy()
        original = self.transport.model
        cases = (
            (b'', b'', 0),
            (b'one\tline \\\n', b'err\\tail with spaces\n', 0),
            ('雪\n'.encode(), '錯\n'.encode(), 0),
            (b'\n', b'', 0),
            (b'first\nsecond\n', b'', 1),
            (b'no-final-lf', b'error-no-lf', 2),
            (b'left\x00right\n', b'bad-\xff\n', 1),
            (b'', b'first\nsecond\n', 1),
            (b'', b'left\x00right\n', 1),
        )
        for stdout, stderr, cat_count in cases:
            with self.subTest(stdout=stdout, stderr=stderr):
                calls.write_bytes(b'')
                env = {**self._context(), 'EXIT_STATUS': '7',
                       'RAW_STDOUT_HEX': stdout.hex(), 'RAW_STDERR_HEX': stderr.hex()}
                expected = self._standalone(original, ['probe'], env)
                actual = self._call(['probe'], env)
                self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                                 (expected.stdout, expected.stderr, expected.returncode))
                observed = calls.read_text().splitlines() if calls.exists() else []
                self.assertEqual(len(observed), cat_count, observed)
                self._await_drained(self.transport)

    def test_candidate_route_isolated_and_native_fallback_is_byte_equivalent(self):
        self.assertIn(_CANDIDATE_ROUTE, FINITE_TEST_CASES)
        self.assertIs(FINITE_ROUTES['test_install_stop_up_does_not_reimport'], _COMMANDS)
        self.assertIsNot(FINITE_ROUTES[_CANDIDATE_ROUTE], _COMMANDS)
        with tempfile.TemporaryDirectory(prefix='lf candidate transport ') as directory:
            root = Path(directory).resolve()
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            model = bin_dir / 'podman'
            model.write_text('#!/usr/bin/env python3\n' + MODEL)
            model.chmod(0o700)
            mode, baseline_state, transported_state = root / 'mode', root / 'base', root / 'transported'
            mode.write_text('candidate')
            baseline_state.write_text('')
            transported_state.write_text('')
            transport = PodmanFakeTransport(model, bin_dir, root, _CANDIDATE_ROUTE)
            transport.start()
            env_base = {**os.environ, 'MODE_FILE': str(mode), 'STATE_FILE': str(baseline_state), 'CTX': 'candidate'}
            env_transport = {**os.environ, 'MODE_FILE': str(mode), 'STATE_FILE': str(transported_state), 'CTX': 'candidate'}
            routed = (['info'], ['image', 'exists', 'candidate'], ['image', 'inspect', 'candidate'], ['tag', 'one', 'two'],
                      ['compose', '--project-name', 'logs', 'up', '-d', 'api'],
                      ['compose', '--env-file', 'up', '-p', 'config', '-f', 'stop', 'config'],
                      ['compose', '--project-directory', 'up', '--file', 'config', 'stop', 'api'])
            native = (['load', 'archive.tar'], ['run', '--deployment-data', 'probe'], ['pull', 'image'],
                      ['compose', '--project-name', 'candidate', 'logs', '--follow'], ['compose', 'ps'],
                      ['compose', '--project-name', 'up', 'ps'],
                      ['compose', '-p', 'stop', 'ps'], ['compose', '-f', 'config', 'ps'],
                      ['compose', '--env-file', 'up', 'run', 'api'],
                      ['compose', '--unknown', 'up'], ['compose', '--file'],
                      ['compose', '--file', '--unknown', 'up'], ['compose', '-p', '', 'up'],
                      ['compose', 'ps', 'up'], ['compose', '--project-name=up', 'ps'])
            try:
                count_file = transport.mailbox / 'server.count'
                for should_route, args in [*((True, args) for args in routed), *((False, args) for args in native)]:
                    with self.subTest(args=args):
                        before_count = int(count_file.read_text()) if count_file.exists() else 0
                        actual = subprocess.run([str(bin_dir / 'podman'), *args], cwd=root, env=env_transport,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                        expected = subprocess.run([sys.executable, str(transport.model), *args], cwd=root, env=env_base,
                                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                        self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                                         (expected.stdout, expected.stderr, expected.returncode))
                        self._await_drained(transport)
                        after_count = int(count_file.read_text()) if count_file.exists() else 0
                        self.assertEqual(after_count - before_count, int(should_route))
                self.assertEqual(baseline_state.read_bytes(), transported_state.read_bytes())
                self._await_drained(transport)
                self.assertEqual((transport.mailbox / 'server.count').read_text(), str(len(routed)))
            finally:
                transport.close()

    def test_empty_argv_and_exit_semantics(self):
        env = {**self._context(), 'EXIT_STATUS': '7'}
        actual = self._call([], env)
        expected = self._standalone(self.transport.model, [], env)
        self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                         (expected.stdout, expected.stderr, expected.returncode))
        for kind in ('none', 'text'):
            with self.subTest(kind=kind):
                env = {**self._context(), 'EXIT_KIND': kind}
                actual = self._call([], env)
                expected = self._standalone(self.transport.model, [], env)
                self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                                 (expected.stdout, expected.stderr, expected.returncode))
        error = self._call(['probe'], {**self._context(), 'EXIT_KIND': 'exception'})
        native_error = self._standalone(self.transport.model, ['probe'],
                                        {**self._context(), 'EXIT_KIND': 'exception'})
        self.assertEqual((error.stdout, error.stderr, error.returncode),
                         (native_error.stdout,
                          native_error.stderr.replace(os.fsencode(str(self.transport.model)),
                                                      os.fsencode(str(self.bin / 'podman'))),
                          native_error.returncode))

    def test_bad_frames_and_limits_fail_closed(self):
        for frame in (b'', b'x\n', b'1\na', b'2\na\0', b'1\na\0extra\0'):
            with self.subTest(frame=frame), self.assertRaises(TransportError):
                _decode_args(frame)
        with self.assertRaises(TransportError):
            _decode_args(b'1\n' + b'x' * MAX_REQUEST_BYTES)
        for frame in (b'a=1', b'=bad\0', b'A=1\0A=2\0', b'A=1\0B'):
            with self.subTest(frame=frame), self.assertRaises(TransportError):
                _decode_env(frame)

    def test_each_transport_has_private_server_and_mailbox(self):
        other_root = self.root / 'other 雪'
        other_root.mkdir()
        other_bin = other_root / 'bin'
        other_bin.mkdir()
        other_model = other_bin / 'podman'
        other_model.write_text('#!/usr/bin/env python3\n' + MODEL)
        other_model.chmod(0o700)
        other_mode = other_root / 'mode'
        other_mode.write_text('other')
        other_state = other_root / 'state'
        other_state.write_text('')
        other = PodmanFakeTransport(other_model, other_bin, other_root, self.case)
        other.start()
        try:
            self.assertNotEqual(self.transport.server.pid, other.server.pid)
            self.assertNotEqual(self.transport.mailbox, other.mailbox)
            env_a = self._context()
            env_b = {**os.environ, 'MODE_FILE': str(other_mode),
                     'STATE_FILE': str(other_state), 'CTX': 'other'}
            a = self._call(['probe'], env_a)
            b = subprocess.run([str(other_bin / 'podman'), 'probe'], cwd=other_root, env=env_b,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertIn(b'first', a.stdout)
            self.assertIn(b'other', b.stdout)
            self._await_drained(self.transport)
            self._await_drained(other)
        finally:
            other.close()
            other.temp_cleanup = None
            import shutil
            shutil.rmtree(other_root)

    def test_server_crash_makes_transport_fail_not_empty_success(self):
        self.transport.server.kill()
        self.transport.server.wait(timeout=2)
        result = self._call(['probe'], self._context())
        self.assertNotEqual(result.returncode, 0)
        with self.assertRaises(TransportError):
            self.transport.close()

    def test_unknown_residual_is_sticky_failure(self):
        (self.transport.mailbox / 'q.999.unknown').write_text('unexpected')
        result = self._call(['probe'], self._context())
        self.assertEqual(result.returncode, 97)
        self.assertTrue((self.transport.mailbox / 'server.failed').is_file())
        with self.assertRaises(TransportError):
            self.transport.close()

    def test_model_output_over_limit_fails_closed(self):
        result = self._call(['probe'], {**self._context(), 'EXIT_KIND': 'large'})
        self.assertEqual(result.returncode, 97)
        with self.assertRaises(TransportError):
            self.transport.close()

    def test_malformed_published_request_fails_closed(self):
        base = self.transport.mailbox / 'q.999'
        Path(str(base) + '.args').write_bytes(b'1\nnot-terminated')
        Path(str(base) + '.env').write_bytes(b'')
        Path(str(base) + '.cwd').write_bytes(os.fsencode(str(self.root)) + b'\n')
        Path(str(base) + '.ready').touch()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not (self.transport.mailbox / 'server.failed').exists():
            time.sleep(.002)
        self.assertTrue((self.transport.mailbox / 'server.failed').exists())
        with self.assertRaises(TransportError):
            self.transport.close()

    def test_native_fallback_preserves_argv_and_never_uses_server(self):
        before = int((self.transport.mailbox / 'server.count').read_text()) if (self.transport.mailbox / 'server.count').exists() else 0
        for args in (['pull', 'space value', ''], ['run', '雪', ''],
                     ['compose', '--project-name', 'x', 'logs', '--follow'],
                     ['unlisted', 'quoted "value"', '']):
            with self.subTest(args=args):
                expected = self._standalone(self.transport.model, args, self._context())
                actual = self._call(args, self._context())
                self.assertEqual((actual.stdout, actual.stderr, actual.returncode),
                                 (expected.stdout, expected.stderr, expected.returncode))
        after = int((self.transport.mailbox / 'server.count').read_text()) if (self.transport.mailbox / 'server.count').exists() else 0
        self.assertEqual(after, before)

    def test_corrupt_response_length_is_sticky_failure(self):
        fake_wc = self.root / 'bad-wc'
        fake_wc.write_text('#!/bin/sh\nprintf "0 %s\\n0 %s\\n0 total\\n" "$2" "$3"\n')
        fake_wc.chmod(0o700)
        import podman_fake_transport as module
        real_which = module.shutil.which
        with patch.object(module.shutil, 'which', side_effect=lambda name, path=None: str(fake_wc) if name == 'wc' else real_which(name, path=path)):
            self.transport._write_wrapper()
        result = self._call(['probe'], self._context())
        self.assertEqual(result.returncode, 97)
        with self.assertRaises(TransportError):
            self.transport.close()

    def test_real_podman_model_native_transport_equivalence(self):
        from test_podman_local import LocalEntryTest
        transported = LocalEntryTest('test_verify_plan_failure_keeps_config_bound_for_retry')
        native = LocalEntryTest('test_verify_plan_failure_keeps_config_bound_for_retry')
        native.id = lambda: 'test_podman_local.LocalEntryTest.native_only'
        transported.setUp()
        native.setUp()
        try:
            self.assertIsNotNone(transported.fake_transport)
            self.assertIsNone(native.fake_transport)
            steps = (['info'], ['image', 'exists', 'unknown'],
                     ['build', '-t', 'synthetic:one', '--label', 'io.lexiflow.image-role=api', '/tmp'],
                     ['image', 'exists', 'synthetic:one'],
                     ['image', 'inspect', '--format', 'json', 'unknown'], ['network', 'ls'])
            for mode in ('', 'api-build-fail'):
                transported.mode.write_text(mode)
                native.mode.write_text(mode)
                for args in steps:
                    got = subprocess.run([str(transported.bin / 'podman'), *args], cwd=transported.root,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                    expected = subprocess.run([str(native.bin / 'podman'), *args], cwd=native.root,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
                    self.assertEqual((got.stdout, got.stderr, got.returncode),
                                     (expected.stdout, expected.stderr, expected.returncode))
            self.assertEqual(transported.calls.read_bytes(), native.calls.read_bytes())
            self.assertEqual(len(transported.calls.read_text().splitlines()), 12)
            self.assertEqual((transported.root / 'images.json').read_bytes(),
                             (native.root / 'images.json').read_bytes())
            self._await_drained(transported.fake_transport)
            self.assertEqual(int((transported.fake_transport.mailbox / 'server.count').read_text()), 12)
        finally:
            try:
                transported.tearDown()
            finally:
                native.tearDown()

    def test_original_cancel_case_does_not_start_server(self):
        from test_podman_local import LocalEntryTest
        original = LocalEntryTest('test_cancel_during_image_pull_releases_lock_and_retries')
        original.setUp()
        try:
            self.assertIsNone(original.fake_transport)
            self.assertFalse((original.root / 'podman-transport').exists())
        finally:
            original.tearDown()


if __name__ == '__main__':
    unittest.main()
