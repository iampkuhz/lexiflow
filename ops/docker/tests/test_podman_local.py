"""验证本机部署入口的编排和失败边界；替身不能证明真实容器成功。"""
import contextlib
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import signal
import time
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[3]


class LocalEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lexiflow local ")
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "source"
        self.kit = self.root / "install"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.mode = self.root / "mode"
        self.mode.write_text("")
        for relative in ["ops/podman/local.mjs", "ops/podman/command.mjs", "ops/podman/doctor.mjs", "ops/podman/network-repair.mjs", "ops/podman/compose.validation.yaml",
                         "ops/podman/fetch-ecdict.sh", "ops/podman/source-tools.Containerfile",
                         "ops/docker/Dockerfile", "ops/docker/Dockerfile.postgres",
                         "ops/docker/entrypoint.sh", "ops/docker/bootstrap.sh",
                         "ops/dataset/ecdict-source.lock.json", "scripts/environment/ecdict_bundle.py",
                         "infra/postgres/schema.sql", "ops/release/version.txt"]:
            dst = self.repo / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dst)
        (self.repo / "backend/product/api/build/libs").mkdir(parents=True)
        version = (self.repo / "ops/release/version.txt").read_text().strip()
        (self.repo / f"backend/product/api/build/libs/api-{version}.jar").write_text("synthetic")
        (self.repo / "extension/dist").mkdir(parents=True)
        (self.repo / "extension/dist/manifest.json").write_text(json.dumps({"version": version}))
        self.program(self.repo / "backend/gradlew", "#!/bin/sh\nexit 0\n")
        self.program(self.bin / "npm", "#!/bin/sh\nexit 0\n")
        self.program(self.bin / "java", '#!/bin/sh\necho \'openjdk version "25.0.1"\' >&2\n')
        # 用 Node 标准测试预加载替换平台探测；产品本身无测试开关或平台绕过参数。
        preload = self.root / "platform.mjs"
        preload.write_text("import os from 'node:os'; os.platform=()=> 'darwin'; os.arch=()=> 'arm64';")
        self.preload = preload
        fake = '''#!PYTHON
import json, sys
from pathlib import Path
args=sys.argv[1:]
with Path(CALLS).open('a') as f: f.write(json.dumps(args)+'\\n')
mode=Path(MODE).read_text()
if args[:1]==['pull'] and mode=='slow-pull':
    import time
    print('private-download-output',flush=True);time.sleep(30)
elif args[:1]==['info']: print('{}')
elif args[:2]==['image','inspect']: print('sha256:'+'a'*64)
elif args[:1]==['ps'] or args[:2] in [['volume','ls'],['network','ls']]:
    if mode=='foreign':
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'lexiflow.installation':'foreign'}}]))
    elif mode=='owned-pg':
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':'postgres','lexiflow.installation':state['id']}}]))
    else: print('[]')
elif args[:1]==['run']:
    if mode=='fetch-fail': sys.exit(9)
    data=Path(KIT,'data/ecdict-source.ABC123');data.mkdir(exist_ok=True)
    (data/'stardict.csv').write_text('word,translation,oxford,tag,bnc,frq,exchange\\ntest,测试,1,,1,1,\\n')
    print('dataset_dir=/data/ecdict-source.ABC123')
elif args[:1]==['compose'] and 'initialize' in args and mode=='init-fail': sys.exit(8)
'''.replace("PYTHON", os.sys.executable).replace("CALLS", repr(str(self.calls))).replace("MODE", repr(str(self.mode))).replace("KIT", repr(str(self.kit)))
        self.program(self.bin / "podman", fake)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = {"status": "UP"} if self.path.endswith("readiness") else {"mode": "formal", "ready": True, "reason": "OK", "softwareVersion": version, "apiContract": "caption-hints.v1"}
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *_args):
                pass
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api_port = self.server.server_port
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.db_port = reserve.getsockname()[1]
        # install 检查空闲端口后才启动替身HTTP；run()在后台等待写入初始化状态。
        self.thread = None
        self.finished = threading.Event()

    @staticmethod
    def program(file, content):
        file.write_text(content); file.chmod(0o755)

    def tearDown(self):
        self.finished.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.server.server_close()
        self.temp.cleanup()

    def invoke(self, action, *extra, serve=False):
        if serve and not self.thread:
            # 释放预留监听，install preflight 后重建；同线程有界观察夹具state。
            self.server.server_close()
            def later():
                import time
                for _ in range(500):
                    if self.finished.is_set(): return
                    try:
                        state=json.loads((self.kit/'state.json').read_text())
                        if state['phase'] in ['initializing','initialized','ready']: break
                    except (FileNotFoundError, json.JSONDecodeError): pass
                    time.sleep(.02)
                self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.api_port), self.server.RequestHandlerClass)
                self.server.timeout = .1
                while not self.finished.is_set():
                    self.server.handle_request()
            self.thread=threading.Thread(target=later, daemon=True);self.thread.start()
        elif not self.thread:
            self.server.server_close()
        argv = [shutil.which('node'), '--import', str(self.preload), str(self.repo/'ops/podman/local.mjs'), action, '--dir', str(self.kit)]
        if action == 'install': argv += ['--api-port', str(self.api_port), '--db-port', str(self.db_port)]
        return subprocess.run(argv+list(extra), env={**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}, capture_output=True, text=True, timeout=35)

    def test_unknown_directory_is_not_adopted(self):
        self.kit.mkdir(); (self.kit/'keep').write_text('safe')
        result=self.invoke('install')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.kit/'keep').read_text(),'safe')
        self.assertFalse(self.calls.exists())

    def test_install_stop_up_does_not_reimport(self):
        result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(state['phase'],'ready')
        self.assertEqual((self.kit/'secrets/app-password').stat().st_mode & 0o777,0o444)
        self.assertIn('sha256:'+'a'*64,(self.kit/'release.env').read_text())
        for action in ['stop','up','install','status']:
            result=self.invoke(action); self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('initialize' in call for call in calls),1)
        self.assertFalse(any('prune' in call or '-v' in call and 'down' in call for call in calls))

    def test_fetch_failure_can_retry_before_database(self):
        self.mode.write_text('fetch-fail')
        result=self.invoke('install'); self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'built')
        secret=(self.kit/'secrets/app-password').read_bytes()
        self.mode.write_text('')
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(secret,(self.kit/'secrets/app-password').read_bytes())

    def test_initialized_install_repairs_network_without_reimport(self):
        result = self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)
        compose = self.kit/'compose.yaml'
        legacy = compose.read_text().split('  published:\n')[0].replace('networks: [private, published]', 'networks: [private]')
        compose.write_text(legacy)
        state_file = self.kit/'state.json'
        state = json.loads(state_file.read_text())
        state['digests']['compose.yaml'] = hashlib.sha256(legacy.encode()).hexdigest()
        state['phase'] = 'initialized'
        state_file.write_text(json.dumps(state))
        preserved = {key:value for key,value in state['digests'].items() if key != 'compose.yaml'}
        (self.kit/'state.json.next').write_text('interrupted-old-save')
        result = self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('已自动修复',result.stdout)
        self.assertEqual((self.kit/'state.json.next').read_text(),'interrupted-old-save')
        after = json.loads(state_file.read_text())
        self.assertEqual(preserved,{key:value for key,value in after['digests'].items() if key != 'compose.yaml'})
        self.assertIn('networks: [private, published]',compose.read_text())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('initialize' in row and 'run' in row for row in calls),1)
        self.assertFalse(any('down' in row or 'prune' in row for row in calls))

    def test_readiness_deadline_keeps_initialized_data(self):
        # 只在标准Node preload替换单调时钟，产品没有缩短超时的测试开关。
        with self.preload.open('a') as output:
            output.write("Object.defineProperty(globalThis,'performance',{value:{now:(()=>{let n=0;return()=>n+=30000})()}});")
        result=self.invoke('install')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('最多 120 秒',result.stdout)
        self.assertIn('API 就绪等待超时',result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'initialized')
        self.assertTrue((self.kit/'secrets/app-password').exists())
        self.assertFalse((self.kit/'.lock').exists())

    def test_uncertain_initialization_never_retries(self):
        self.mode.write_text('init-fail')
        result=self.invoke('install'); self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'initializing')
        self.mode.write_text('')
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('initialize' in call for call in calls),1)

    def test_tampered_configuration_refuses_stop(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        with (self.kit/'release.env').open('a') as file: file.write('EXTRA=value\n')
        before=self.calls.read_text()
        result=self.invoke('stop');self.assertNotEqual(result.returncode,0)
        self.assertNotIn('"stop"',self.calls.read_text()[len(before):])

    def test_foreign_project_label_refuses_stop(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        self.mode.write_text('foreign')
        result=self.invoke('stop');self.assertNotEqual(result.returncode,0)
        self.assertIn('归属不匹配',result.stderr)

    def test_owned_postgres_port_does_not_block_preinit_retry(self):
        self.mode.write_text('init-fail')
        result=self.invoke('install'); self.assertNotEqual(result.returncode,0)
        state=json.loads((self.kit/'state.json').read_text())
        # 重建“PG启动后、标记initializing前”保存的状态，未修改产品入口。
        state['phase']='source'
        (self.kit/'state.json').write_text(json.dumps(state))
        self.mode.write_text('owned-pg')
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1',state['dbPort']));listener.listen()
            result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_schema_and_csv_tampering_block_initialization(self):
        self.mode.write_text('init-fail')
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        state=json.loads((self.kit/'state.json').read_text())
        state['phase']='source'
        (self.kit/'state.json').write_text(json.dumps(state))
        self.mode.write_text('')
        for file in [self.kit/'infra/postgres/schema.sql',Path(state['dataset'])/'stardict.csv']:
            before=file.read_bytes();file.write_bytes(before+b'changed')
            calls=self.calls.read_text()
            result=self.invoke('install');self.assertNotEqual(result.returncode,0)
            self.assertIn('发生变化',result.stderr)
            self.assertNotIn('"initialize"',self.calls.read_text()[len(calls):])
            file.write_bytes(before)

    def test_cancel_during_image_pull_releases_lock_and_retries(self):
        self.server.server_close()
        self.mode.write_text('slow-pull')
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/local.mjs'),'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port)]
        proc=subprocess.Popen(argv,env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            until=time.monotonic()+10
            while time.monotonic()<until:
                if self.calls.exists() and any(json.loads(line)[0]=='pull' for line in self.calls.read_text().splitlines()): break
                time.sleep(.02)
            self.assertIsNone(proc.poll())
            self.assertTrue((self.kit/'.lock/owner.json').exists())
            proc.send_signal(signal.SIGINT)
            out,err=proc.communicate(timeout=8)
            self.assertNotEqual(proc.returncode,0)
            self.assertIn('收到中断请求',err)
            self.assertFalse((self.kit/'.lock').exists())
            self.assertNotIn('private-download-output',out+err)
        finally:
            if proc.poll() is None: proc.kill();proc.communicate()
        self.mode.write_text('')
        result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_legacy_empty_lock_requires_confirmation_and_preserves_data(self):
        self.mode.write_text('fetch-fail');result=self.invoke('install')
        self.assertNotEqual(result.returncode,0)
        secret=(self.kit/'secrets/app-password').read_bytes()
        (self.kit/'.lock').mkdir()
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        result=self.invoke('recover');self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.kit/'.lock').exists())
        result=self.invoke('recover','--confirm-stopped');self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.kit/'.lock').exists())
        self.assertEqual(secret,(self.kit/'secrets/app-password').read_bytes())
        self.mode.write_text('');result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_active_lock_cannot_be_recovered_or_removed(self):
        self.mode.write_text('fetch-fail');self.invoke('install')
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':os.getpid(),'token':'a'*32,'childPid':None}
        text=json.dumps(owner,separators=(',',':'))
        (self.kit/'.lock/owner.json').write_text(text)
        result=self.invoke('recover','--confirm-stopped');self.assertNotEqual(result.returncode,0)
        self.assertEqual(text,(self.kit/'.lock/owner.json').read_text())

    def test_existing_claim_refuses_recovery_without_touching_locks(self):
        self.mode.write_text('fetch-fail');self.invoke('install')
        (self.kit/'.lock').mkdir()
        (self.kit/'.lock-claim').mkdir()
        result=self.invoke('recover','--confirm-stopped')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.kit/'.lock').is_dir())
        self.assertTrue((self.kit/'.lock-claim').is_dir())

    def test_dead_owned_lock_is_reclaimed_without_confirmation(self):
        self.mode.write_text('fetch-fail');self.invoke('install')
        old=subprocess.Popen([shutil.which('node'),'-e','']);old.wait()
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':old.pid,'token':'b'*32,'childPid':None}
        (self.kit/'.lock/owner.json').write_text(json.dumps(owner,separators=(',',':')))
        self.mode.write_text('');result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_symlink_directory_is_rejected(self):
        real=self.root/'real';real.mkdir();self.kit.symlink_to(real)
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        self.assertEqual(list(real.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
