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
from unittest.mock import patch

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
        for relative in ["ops/podman/local.mjs", "ops/podman/unified-local.mjs", "ops/podman/deployment.mjs", "ops/podman/database-target.mjs", "ops/podman/candidate.mjs", "ops/podman/java-runtime.mjs", "ops/podman/process-lock.mjs", "ops/podman/command.mjs", "ops/podman/doctor.mjs", "ops/podman/network-repair.mjs", "ops/podman/upgrade.mjs", "ops/podman/prepare-workspace.mjs", "ops/podman/versions.mjs", "ops/podman/compose.validation.yaml",
                         "ops/podman/fetch-ecdict.sh", "ops/podman/source-tools.Containerfile",
                         "ops/docker/Dockerfile", "ops/docker/Dockerfile.postgres",
                         "ops/docker/entrypoint.sh", "ops/docker/bootstrap.sh",
                         "ops/dataset/ecdict-source.lock.json", "ops/dataset/generation-inputs.json", "ops/dataset/generation-version.mjs", "scripts/environment/ecdict_bundle.py",
                         "infra/postgres/schema.sql", "ops/release/version.txt", "ops/release/version.mjs", "ops/release/archive-identity.mjs"]:
            dst = self.repo / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dst)
        (self.repo / 'fixture.txt').write_text('stable source fixture\n')
        (self.repo / 'ops/dataset/generation-inputs.json').write_text(json.dumps({'schemaVersion': 1, 'include': ['fixture.txt']}))
        # Build identity is calculated by the production resolver from Git-visible inputs.
        (self.repo / ".gitignore").write_text("tmp/\n.local/\nbackend/product/api/build/\nextension/dist/\nextension/node_modules/\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Synthetic Fixture"], check=True)
        # Resolver imports need the release module too; all resolver-visible files are committed as fixture source.
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)
        (self.repo / "backend/product/api/build/internal/boot").mkdir(parents=True)
        (self.repo / "extension/dist").mkdir(parents=True)
        self.program(self.repo / "backend/gradlew", "#!/bin/sh\nset -eu\nnode -e \"import('../ops/release/version.mjs').then(({resolveBuildIdentity})=>{const fs=require('fs'); fs.mkdirSync('product/api/build/internal/boot',{recursive:true}); fs.writeFileSync('product/api/build/internal/boot/api-internal.jar','synthetic');})\"\n")
        self.program(self.bin / "npm", "#!/bin/sh\nset -eu\nif [ \"$1\" = run ] && [ \"$2\" = build ]; then node -e \"import('../ops/release/version.mjs').then(({resolveBuildIdentity})=>{const fs=require('fs'); const id=resolveBuildIdentity('..'); const port=process.env.LEXIFLOW_API_PORT; fs.mkdirSync('dist',{recursive:true}); fs.writeFileSync('dist/build-identity.json',JSON.stringify(id)); fs.writeFileSync('dist/manifest.json',JSON.stringify({version:id.chromeVersion,version_name:id.softwareVersion,host_permissions:['http://127.0.0.1:'+port+'/*']}));})\"; fi\n")
        self.program(self.bin / "java", f'''#!/bin/sh
if [ "$1" = -version ]; then echo 'openjdk version "25.0.1"' >&2; exit 0; fi
if [ "$3" = --deployment-data ]; then
  if [ "$4" = inspect ]; then printf '{{"status":"PASS","database":"lexiflow","schema":"lexiflow_release","fingerprint":"{'f'*64}","needsRebuild":false,"reason":"OK","generationVersion":"%s","datasetVersion":1}}\\n' "$(cat {str(self.root / 'generation-version')!r})"; exit 0; fi
fi
exit 1
''')
        local_java = self.repo / '.local/toolchains/jdk-25/Contents/Home'
        (local_java / 'bin').mkdir(parents=True)
        shutil.copyfile(self.bin / 'java', local_java / 'bin/java')
        (local_java / 'bin/java').chmod(0o755)
        (local_java / 'release').write_text('JAVA_VERSION="25.0.1"\nIMPLEMENTOR="Eclipse Adoptium"\nIMPLEMENTOR_VERSION="Temurin-25.0.1+1"\n')
        version = json.loads(subprocess.check_output(["node", "-e", "import('./ops/release/version.mjs').then(({resolveBuildIdentity})=>console.log(JSON.stringify(resolveBuildIdentity('.'))))"], cwd=self.repo, text=True))["softwareVersion"]
        # 用 Node 标准测试预加载替换平台探测；产品本身无测试开关或平台绕过参数。
        preload = self.root / "platform.mjs"
        preload.write_text("import os from 'node:os'; os.platform=()=> 'darwin'; os.arch=()=> 'arm64';")
        self.preload = preload
        fake = '''#!PYTHON
import hashlib, json, sys, os, subprocess
from pathlib import Path
args=sys.argv[1:]
images_file=Path(CALLS).with_name('images.json')
runtime_file=Path(CALLS).with_name('runtime.json')
images=json.loads(images_file.read_text()) if images_file.exists() else {}
with Path(CALLS).open('a') as f: f.write(json.dumps(args)+'\\n')
mode=Path(MODE).read_text()
if args[:1]==['pull'] and mode=='slow-pull':
    import time
    print('private-download-output',flush=True);time.sleep(30)
elif args[:1]==['info']: print('{}')
elif args[:2]==['image','inspect']:
    entry=images.get(args[-1])
    if '--format' in args and args[args.index('--format')+1]=='{{json .Labels}}':
        print(json.dumps(entry['labels'] if entry else {}))
    elif '--format' in args and args[args.index('--format')+1]=='{{json .Os}} {{json .Architecture}}':
        print('\"linux\" \"arm64\"')
    else: print(entry['id'] if entry else 'sha256:'+hashlib.sha256(args[-1].encode()).hexdigest())
elif args[:1]==['ps'] or args[:2] in [['volume','ls'],['network','ls']]:
    if mode=='foreign':
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'lexiflow.installation':'foreign'}}]))
    elif mode=='owned-pg' and '-a' in args:
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':'postgres','lexiflow.installation':state['id']}}]))
    elif args[:1]==['ps']:
        state=json.loads(Path(KIT,'state.json').read_text())
        runtime=json.loads(runtime_file.read_text()) if runtime_file.exists() else None
        print(json.dumps([{'Id':runtime['containerId'],'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':'api','lexiflow.installation':state['id']}}] if runtime else []))
    else: print('[]')
elif args[:1]==['inspect']:
    print(json.loads(runtime_file.read_text())['id'])
elif args[:1]==['run']:
    if mode=='fetch-fail': sys.exit(9)
    if '/bin/sh' in args:
        sql=args[-1]
        if 'pg_database' in sql:
            print(json.dumps({'serverVersion':17,'currentUser':'postgres','userOid':10,'canCreate':True,
              'databaseExists':True,'databaseOid':16385,'databaseOwner':'postgres'}))
        elif 'pg_namespace' in sql:
            print(json.dumps({'schemaExists':True,'schemaOid':16386,'owner':'postgres','canCreateSchema':True}))
        else: sys.exit(7)
    elif '--deployment-data' in args:
        if 'apply' in args:
            if mode=='data-apply-fail': sys.exit(9)
            print(json.dumps({'status':'PASS','rebuilt':True,'generationVersion':Path(CALLS).with_name('generation-version').read_text(),'datasetVersion':1}))
        else:
            committed=mode=='external' or Path(KIT,'state.json').exists() and json.loads(Path(KIT,'state.json').read_text()).get('phase') in ('initialized','ready')
            print(json.dumps({'status':'PASS','database':'lexiflow','schema':'lexiflow_release','fingerprint':'f'*64,
              'needsRebuild':not committed,'reason':'OK' if committed else 'SCHEMA_MISSING',
              'generationVersion':Path(CALLS).with_name('generation-version').read_text() if committed else None,
              'datasetVersion':1 if committed else None}))
    else:
        data=Path(next(x.split(':/data')[0] for x in args if x.endswith(':/data')),'ecdict-source.ABC123');data.mkdir(parents=True,exist_ok=True)
        csv='word,translation,oxford,tag,bnc,frq,exchange\\ntest,测试,1,,1,1,\\n';(data/'stardict.csv').write_text(csv)
        digest=hashlib.sha256(csv.encode()).hexdigest();lock=json.loads(Path(REPO,'ops/dataset/ecdict-source.lock.json').read_text())
        upstream={'repository_url':lock['repository_url'],'commit':lock['commit'],'files':lock['files'],'archive_member':{'sha256':lock['archive_member']['sha256'],'bytes':lock['archive_member']['bytes']}}
        script="import {dataGeneration} from './ops/dataset/generation-version.mjs'; console.log(JSON.stringify(dataGeneration(JSON.parse(process.argv[1]))))"
        codeVersion=next(x.split('=',1)[1] for x in args if x.startswith('LEXIFLOW_GENERATION_CODE_VERSION='))
        generation=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps({'codeVersion':codeVersion,'inputSha256':digest,'upstream':upstream})],cwd=REPO,text=True))
        (data/'source-summary.json').write_text(json.dumps({'csv_sha256':digest,'generation':generation}))
        Path(CALLS).with_name('generation-version').write_text(generation['generationVersion'])
        print('dataset_dir=/data/ecdict-source.ABC123')
elif args[:1]==['build']:
    labels={args[index+1].split('=',1)[0]:args[index+1].split('=',1)[1] for index,arg in enumerate(args[:-1]) if arg=='--label'}
    if labels and mode=='api-build-fail': sys.exit(9)
    tag=args[args.index('-t')+1]
    image_id='sha256:'+hashlib.sha256((tag+json.dumps(labels,sort_keys=True)).encode()).hexdigest()
    entry={'id':image_id,'labels':labels}
    images[tag]=entry; images[image_id]=entry
    images_file.write_text(json.dumps(images))
    print('')
elif args[:1]==['compose'] and 'up' in args and args[-1]=='api':
    env=Path(KIT,'release.env').read_text()
    image_id=next(line.split('=',1)[1] for line in env.splitlines() if line.startswith('LEXIFLOW_API_IMAGE='))
    if mode=='api-start-fail':
        Path(MODE).write_text(''); sys.exit(8)
    if mode=='reuse-container-once':
        Path(MODE).write_text(''); sys.exit(0)
    previous=json.loads(runtime_file.read_text()) if runtime_file.exists() else None
    if previous and previous['id']==image_id and '--force-recreate' not in args: sys.exit(0)
    generation=(previous or {}).get('generation',0)+1
    current={**images[image_id],'generation':generation,'containerId':hashlib.sha256((image_id+str(generation)).encode()).hexdigest()}
    runtime_file.write_text(json.dumps(current))
elif args[:1]==['compose'] and 'logs' in args:
    import os, time
    assert '--follow' in args and '--tail=0' in args
    print('synthetic-caption-diagnostic',flush=True)
    if mode=='logs-fail': sys.exit(9)
    if mode=='logs-follow':
        Path(CALLS).with_name('logs-pid').write_text(str(os.getpid()))
        time.sleep(.3)
        print('synthetic-follow-stderr',file=sys.stderr,flush=True)
        while True: time.sleep(1)
elif args[:1]==['compose'] and 'initialize' in args and mode=='init-fail': sys.exit(8)
'''.replace("PYTHON", os.sys.executable).replace("CALLS", repr(str(self.calls))).replace("MODE", repr(str(self.mode))).replace("KIT", repr(str(self.kit))).replace("REPO", repr(str(self.repo)))
        self.program(self.bin / "podman", fake)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                runtime = json.loads((self.server.kit.parent / 'runtime.json').read_text())
                current_version = runtime['labels']['org.opencontainers.image.version']
                payload = {"status": "UP"} if self.path.endswith("readiness") else {"mode": "formal", "ready": True, "reason": "OK", "softwareVersion": current_version, "apiContract": "caption-hints.v2", "datasetVersion": 1}
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(payload).encode())
            def log_message(self, *_args):
                pass
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.repo = self.repo
        self.server.kit = self.kit
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
                self.server.repo = self.repo
                self.server.kit = self.kit
                self.server.timeout = .1
                while not self.finished.is_set():
                    self.server.handle_request()
            self.thread=threading.Thread(target=later, daemon=True);self.thread.start()
        elif not self.thread:
            self.server.server_close()
        argv = [shutil.which('node'), '--import', str(self.preload), str(self.repo/'ops/podman/local.mjs'), action, '--dir', str(self.kit)]
        if action == 'install': argv += ['--api-port', str(self.api_port), '--db-port', str(self.db_port)]
        env = {**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}
        if action in ('install', 'upgrade') and not any(value in extra for value in ('--plan', '--confirm-plan')):
            planned = subprocess.run(argv+list(extra)+['--plan'], env=env, capture_output=True, text=True, timeout=35)
            if planned.returncode: return planned
            rows = [json.loads(line) for line in planned.stdout.splitlines() if line.startswith('{') and 'planHash' in line]
            self.assertEqual(len(rows), 1, planned.stdout)
            return subprocess.run(argv+list(extra)+['--confirm-plan', rows[0]['planHash']], env=env, capture_output=True, text=True, timeout=35)
        return subprocess.run(argv+list(extra), env=env, capture_output=True, text=True, timeout=35)

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
        self.assertEqual((self.kit/'secrets/app-password').stat().st_mode & 0o777,0o600)
        self.assertIn(state['apiImage'],(self.kit/'release.env').read_text())
        for action in ['stop','up','install','status']:
            result=self.invoke(action); self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('--deployment-data' in call and 'apply' in call for call in calls),1)
        self.assertFalse(any('initialize' in call for call in calls))
        self.assertFalse(any('prune' in call or '-v' in call and 'down' in call for call in calls))

    def test_external_runlocal_uses_only_app_secret_and_recovers_read_only(self):
        # 已安装的外部目标只使用状态中记录的用户与本安装私密密码，不开放 CLI 覆盖。
        result=self.invoke('install',serve=True); self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        state['databaseTarget']={'kind':'external','host':'127.0.0.1','port':self.db_port,'user':'postgres'}
        state['digests'].pop('secrets/postgres-password')
        (self.kit/'state.json').write_text(json.dumps(state))
        self.mode.write_text('external')
        credential=self.kit/'secrets/app-password'
        result=self.invoke('upgrade','--plan')
        self.assertEqual(result.returncode,0,result.stderr)
        plan=next(json.loads(line)['plan'] for line in result.stdout.splitlines() if line.startswith('{'))
        self.assertEqual(plan['database']['target']['kind'],'external')
        self.assertFalse(plan['database']['create'])
        self.assertEqual(state['databaseTarget']['kind'],'external')
        self.assertNotIn('secrets/postgres-password',state['digests'])
        self.assertTrue(credential.exists())
        before=(self.kit/'state.json').read_bytes(); calls=self.calls.read_text()
        result=self.invoke('recover'); self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.kit/'state.json').read_bytes(),before)
        new=[json.loads(line) for line in self.calls.read_text()[len(calls):].splitlines()]
        self.assertFalse(any('apply' in row or 'up' in row or 'stop' in row for row in new))
        for flag in ('--db-user','--db-password-file'):
            rejected=self.invoke('upgrade',flag,'postgres','--plan')
            self.assertNotEqual(rejected.returncode,0)
        offset=len(self.calls.read_text().splitlines())
        stopped=self.invoke('stop'); self.assertEqual(stopped.returncode,0,stopped.stderr)
        started=self.invoke('up'); self.assertEqual(started.returncode,0,started.stderr)
        actions=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any(row[:1]==['compose'] and row[-1]=='postgres' for row in actions))

    def test_caption_debug_is_default_and_legacy_flag_only_warns(self):
        result=self.invoke('install','--caption-debug')
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.kit/'state.json').exists())
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        self.assertFalse(state.get('captionDebug'))
        self.assertNotIn('caption-debug.yaml',state['digests'])
        result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any('caption-debug.yaml' in call for call in calls))

    def test_repeat_install_uses_same_executor_and_preserves_data(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        old=json.loads((self.kit/'state.json').read_text())
        # Change a tracked source input to obtain a real resolver-derived target identity.
        (self.repo/'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
        result=self.invoke('install');self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('"plan":',result.stdout)
        after=json.loads((self.kit/'state.json').read_text())
        self.assertNotEqual(after['buildIdentity'],old['buildIdentity'])
        self.assertEqual(after['datasetIdentity'],old['datasetIdentity'])
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('--deployment-data' in row and 'apply' in row for row in calls),1)

    def test_upgrade_cli_uses_api_only_and_never_initializes_again(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        marker=self.kit/'data/synthetic-marker';marker.write_text('preserve')
        before=json.loads((self.kit/'state.json').read_text())
        old_container=json.loads((self.root/'runtime.json').read_text())['containerId']
        (self.repo/'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
        first_upgrade_call = len(self.calls.read_text().splitlines())
        self.mode.write_text('')
        result=self.invoke('upgrade');self.assertEqual(result.returncode,0,result.stderr)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        calls = calls[first_upgrade_call:]
        self.assertIn('构建 API 镜像', result.stdout)
        self.assertNotIn('构建词库工具镜像', result.stdout)
        compose=[row for row in calls if row[:1]==['compose']]
        self.assertTrue(any('up' in row and '--no-deps' in row and '--force-recreate' in row and row[-1]=='api' for row in compose),compose)
        self.assertFalse(any('initialize' in row for row in calls))
        self.assertFalse(any('up' in row and row[-1]=='postgres' for row in compose))
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual((after['id'],after['project'],after['apiPort'],after['dbPort']),(before['id'],before['project'],before['apiPort'],before['dbPort']))
        self.assertEqual(after['postgresImage'],before['postgresImage']);self.assertEqual(marker.read_text(),'preserve')
        self.assertNotEqual(after['apiImage'],before['apiImage'])
        self.assertEqual(json.loads((self.kit/'extension/build-identity.json').read_text()),after['buildIdentity'])
        self.assertEqual(after['lastOperation']['buildId'],after['buildIdentity']['buildId'])
        self.assertEqual(after['datasetIdentity'],before['datasetIdentity'])
        new_container=json.loads((self.root/'runtime.json').read_text())['containerId']
        self.assertNotEqual(old_container,new_container)
        noop_offset=len(self.calls.read_text().splitlines())
        result=self.invoke('upgrade');self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('"plan":',result.stdout)
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['containerId'],new_container)
        self.assertFalse(any('--force-recreate' in json.loads(line) for line in self.calls.read_text().splitlines()[noop_offset:]))

    def test_install_and_upgrade_share_explicit_java_home(self):
        home = self.root / "jdk 25"
        (home / "bin").mkdir(parents=True)
        self.program(home / "bin/java", (self.bin / 'java').read_text().replace('25.0.1','25.0.4'))
        (home / 'release').write_text('JAVA_VERSION="25.0.4"\nIMPLEMENTOR="Eclipse Adoptium"\nIMPLEMENTOR_VERSION="Temurin-25.0.4+7"\n')
        self.program(self.bin / "java", '#!/bin/sh\necho \'openjdk version "26.0.2"\' >&2\n')
        gradle = self.repo / "backend/gradlew"
        gradle.write_text(gradle.read_text().replace("set -eu\n", 'set -eu\n[ "$(command -v java)" = "$JAVA_HOME/bin/java" ]\n'))
        with patch.dict(os.environ, {"LEXIFLOW_JAVA_HOME": str(home)}):
            result = self.invoke('install', serve=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = json.loads((self.kit / 'state.json').read_text())
            (self.repo / 'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
            result = self.invoke('upgrade')
            self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads((self.kit / 'state.json').read_text())
        self.assertNotEqual(before['apiImage'], after['apiImage'])
        self.assertEqual(before['postgresImage'], after['postgresImage'])
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('--deployment-data' in call and 'apply' in call for call in calls), 1)

    def test_invalid_explicit_java_is_classified_without_raw_path(self):
        private = self.root / 'private-secret-java'
        with patch.dict(os.environ, {'LEXIFLOW_JAVA_HOME': str(private)}):
            result = self.invoke('install', '--plan')
        self.assertEqual(result.returncode, 1)
        self.assertIn('JAVA_RELEASE_INVALID', result.stderr)
        self.assertNotIn(str(private), result.stdout + result.stderr)
        self.assertFalse((self.kit / 'state.json').exists())

    def test_upgrade_rejects_successful_compose_that_reuses_old_api_container(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        before=json.loads((self.kit/'state.json').read_text())
        extension=(self.kit/'extension/build-identity.json').read_bytes()
        (self.repo/'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
        self.mode.write_text('reuse-container-once')
        offset=len(self.calls.read_text().splitlines())
        result=self.invoke('upgrade')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('API 响应协议或软件版本不匹配',result.stderr)
        self.assertNotIn('部署完成',result.stdout)
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(after['phase'],'initialized')
        self.assertNotEqual(after['apiImage'],before['apiImage'])
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],before['apiImage'])
        self.assertEqual(json.loads((self.kit/'deployment.json').read_text())['stage'],'application-activated')
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        activations=[row for row in calls if row[:1]==['compose'] and 'up' in row]
        self.assertEqual(len(activations),1)
        self.assertTrue(all('--force-recreate' in row and '--no-deps' in row and row[-1]=='api' for row in activations))
        self.assertFalse(any('initialize' in row for row in calls))
        result=self.invoke('recover');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'ready')
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],after['apiImage'])

    def test_upgrade_build_and_start_failures_restore_then_retry(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        before=json.loads((self.kit/'state.json').read_text())
        original_extension=(self.kit/'extension/build-identity.json').read_bytes()
        (self.repo/'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
        self.mode.write_text('api-build-fail')
        offset=len(self.calls.read_text().splitlines())
        result=self.invoke('upgrade');self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text()),before)
        self.assertEqual((self.kit/'extension/build-identity.json').read_bytes(),original_extension)
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],before['apiImage'])
        self.assertFalse((self.kit/'.lock').exists())
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('stop' in row or 'initialize' in row for row in calls))
        self.mode.write_text('api-start-fail')
        result=self.invoke('upgrade');self.assertNotEqual(result.returncode,0)
        pending=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(pending['phase'],'initialized')
        self.assertNotEqual(pending['apiImage'],before['apiImage'])
        self.assertEqual(json.loads((self.kit/'deployment.json').read_text())['stage'],'data-committed')
        self.assertFalse((self.kit/'.lock').exists())
        self.mode.write_text('')
        result=self.invoke('recover');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'ready')
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],pending['apiImage'])

    def test_logs_display_does_not_copy_caption_body_to_operation_file(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        result=self.invoke('logs');self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('synthetic-caption-diagnostic',result.stdout)
        for file in self.kit.glob('operation-*.log'):
            self.assertNotIn('synthetic-caption-diagnostic',file.read_text())

    def test_logs_are_read_only_even_with_lock_and_pending_transaction(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        before={str(p.relative_to(self.kit)):p.read_bytes() for p in self.kit.rglob('*') if p.is_file()}
        (self.kit/'.lock').mkdir()
        (self.kit/'.lock/owner.json').write_text('synthetic-live-owner')
        (self.kit/'upgrade.json').write_text('synthetic-pending-transaction')
        offset=len(self.calls.read_text().splitlines())
        result=self.invoke('logs');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.kit/'.lock/owner.json').read_text(),'synthetic-live-owner')
        self.assertEqual((self.kit/'upgrade.json').read_text(),'synthetic-pending-transaction')
        after={str(p.relative_to(self.kit)):p.read_bytes() for p in self.kit.rglob('*') if p.is_file() and p.name not in ['owner.json','upgrade.json']}
        self.assertEqual(before,after)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('up' in row or 'stop' in row or 'build' in row for row in calls))

    def test_logs_stream_before_exit_and_cancel_only_the_viewer(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        self.mode.write_text('logs-follow')
        offset=len(self.calls.read_text().splitlines())
        operations=set(self.kit.glob('operation-*.log'))
        state=(self.kit/'state.json').read_bytes()
        output=self.root/'follow-output'
        with output.open('w') as out:
            child=subprocess.Popen([shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/local.mjs'),'logs','--dir',str(self.kit)],env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']},stdout=out,stderr=out)
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and 'synthetic-follow-stderr' not in output.read_text():
                    if child.poll() is not None: break
                    time.sleep(.02)
                self.assertIn('synthetic-caption-diagnostic',output.read_text())
                self.assertIn('synthetic-follow-stderr',output.read_text())
                self.assertIsNone(child.poll(),'logs must remain active after delivering output')
                self.assertFalse((self.kit/'.lock').exists())
                child.send_signal(signal.SIGINT)
                self.assertEqual(child.wait(timeout=8),0,output.read_text())
            finally:
                if child.poll() is None:
                    child.send_signal(signal.SIGTERM)
                    child.wait(timeout=8)
        self.assertEqual(state,(self.kit/'state.json').read_bytes())
        self.assertEqual(operations,set(self.kit.glob('operation-*.log')))
        self.assertNotIn('失败',output.read_text())
        with self.assertRaises(ProcessLookupError): os.killpg(int((self.root/'logs-pid').read_text()),0)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('stop' in row or 'up' in row for row in calls))

    def test_logs_provider_failure_is_not_success(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        self.mode.write_text('logs-fail')
        result=self.invoke('logs')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('退出码 9',result.stderr)

    def test_fetch_failure_can_retry_before_database(self):
        self.mode.write_text('fetch-fail')
        result=self.invoke('install'); self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.kit/'state.json').exists())
        self.assertFalse((self.kit/'secrets/app-password').exists())
        self.mode.write_text('')
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'ready')

    def test_incomplete_install_rejects_changed_identity_resolver(self):
        result = self.invoke('install','--plan')
        self.assertEqual(result.returncode,0,result.stderr)
        old_hash=next(json.loads(line)['planHash'] for line in result.stdout.splitlines() if line.startswith('{'))
        self.assertFalse((self.kit/'state.json').exists())
        with (self.repo/'ops/release/version.mjs').open('a') as stream:
            stream.write('\n// synthetic source identity dependency change\n')
        offset = len(self.calls.read_text().splitlines())
        result = self.invoke('install','--confirm-plan',old_hash)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DEPLOYMENT_PLAN_CHANGED', result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('initialize' in row or 'stop' in row for row in calls))
        self.assertFalse((self.kit/'state.json').exists())
        self.assertFalse((self.kit/'secrets/app-password').exists())

    def test_up_does_not_repair_tampered_network_or_reimport(self):
        result = self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)
        compose = self.kit/'compose.yaml'
        before=compose.read_bytes(); state=(self.kit/'state.json').read_bytes()
        compose.write_bytes(before+b'\nunknown-network: true\n')
        offset=len(self.calls.read_text().splitlines())
        result = self.invoke('up')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(compose.read_bytes(),before+b'\nunknown-network: true\n')
        self.assertEqual((self.kit/'state.json').read_bytes(),state)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('apply' in row or 'down' in row or 'prune' in row for row in calls))

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
        self.mode.write_text('data-apply-fail')
        result=self.invoke('install'); self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'new')
        self.assertEqual(json.loads((self.kit/'deployment.json').read_text())['stage'],'application-stopped')
        self.mode.write_text('')
        result=self.invoke('up');self.assertNotEqual(result.returncode,0)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('--deployment-data' in call and 'apply' in call for call in calls),1)

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
        result=self.invoke('install',serve=True); self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        self.mode.write_text('owned-pg')
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1',state['dbPort']));listener.listen()
            result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['datasetIdentity'],state['datasetIdentity'])

    def test_schema_and_csv_tampering_block_initialization(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        for file in [self.kit/'infra/postgres/schema.sql',Path(state['dataset'])/'stardict.csv']:
            before=file.read_bytes();file.write_bytes(before+b'changed')
            calls=self.calls.read_text()
            result=self.invoke('upgrade','--plan');self.assertNotEqual(result.returncode,0)
            self.assertNotIn('"apply"',self.calls.read_text()[len(calls):])
            file.write_bytes(before)

    def test_cancel_during_image_pull_releases_lock_and_retries(self):
        self.server.server_close()
        planned=self.invoke('install','--plan');self.assertEqual(planned.returncode,0,planned.stderr)
        plan_hash=next(json.loads(line)['planHash'] for line in planned.stdout.splitlines() if line.startswith('{'))
        self.mode.write_text('slow-pull')
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/local.mjs'),'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port),'--confirm-plan',plan_hash]
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
            self.assertTrue('收到中断请求' in err or '安装已取消' in err,err)
            self.assertFalse((self.kit/'.lock').exists())
            self.assertNotIn('private-download-output',out+err)
        finally:
            if proc.poll() is None: proc.kill();proc.communicate()
        self.mode.write_text('')
        result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_legacy_empty_lock_requires_confirmation_and_preserves_data(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        secret=(self.kit/'secrets/app-password').read_bytes()
        (self.kit/'.lock').mkdir()
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        result=self.invoke('recover');self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.kit/'.lock').exists())
        result=self.invoke('recover','--confirm-stopped');self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.kit/'.lock').exists())
        self.assertEqual(secret,(self.kit/'secrets/app-password').read_bytes())
        result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_active_lock_cannot_be_recovered_or_removed(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':os.getpid(),'token':'a'*32,'childPid':None}
        text=json.dumps(owner,separators=(',',':'))
        (self.kit/'.lock/owner.json').write_text(text)
        result=self.invoke('recover','--confirm-stopped');self.assertNotEqual(result.returncode,0)
        self.assertEqual(text,(self.kit/'.lock/owner.json').read_text())

    def test_existing_claim_refuses_recovery_without_touching_locks(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        (self.kit/'.lock').mkdir()
        (self.kit/'.lock-claim').mkdir()
        result=self.invoke('recover','--confirm-stopped')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.kit/'.lock').is_dir())
        self.assertTrue((self.kit/'.lock-claim').is_dir())

    def test_dead_owned_lock_is_reclaimed_without_confirmation(self):
        result=self.invoke('install',serve=True);self.assertEqual(result.returncode,0,result.stderr)
        old=subprocess.Popen([shutil.which('node'),'-e','']);old.wait()
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':old.pid,'token':'b'*32,'childPid':None}
        (self.kit/'.lock/owner.json').write_text(json.dumps(owner,separators=(',',':')))
        result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_sigkill_lock_windows_retry_without_permanent_claim(self):
        result = self.invoke('install', serve=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = (self.kit/'state.json').read_bytes()
        secret = (self.kit/'secrets/app-password').read_bytes()
        guard_inode = (self.kit/'.lock-guard').stat().st_ino
        crash = self.root/'lock-crash.mjs'
        marker = self.root/'lock-crash.marker'
        for phase in ['guard', 'prepare-write', 'publish', 'owner-write', 'owner', 'retire']:
            with self.subTest(phase=phase):
                marker.unlink(missing_ok=True)
                crash.write_text("import fs from 'node:fs'; import cp from 'node:child_process'; import {syncBuiltinESMExports} from 'node:module';\n"
                    + f"const phase={json.dumps(phase)}, marker={json.dumps(str(marker))};\n"
                    + r"""
const write=fs.writeFileSync.bind(fs);
const die=()=>{write(marker,phase);process.kill(process.pid,'SIGKILL');};
const originalSpawn=cp.spawnSync.bind(cp);
cp.spawnSync=(command,args,...rest)=>{const result=originalSpawn(command,args,...rest);if(phase==='guard' && ['/usr/bin/lockf','/usr/bin/flock'].includes(command) && result.status===0)die();return result;};
const opened=new Map(); const originalOpen=fs.openSync.bind(fs);
fs.openSync=(file,...rest)=>{const fd=originalOpen(file,...rest);opened.set(fd,String(file));return fd;};
fs.writeFileSync=(file,...rest)=>{const name=opened.get(file)||'';
if((phase==='prepare-write' && name.includes('/.lock-prepared-')) || (phase==='owner-write' && name.includes('/.lock-owner-'))){write(file,'{partial');die();}
return write(file,...rest);};
const rename=fs.renameSync.bind(fs);
fs.renameSync=(from,to)=>{const result=rename(from,to);from=String(from);to=String(to);
if(phase==='publish' && from.includes('/.lock-prepared-') && to.endsWith('/.lock'))die();
if(phase==='owner' && from.includes('/.lock-owner-') && to.endsWith('/.lock/owner.json'))die();
if(phase==='retire' && from.endsWith('/.lock') && to.includes('/.lock-retired-'))die();
return result;};
syncBuiltinESMExports();
""")
                argv = [shutil.which('node'), '--import', str(self.preload), '--import', str(crash), str(self.repo/'ops/podman/local.mjs'), 'up', '--dir', str(self.kit)]
                proc = subprocess.run(argv, env={**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}, capture_output=True, text=True, timeout=15)
                self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr)
                self.assertEqual(marker.read_text(), phase)
                if (self.kit/'.lock/owner.json').exists():
                    owner = json.loads((self.kit/'.lock/owner.json').read_text())
                    self.assertEqual(owner['root'], str(self.kit))
                deadline = time.monotonic() + 10
                while True:
                    result = self.invoke('up')
                    if result.returncode == 0 or time.monotonic() >= deadline:
                        break
                    self.assertIn('仍在运行', result.stderr)
                    time.sleep(.1)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.kit/'.lock').exists())
                self.assertFalse((self.kit/'.lock-claim').exists())
                self.assertEqual((self.kit/'.lock-guard').stat().st_ino, guard_inode)
                self.assertEqual((self.kit/'state.json').read_bytes(), before)
                self.assertEqual((self.kit/'secrets/app-password').read_bytes(), secret)

    def test_symlink_directory_is_rejected(self):
        real=self.root/'real';real.mkdir();self.kit.symlink_to(real)
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        self.assertEqual(list(real.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
