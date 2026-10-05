"""只读诊断的输出白名单、无副作用和失败分类。"""
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='doctor fixture ')
        self.root = Path(self.tmp.name).resolve()
        self.install = self.root/'install'
        self.bin = self.root/'bin';self.bin.mkdir()
        self.mode = self.root/'mode';self.mode.write_text('')
        self.calls = self.root/'calls'
        self.preload = self.root/'platform.mjs'
        self.preload.write_text("import os from 'node:os';os.platform=()=> 'darwin';os.arch=()=> 'arm64';")
        self.write_program('java', '#!/bin/sh\necho \'openjdk version "25.0.4"\' >&2\necho PRIVATE-FIXTURE >&2\n')
        fake='''#!PYTHON
import json,sys
from pathlib import Path
args=sys.argv[1:]
with Path(CALLS).open('a') as f: f.write(json.dumps(args)+'\\n')
mode=Path(MODE).read_text()
print('PRIVATE-FIXTURE secret/path',file=sys.stderr)
if args[:1]==['info']:
 if mode=='engine-down': sys.exit(9)
 print(json.dumps({'host':{'arch':'arm64','os':'linux'},'private':'PRIVATE-FIXTURE'}))
elif args[:1]==['compose']: print('podman-compose version 1.6.0')
elif args[:1]==['ps']:
 state=json.loads(Path(INSTALL,'state.json').read_text())
 if mode=='stopped': print('[]')
 else: print(json.dumps([{'State':'running','Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':service,'lexiflow.installation':'wrong' if mode=='foreign' else state['id']}} for service in ['postgres','api']]))
else: sys.exit(88)
'''.replace('PYTHON',os.sys.executable).replace('CALLS',repr(str(self.calls))).replace('MODE',repr(str(self.mode))).replace('INSTALL',repr(str(self.install)))
        self.write_program('podman',fake)
        self.server=None;self.thread=None

    def write_program(self,name,text):
        target=self.bin/name;target.write_text(text);target.chmod(0o755)

    def tearDown(self):
        if self.server: self.server.shutdown();self.server.server_close();self.thread.join()
        self.tmp.cleanup()

    def setup_install(self,api_contract='caption-hints.v2',phase='ready',health_code=200,health_status=None,oversized=False,redirect=False):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(inner):
                body={'mode':'formal','ready':True,'reason':'OK','softwareVersion':'0.1.0','apiContract':api_contract,'datasetVersion':1,'private':'PRIVATE-FIXTURE'}
                if inner.path.endswith('/readiness'):
                    body={'status':health_status or ('UP' if health_code==200 else 'DOWN')}
                    if oversized: body['padding']='x'*8193
                    if redirect:
                        inner.send_response(302);inner.send_header('Location','http://127.0.0.1:1/forbidden');inner.end_headers();return
                    inner.send_response(health_code)
                else: inner.send_response(200)
                inner.end_headers();inner.wfile.write(json.dumps(body).encode())
            def log_message(*_args): pass
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.install.mkdir()
        self.state={'schema':1,'root':str(self.install),'id':'a'*32,'project':'lexiflow-local-'+'a'*32,'phase':phase,'apiPort':self.server.server_port,'version':'0.1.0','private':'PRIVATE-FIXTURE'}
        (self.install/'state.json').write_text(json.dumps(self.state))
        (self.install/'secret-marker').write_text('PRIVATE-FIXTURE')

    def snapshot(self):
        if not self.install.exists(): return None
        return {str(p.relative_to(self.install)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.install.rglob('*') if p.is_file()}

    def invoke(self, directory=None):
        before=self.snapshot()
        result=subprocess.run([shutil.which('node'),'--import',str(self.preload),str(ROOT/'ops/podman/local.mjs'),'doctor','--json','--dir',directory or str(self.install)],env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']},capture_output=True,text=True,timeout=15)
        self.assertEqual(before,self.snapshot())
        self.assertNotIn('PRIVATE-FIXTURE',result.stdout+result.stderr)
        self.assertNotIn(str(self.root),result.stdout+result.stderr)
        self.assertFalse((self.install/'.lock-claim').exists())
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertTrue(all(call[0] in ['info','compose','ps'] for call in calls))
        return result,json.loads(result.stdout)

    def test_not_installed_is_blocked_and_creates_nothing(self):
        result,report=self.invoke()
        self.assertEqual(result.returncode,3);self.assertEqual(report['status'],'BLOCKED')
        self.assertEqual(report['checks'][-1]['reason'],'NOT_INSTALLED')
        self.assertFalse(self.install.exists())

    def test_ready_report_filters_tools_state_and_api_private_fields(self):
        self.setup_install();result,report=self.invoke()
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(report['status'],'PASS')
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_READY')

    def test_equivalent_absolute_directory_is_accepted(self):
        self.setup_install()
        for directory in [str(self.install)+'/',str(self.install)+'/../install']:
            with self.subTest(directory=directory):
                result,report=self.invoke(directory)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(report['status'],'PASS')

    def test_foreign_containers_fail_without_runtime_probe(self):
        self.setup_install();self.mode.write_text('foreign')
        result,report=self.invoke();self.assertEqual(result.returncode,1)
        self.assertIn('CONTAINER_OWNERSHIP',[c['reason'] for c in report['checks']])

    def test_incompatible_protocol_fails(self):
        self.setup_install(api_contract='caption-hints.v999')
        result,report=self.invoke();self.assertEqual(result.returncode,1)
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_INVALID')

    def test_legacy_lock_is_reported_but_not_removed(self):
        self.setup_install(phase='new');(self.install/'.lock').mkdir()
        result,report=self.invoke();self.assertEqual(result.returncode,3)
        self.assertIn('LEGACY_LOCK',[c['reason'] for c in report['checks']]);self.assertTrue((self.install/'.lock').is_dir())

    def test_corrupt_state_is_fail_and_not_echoed(self):
        self.setup_install();(self.install/'state.json').write_text('PRIVATE-FIXTURE')
        result,report=self.invoke();self.assertEqual(result.returncode,1)
        self.assertEqual(report['checks'][-1]['reason'],'INVALID_STATE')

    def test_readiness_failure_cannot_be_hidden_by_runtime_ready(self):
        self.setup_install(health_code=503)
        result,report=self.invoke();self.assertEqual(result.returncode,3)
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_NOT_READY')

    def test_http_200_down_is_not_ready(self):
        self.setup_install(health_status='DOWN')
        result,report=self.invoke();self.assertEqual(result.returncode,3)
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_NOT_READY')

    def test_oversized_health_is_rejected(self):
        self.setup_install(oversized=True)
        result,report=self.invoke();self.assertEqual(result.returncode,1)
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_INVALID')

    def test_health_redirect_is_rejected(self):
        self.setup_install(redirect=True)
        result,report=self.invoke();self.assertEqual(result.returncode,3)
        self.assertEqual(report['checks'][-1]['reason'],'RUNTIME_UNAVAILABLE')

    def test_malformed_lock_metadata_is_fail_without_modification(self):
        self.setup_install();(self.install/'.lock').mkdir()
        owner={'root':str(self.install),'installation':self.state['id'],'pid':os.getpid(),'childPid':None}
        (self.install/'.lock/owner.json').write_text(json.dumps(owner))
        result,report=self.invoke();self.assertEqual(result.returncode,1)
        self.assertIn('INVALID_LOCK',[c['reason'] for c in report['checks']])

    def test_engine_unavailable_does_not_attempt_runtime(self):
        self.setup_install();self.mode.write_text('engine-down')
        result,report=self.invoke();self.assertEqual(result.returncode,3)
        self.assertIn('ENGINE_UNAVAILABLE',[c['reason'] for c in report['checks']])


if __name__=='__main__': unittest.main()
