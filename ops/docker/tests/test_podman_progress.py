"""检查部署进度节流、日志即时可读及失败清理，不运行真实容器。"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / 'ops/podman/command.mjs').as_uri()


class CommandProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.log = self.root / 'private.log'
        self.node = shutil.which('node')
        self.pidfile = self.root / 'child.pid'
        self.advance = self.root / 'advance-heartbeat'
        self.release = self.root / 'release-child'

    def tearDown(self):
        self.tmp.cleanup()

    def launch(self, child, timeout=6000, announce=True, command=None, heartbeat_clock=False):
        argv = ['-e', child] if command is None else []
        options = {'cwd': str(self.root), 'timeout': timeout, 'logFile': str(self.log),
                   'capture': True, 'label': '测试子步骤', 'announce': announce}
        invocation = f'runCommand({json.dumps(command or self.node)}, {json.dumps(argv)}, {json.dumps(options)})'
        clock_driver = ''
        if heartbeat_clock:
            # 仅推进本例 interval；真实子进程、日志流、deadline 与取消均不使用假时钟。
            clock_driver = f'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {{mock}} from 'node:test';
import {{HEARTBEAT_MS}} from {json.dumps(HELPER)};
async function checkedHeartbeat() {{
  assert.equal(HEARTBEAT_MS, 20000);
  mock.timers.enable({{apis: ['setInterval']}});
  const originalLog = console.log;
  let beats = 0;
  console.log = (...args) => {{
    if (String(args[0]).includes('仍在')) beats++;
    originalLog(...args);
  }};
  const running = {invocation};
  // 立即登记 rejection；错误仍由下面的 await 抛出，不能成为提前的 unhandled rejection。
  running.catch(() => {{}});
  try {{
    const until = Date.now() + 10000;
    while (!fs.existsSync({json.dumps(str(self.advance))}) && Date.now() < until)
      await new Promise(resolve => setTimeout(resolve, 5));
    assert.ok(fs.existsSync({json.dumps(str(self.advance))}), 'parent log observation missing');
    mock.timers.tick(19999); assert.equal(beats, 0, 'heartbeat before 20 seconds');
    mock.timers.tick(1); assert.equal(beats, 1, 'missing heartbeat at 20 seconds');
    mock.timers.tick(19999); assert.equal(beats, 1, 'duplicate heartbeat before 40 seconds');
    fs.writeFileSync({json.dumps(str(self.release))}, '');
    const result = await running;
    mock.timers.tick(1); assert.equal(beats, 1, 'heartbeat timer survived command completion');
    return result;
  }} finally {{
    fs.writeFileSync({json.dumps(str(self.release))}, '');
    await running.catch(() => {{}});
    mock.timers.reset();
    console.log = originalLog;
  }}
}}
'''
        source = f'''import {{runCommand}} from {json.dumps(HELPER)};
{clock_driver}
try {{
const result = await {'checkedHeartbeat()' if heartbeat_clock else invocation};
console.log(result?.includes('PRIVATE-FIXTURE') ? 'CAPTURE_OK' : 'DONE');
}} catch(error) {{ console.error(error.message); process.exitCode=1; }}
'''
        return subprocess.Popen([self.node, '--input-type=module', '-e', source],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def finish(self, proc, timeout=30):
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate(); raise
        return proc.returncode, out, err

    def test_streams_private_log_before_exit_with_one_twenty_second_heartbeat(self):
        child = f"""console.log('PRIVATE-FIXTURE'); console.error('PRIVATE-STDERR');
const fs = require('fs');
function waitForRelease() {{
  if (!fs.existsSync({json.dumps(str(self.release))})) setTimeout(waitForRelease, 5);
}}
waitForRelease();"""
        proc = self.launch(child, timeout=26000, heartbeat_clock=True)
        try:
            until=time.monotonic()+4
            while time.monotonic()<until:
                if self.log.exists() and 'PRIVATE-STDERR' in self.log.read_text(): break
                time.sleep(.02)
            self.assertIsNone(proc.poll(), '检查实时日志时子进程应仍运行')
            self.assertIn('PRIVATE-FIXTURE',self.log.read_text())
            self.assertIn('PRIVATE-STDERR',self.log.read_text())
            self.assertEqual(self.log.stat().st_mode & 0o777,0o600)
            self.advance.touch()
            code,out,err=self.finish(proc)
            self.assertEqual(code,0,err)
            self.assertEqual(out.count('仍在'),1,out)
            self.assertIn('最近输出距今',out)
            self.assertIn('CAPTURE_OK',out)
            self.assertNotIn('PRIVATE-FIXTURE',out+err)
            self.assertNotIn('PRIVATE-STDERR',out+err)
            self.assertLessEqual(len(out.splitlines()),4)
        finally:
            # 即使父断言失败，也先允许本例子进程自然退出并让 runCommand 清理。
            if proc.poll() is None:
                self.advance.touch(); self.release.touch()
                try: proc.communicate(timeout=6)
                except subprocess.TimeoutExpired: proc.kill(); proc.communicate()

    def test_fast_auxiliary_command_is_silent(self):
        code,out,err=self.finish(self.launch("console.log('PRIVATE-FIXTURE')",announce=False))
        self.assertEqual(code,0,err);self.assertEqual(out.strip(),'CAPTURE_OK')

    def test_failure_reports_step_without_success_or_raw_output(self):
        code,out,err=self.finish(self.launch("console.error('PRIVATE-FIXTURE');process.exit(7)"))
        self.assertNotEqual(code,0);self.assertIn('退出码 7',err)
        self.assertIn(str(self.log),err);self.assertNotIn('完成',out)
        self.assertNotIn('PRIVATE-FIXTURE',out+err)

    def test_timeout_kills_owned_child_and_fails(self):
        child=f"require('fs').writeFileSync({json.dumps(str(self.pidfile))},String(process.pid)); process.on('SIGTERM',()=>{{}});setInterval(()=>{{}},1000)"
        code,out,err=self.finish(self.launch(child,timeout=500),timeout=6)
        self.assertNotEqual(code,0);self.assertIn('超过',err);self.assertNotIn('完成',out)
        with self.assertRaises(ProcessLookupError): os.kill(int(self.pidfile.read_text()),0)

    def test_interrupt_terminates_owned_child(self):
        child=f"require('fs').writeFileSync({json.dumps(str(self.pidfile))},String(process.pid));setInterval(()=>{{}},1000)"
        proc=self.launch(child)
        until=time.monotonic()+3
        while not self.pidfile.exists() and time.monotonic()<until: time.sleep(.02)
        self.assertTrue(self.pidfile.exists())
        proc.send_signal(signal.SIGINT)
        code,out,err=self.finish(proc,timeout=6)
        self.assertNotEqual(code,0);self.assertIn('收到中断请求',err)
        with self.assertRaises(ProcessLookupError): os.kill(int(self.pidfile.read_text()),0)

    def test_interrupt_waits_for_same_group_descendant_with_closed_stdio(self):
        grandchild=self.root/'grandchild.pid'
        grandcode=f"require('fs').writeFileSync({json.dumps(str(grandchild))},String(process.pid));process.on('SIGTERM',()=>{{}});setInterval(()=>{{}},1000)"
        child=f"require('fs').writeFileSync({json.dumps(str(self.pidfile))},String(process.pid));require('child_process').spawn(process.execPath,['-e',{json.dumps(grandcode)}],{{stdio:'ignore'}});setInterval(()=>{{}},1000)"
        proc=self.launch(child)
        until=time.monotonic()+3
        while not grandchild.exists() and time.monotonic()<until: time.sleep(.02)
        self.assertTrue(grandchild.exists())
        proc.send_signal(signal.SIGINT)
        code,out,err=self.finish(proc,timeout=8)
        self.assertNotEqual(code,0);self.assertIn('收到中断请求',err)
        with self.assertRaises(ProcessLookupError): os.kill(-int(self.pidfile.read_text()),0)
        with self.assertRaises(ProcessLookupError): os.kill(int(grandchild.read_text()),0)

    def test_missing_executable_fails_without_success(self):
        code,out,err=self.finish(self.launch('',command=str(self.root/'missing')))
        self.assertNotEqual(code,0);self.assertIn('无法启动',err);self.assertNotIn('完成',out)


if __name__ == '__main__':
    unittest.main()
