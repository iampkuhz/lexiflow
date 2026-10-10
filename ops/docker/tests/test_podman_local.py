"""验证本机部署入口的编排和失败边界；替身不能证明真实容器成功。"""
import contextlib
import hashlib
import http.server
import json
import os
import re
import pty
from pathlib import Path
import shutil
import socket
import signal
import select
import time
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


class LocalEntryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix="lexiflow source template ")
        cls.addClassCleanup(temp.cleanup)
        cls._source_template = Path(temp.name).resolve() / "source"
        cls._prepare_source(cls._source_template)
        snapshot = cls._source_snapshot(cls._source_template)

        def verify_template():
            try:
                if cls._source_snapshot(cls._source_template) != snapshot:
                    raise AssertionError("shared source template was mutated")
            finally:
                del cls._source_template

        # 先核不可变 source，再删除本进程模板；不缓存安装/provider 状态。
        cls.addClassCleanup(verify_template)

    @staticmethod
    def _source_snapshot(root):
        return {
            str(path.relative_to(root)): (path.stat().st_mode, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()
        }

    @staticmethod
    def _prepare_source(repo):
        for relative in ["ops/podman/commands/manage-podman-installation.mjs", "ops/podman/installation/installation-upgrade-workflow.mjs", "ops/podman/installation/installation-plan-records.mjs", "ops/podman/environment/local-database-target.mjs", "ops/podman/candidates/candidate-artifact-installer.mjs", "ops/podman/images/versioned-images.mjs", "ops/podman/images/image-slot-lock.mjs", "ops/podman/environment/java-build-environment.mjs", "ops/podman/execution/installation-operation-lock.mjs", "ops/podman/execution/deployment-process-control.mjs", "ops/podman/commands/diagnose-podman-installation.mjs", "ops/podman/installation/installation-update-transaction.mjs", "ops/podman/installation/installation-workspace-journal.mjs", "ops/podman/installation/installation-version-summary.mjs", "ops/podman/compose.validation.yaml",
                         "ops/dataset/prepare_ecdict_bundle.py", "ops/podman/source-tools.Containerfile",
                         "ops/docker/Dockerfile", "ops/docker/Dockerfile.postgres",
                         "ops/docker/run-container-command.sh", "ops/docker/initialize-postgres-app-role.sh",
                         "ops/dataset/ecdict-source.lock.json", "ops/dataset/generation-inputs.json", "ops/dataset/dataset-generation-identity.mjs",
                         "infra/postgres/schema.sql", "ops/release/version.txt", "ops/release/integrity/source-build-identity.mjs", "ops/release/integrity/extension-archive-integrity.mjs"]:
            dst = repo / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, dst)
        (repo / 'fixture.txt').write_text('stable source fixture\n')
        (repo / 'ops/dataset/generation-inputs.json').write_text(json.dumps({'schemaVersion': 1, 'include': ['fixture.txt']}))
        # Build identity is calculated by the production resolver from Git-visible inputs.
        (repo / ".gitignore").write_text("tmp/\n.local/\nbackend/product/api/build/\nextension/dist/\nextension/node_modules/\n")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Synthetic Fixture"], check=True)
        # Resolver imports need the release module too; all resolver-visible files are committed as fixture source.
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)

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
        # 仅合成 source/.git 模板共享；每例复制为独立可变 checkout。
        # 手动构造的 standalone fixture 不依赖 unittest class 生命周期。
        template = getattr(type(self), '_source_template', None)
        if template is None:
            self._prepare_source(self.repo)
        else:
            shutil.copytree(template, self.repo)
        (self.repo / "backend/product/api/build/internal/boot").mkdir(parents=True)
        (self.repo / "extension/dist").mkdir(parents=True)
        self.program(self.repo / "backend/gradlew", "#!/bin/sh\nset -eu\nnode -e \"import('../ops/release/integrity/source-build-identity.mjs').then(({resolveBuildIdentity})=>{const fs=require('fs'); fs.mkdirSync('product/api/build/internal/boot',{recursive:true}); fs.writeFileSync('product/api/build/internal/boot/api-internal.jar','synthetic');})\"\n")
        self.program(self.bin / "npm", "#!/bin/sh\nset -eu\nif [ \"$1\" = run ] && [ \"$2\" = build ]; then node -e \"import('../ops/release/integrity/source-build-identity.mjs').then(({resolveBuildIdentity})=>{const fs=require('fs'); const id=resolveBuildIdentity('..'); const port=process.env.LEXIFLOW_API_PORT; fs.mkdirSync('dist',{recursive:true}); fs.writeFileSync('dist/build-identity.json',JSON.stringify(id)); fs.writeFileSync('dist/manifest.json',JSON.stringify({version:id.chromeVersion,version_name:id.softwareVersion,host_permissions:['http://127.0.0.1:'+port+'/*']}));})\"; fi\n")
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
        # 用 Node 标准测试预加载替换平台探测；产品本身无测试开关或平台绕过参数。
        preload = self.root / "platform.mjs"
        preload.write_text("import os from 'node:os'; os.platform=()=> 'darwin'; os.arch=()=> 'arm64';")
        self.preload = preload
        fake = '''#!PYTHON
import hashlib, json, sys, os
from pathlib import Path
args=sys.argv[1:]
images_file=Path(CALLS).with_name('images.json')
runtime_file=Path(CALLS).with_name('runtime.json')
pg_runtime_file=Path(CALLS).with_name('pg-runtime.json')
images=json.loads(images_file.read_text()) if images_file.exists() else {}
with Path(CALLS).open('a') as f: f.write(json.dumps(args)+'\\n')
mode=Path(MODE).read_text()
if args[:1]==['pull'] and mode=='slow-pull':
    import time
    print('private-download-output',flush=True);time.sleep(30)
elif args[:1]==['info']: print('{}')
elif args[:2]==['image','exists']: sys.exit(0 if args[2] in images else 1)
elif args[:1]==['tag']:
    images[args[2]]=images[args[1]]; images_file.write_text(json.dumps(images))
elif args[:1]==['rmi']:
    images.pop(args[-1],None); images_file.write_text(json.dumps(images))
elif args[:2]==['image','inspect']:
    image_key=args[2] if args[2]!='--format' else args[-1]
    entry=images.get(image_key)
    if '--format' in args and args[args.index('--format')+1]=='{{json .Labels}}':
        print(json.dumps(entry['labels'] if entry else {}))
    elif '--format' in args and args[args.index('--format')+1]=='json':
        print(json.dumps([{'Id':entry['id'],'Labels':entry['labels']}]) if entry else '[]')
    elif '--format' in args and args[args.index('--format')+1]=='{{json .Os}} {{json .Architecture}}':
        print('\"linux\" \"arm64\"')
    else: print(entry['id'] if entry else 'sha256:'+hashlib.sha256(args[-1].encode()).hexdigest())
elif args[:1]==['ps'] or args[:2] in [['volume','ls'],['network','ls']]:
    if '--filter' in args and any(value.startswith('ancestor=') for value in args):
        ancestor=args[args.index('--filter')+1].split('=',1)[1]
        rows=[json.loads(file.read_text()) for file in [runtime_file,pg_runtime_file] if file.exists()]
        print('\\n'.join(row['containerId'] for row in rows if row['id'].removeprefix('sha256:')==ancestor))
    elif mode=='foreign':
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'lexiflow.installation':'foreign'}}]))
    elif mode=='owned-pg' and '-a' in args:
        state=json.loads(Path(KIT,'state.json').read_text()); print(json.dumps([{'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':'postgres','lexiflow.installation':state['id']}}]))
    elif args[:1]==['ps']:
        state=json.loads(Path(KIT,'state.json').read_text())
        rows=[(role,json.loads(file.read_text())) for role,file in [('api',runtime_file),('postgres',pg_runtime_file)] if file.exists()]
        print(json.dumps([{'Id':item['containerId'],'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':role,'lexiflow.installation':state['id']}} for role,item in rows]))
    else: print('[]')
elif args[:1]==['inspect']:
    rows=[json.loads(file.read_text()) for file in [runtime_file,pg_runtime_file] if file.exists()]
    item=next((row for row in rows if row['containerId']==args[-1]),None)
    if item is None: sys.exit(1)
    print(item['id'])
elif args[:2]==['container','inspect']:
    state=json.loads(Path(KIT,'state.json').read_text())
    for role,file in [('api',runtime_file),('postgres',pg_runtime_file)]:
        if file.exists():
            item=json.loads(file.read_text())
            if item['containerId']==args[2]:
                print(json.dumps([{'Id':item['containerId'],'Image':item['id'],'State':{'Running':True},'Config':{'Labels':{'com.docker.compose.project':state['project'],'com.docker.compose.service':role,'lexiflow.installation':state['id']}}}]))
                sys.exit(0)
    sys.exit(1)
elif args[:1] in (['stop'],['rm']):
    for file in [runtime_file,pg_runtime_file]:
        if file.exists() and json.loads(file.read_text())['containerId']==args[-1]:
            if args[0]=='rm': file.unlink()
            sys.exit(0)
    sys.exit(1)
elif args[:1]==['run']:
    if mode=='fetch-fail': sys.exit(9)
    if mode=='slow-source-run' and '/kit/ops/dataset/prepare_ecdict_bundle.py' in args:
        import time
        print('private-source-output',flush=True);time.sleep(30)
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
        script="import {dataGeneration} from './ops/dataset/dataset-generation-identity.mjs'; console.log(JSON.stringify(dataGeneration(JSON.parse(process.argv[1]))))"
        codeVersion=next(x.split('=',1)[1] for x in args if x.startswith('LEXIFLOW_GENERATION_CODE_VERSION='))
        import subprocess
        generation=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,json.dumps({'codeVersion':codeVersion,'inputSha256':digest,'upstream':upstream})],cwd=REPO,text=True))
        (data/'source-summary.json').write_text(json.dumps({'csv_sha256':digest,'generation':generation}))
        Path(CALLS).with_name('generation-version').write_text(generation['generationVersion'])
        print('dataset_dir=/data/ecdict-source.ABC123')
elif args[:1]==['build']:
    labels={args[index+1].split('=',1)[0]:args[index+1].split('=',1)[1] for index,arg in enumerate(args[:-1]) if arg=='--label'}
    if labels.get('io.lexiflow.image-role')=='api' and mode=='api-build-fail': sys.exit(9)
    if labels.get('io.lexiflow.image-role')=='postgres' and mode=='pg-build-fail': sys.exit(9)
    tag=args[args.index('-t')+1] if '-t' in args else 'untagged:'+hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()
    image_id='sha256:'+hashlib.sha256((tag+json.dumps(labels,sort_keys=True)).encode()).hexdigest()
    entry={'id':image_id,'labels':labels}
    if '-t' in args: images[tag]=entry
    images[image_id]=entry
    if '--iidfile' in args: Path(args[args.index('--iidfile')+1]).write_text(image_id)
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
elif args[:1]==['compose'] and 'up' in args and args[-1]=='postgres':
    env=Path(KIT,'release.env').read_text()
    image_id=next(line.split('=',1)[1] for line in env.splitlines() if line.startswith('LEXIFLOW_POSTGRES_IMAGE='))
    previous=json.loads(pg_runtime_file.read_text()) if pg_runtime_file.exists() else None
    generation=(previous or {}).get('generation',0)+1
    pg_runtime_file.write_text(json.dumps({**images[image_id],'generation':generation,'containerId':hashlib.sha256((image_id+str(generation)).encode()).hexdigest()}))
elif args[:1]==['compose'] and 'config' in args and mode=='config-fail-once':
    Path(MODE).write_text(''); sys.exit(8)
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
        self.fake_transport = None
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
        # 替身 HTTP 仍占宿主端口；当 fake Podman 已精确移除 API 容器时，
        # portTaken 应观察容器真实释放后的语义，而非夹具 socket 的假占用。
        with preload.open('a') as output:
            output.write(f'''
import fs from 'node:fs'; import net from 'node:net';
const originalCreateServer=net.createServer;
net.createServer=(...args)=>{{
  const server=originalCreateServer(...args), originalListen=server.listen.bind(server), originalClose=server.close.bind(server);
  let syntheticFree=false;
  server.listen=(port,host,callback)=>{{
    if(port==={self.db_port} && fs.existsSync({json.dumps(str(self.root / 'pg-runtime.json'))})){{
      setImmediate(()=>server.emit('error',Object.assign(new Error('synthetic managed PG listener'),{{code:'EADDRINUSE'}})));
      return server;
    }}
    if(port==={self.api_port} && !fs.existsSync({json.dumps(str(self.root / 'runtime.json'))})){{syntheticFree=true;setImmediate(callback);return server;}}
    return originalListen(port,host,callback);
  }};
  server.close=callback=>syntheticFree ? setImmediate(callback) : originalClose(callback);
  return server;
}};
''')
        # install 检查空闲端口后才启动替身HTTP；run()在后台等待写入初始化状态。
        self.thread = None
        self.finished = threading.Event()
        from podman_fake_transport import FINITE_TEST_CASES, PodmanFakeTransport
        case_name = self.id().rsplit('.', 1)[-1]
        if case_name in FINITE_TEST_CASES:
            self.fake_transport = PodmanFakeTransport(self.bin / 'podman', self.bin, self.root, case_name)
            self.fake_transport.start()

    @staticmethod
    def program(file, content):
        file.write_text(content); file.chmod(0o755)

    @staticmethod
    def assert_logs_fixture_contract(state, kit):
        required = {
            'schema': int, 'id': str, 'project': str, 'root': str, 'apiPort': int, 'dbPort': int,
            'phase': str, 'version': str, 'buildIdentity': dict, 'databaseTarget': dict,
            'packageType': str, 'apiImage': str, 'postgresImage': str, 'inputType': str,
            'inputSha256': str, 'generation': dict, 'generationVersion': str, 'dataset': str,
            'datasetIdentity': dict, 'digests': dict,
        }
        for name, expected in required.items():
            if name not in state or type(state[name]) is not expected:
                raise AssertionError(f'fixture state contract mismatch: {name}')
        if (state['schema'] != 1 or state['phase'] != 'ready' or state['root'] != str(kit.resolve())
                or state['project'] != f"lexiflow-local-{state['id']}"
                or len(state['id']) != 32 or any(c not in '0123456789abcdef' for c in state['id'])
                or state['apiPort'] == state['dbPort']
                or state['version'] != state['buildIdentity'].get('softwareVersion')
                or state['generationVersion'] != state['generation'].get('generationVersion')
                or state['datasetIdentity'].get('generationVersion') != state['generationVersion']
                or state['datasetIdentity'].get('sourceSha256') != state['inputSha256']
                or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', state['apiImage'])
                or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', state['postgresImage'])
                or not state['dataset'].startswith(str((kit / 'data').resolve()) + os.sep)
                or not set(['compose.yaml', 'release.env', 'secrets/postgres-password',
                            'secrets/app-password', 'infra/postgres/schema.sql',
                            str((Path(state['dataset']) / 'stardict.csv').relative_to(kit))]).issubset(state['digests'])):
            raise AssertionError('fixture state invariant mismatch')
        for relative, digest in state['digests'].items():
            file = kit / relative
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise AssertionError(f'fixture digest mismatch: {relative}')
        if any(not isinstance(value, str) or len(value) != 64
               or any(c not in '0123456789abcdef' for c in value) for value in state['digests'].values()):
            raise AssertionError('fixture digest type mismatch')
        env = (kit / 'release.env').read_text()
        for value in (state['apiImage'], state['postgresImage'], state['id'], f'local-{state["id"]}'):
            if value not in env:
                raise AssertionError('fixture release environment identity mismatch')
        for name in ('secrets/app-password', 'secrets/postgres-password'):
            if (kit / name).stat().st_mode & 0o777 != 0o600:
                raise AssertionError(f'fixture secret permissions mismatch: {name}')

    def assert_ready_fixture_contract(self, state, kit, images, runtime, pg_runtime, container_rows, expected_compose):
        """验证ready状态的字段关系与fake provider资源归属，不推断安装历史。"""
        if (not 1 <= state['apiPort'] <= 65535 or not 1 <= state['dbPort'] <= 65535
                or state['apiPort'] == state['dbPort']
                or state['project'] != f"lexiflow-local-{state['id']}"):
            raise AssertionError('ready fixture port or installation relation mismatch')
        if state['databaseTarget'].get('port') != state['dbPort']:
            raise AssertionError('ready fixture database target port mismatch')
        dataset_relative = str(Path(state['dataset']).resolve().relative_to(kit.resolve()) / 'stardict.csv')
        if set(state['digests']) != {'compose.yaml', 'release.env', 'secrets/postgres-password',
                                     'secrets/app-password', 'infra/postgres/schema.sql', dataset_relative}:
            raise AssertionError(f'ready fixture digest key set mismatch: {sorted(state["digests"])} / {dataset_relative}')
        for relative, digest in state['digests'].items():
            path = kit / relative
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise AssertionError(f'ready fixture digest mismatch: {relative}')
        for relative in ('secrets/app-password', 'secrets/postgres-password'):
            secret = kit / relative
            if not secret.read_bytes() or secret.stat().st_mode & 0o777 != 0o600:
                raise AssertionError(f'ready fixture secret contract mismatch: {relative}')
        if Path(state['dataset']).name != f"ecdict-source.{state['inputSha256'][:16]}":
            raise AssertionError('ready fixture data directory identity mismatch')
        summary = json.loads((Path(state['dataset']) / 'source-summary.json').read_text())
        if (set(summary) != {'upstream', 'csv_sha256', 'generation'}
                or summary != {'upstream': state['generation']['upstream'], 'csv_sha256': state['inputSha256'],
                               'generation': state['generation']}):
            raise AssertionError('ready fixture source summary mismatch')
        manifest = json.loads((kit / 'extension/manifest.json').read_text())
        embedded = json.loads((kit / 'extension/build-identity.json').read_text())
        if (embedded != state['buildIdentity'] or manifest.get('version_name') != state['version']
                or manifest.get('version') != state['buildIdentity']['chromeVersion']
                or manifest.get('host_permissions') != [f"http://127.0.0.1:{state['apiPort']}/*"]):
            raise AssertionError('ready fixture extension identity mismatch')
        jar = Path(state['apiJar']).resolve()
        repo_jar = self.repo / 'backend/product/api/build/internal/boot/api-internal.jar'
        if not jar.is_file() or jar.name != 'lexiflow-api.jar':
            raise AssertionError('ready fixture jar path mismatch')
        if jar.read_bytes() != repo_jar.read_bytes():
            raise AssertionError('ready fixture jar content mismatch')
        if jar.stat().st_mode & 0o777 != (self.repo / 'backend/product/api/build/internal/boot/api-internal.jar').stat().st_mode & 0o777:
            raise AssertionError('ready fixture jar permissions mismatch')
        if hashlib.sha256(jar.read_bytes()).hexdigest() != state['artifacts'].get('jar'):
            raise AssertionError('ready fixture jar identity mismatch')
        for relative in ('infra/postgres/schema.sql',):
            if (kit / relative).read_bytes() != (self.repo / relative).read_bytes() or (kit / relative).stat().st_mode & 0o777 != (self.repo / relative).stat().st_mode & 0o777:
                raise AssertionError(f'ready fixture stable file mismatch: {relative}')
        if (Path(state['dataset']) / 'stardict.csv').read_bytes() != b'word,translation,oxford,tag,bnc,frq,exchange\ntest,\xe6\xb5\x8b\xe8\xaf\x95,1,,1,1,\n':
            raise AssertionError('ready fixture CSV content mismatch')
        for relative in ('manifest.json', 'build-identity.json'):
            actual_file, source_file = kit / 'extension' / relative, self.repo / 'extension/dist' / relative
            if actual_file.read_bytes() != source_file.read_bytes() or actual_file.stat().st_mode & 0o777 != source_file.stat().st_mode & 0o777:
                raise AssertionError(f'ready fixture extension stable file mismatch: {relative}')
        expected_env = (f'LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE={state["apiImage"]}\n'
                        f'LEXIFLOW_POSTGRES_IMAGE={state["postgresImage"]}\nLEXIFLOW_INSTALLATION_ID={state["id"]}\n'
                        f'LEXIFLOW_RELEASE_KEY=local-{state["id"]}\n')
        if (kit / 'release.env').read_text() != expected_env:
            raise AssertionError('ready fixture release environment mismatch')
        if (kit / 'compose.yaml').read_text() != expected_compose:
            raise AssertionError('ready fixture compose rendering mismatch')
        identity = state['buildIdentity']
        api_labels = images[state['apiImage']]['labels']
        for key in ('org.opencontainers.image.version', 'org.opencontainers.image.revision',
                    'io.lexiflow.build-id', 'io.lexiflow.source-sha256'):
            expected = {'org.opencontainers.image.version': identity['softwareVersion'],
                        'org.opencontainers.image.revision': identity['sourceCommit'],
                        'io.lexiflow.build-id': identity['buildId'],
                        'io.lexiflow.source-sha256': identity['sourceSha256']}[key]
            if api_labels.get(key) != expected:
                raise AssertionError(f'ready fixture API build label mismatch: {key}')
        for role, image in (('api', state['apiImage']), ('postgres', state['postgresImage'])):
            labels = images[image]['labels']
            if labels.get('io.lexiflow.image-role') != role or labels.get('io.lexiflow.deployment-version') != identity['baseVersion'].lower():
                raise AssertionError(f'ready fixture image role/version mismatch: {role}')
        if runtime.get('id') != state['apiImage'] or pg_runtime.get('id') != state['postgresImage']:
            raise AssertionError('ready fixture container image relation mismatch')
        if (runtime.get('containerId') == pg_runtime.get('containerId')
                or runtime.get('labels') != api_labels
                or pg_runtime.get('labels') != images[state['postgresImage']]['labels']):
            raise AssertionError('ready fixture fake container metadata mismatch')
        owners = {(row.get('Labels', {}).get('com.docker.compose.service'),
                   row.get('Labels', {}).get('lexiflow.installation'),
                   row.get('Labels', {}).get('com.docker.compose.project')) for row in container_rows}
        if owners != {('api', state['id'], state['project']), ('postgres', state['id'], state['project'])}:
            raise AssertionError('ready fixture container ownership mismatch')

    @staticmethod
    def ready_files_snapshot(kit):
        return {str(path.relative_to(kit)): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in kit.rglob('*') if path.is_file()}

    def expected_resolved_identity(self):
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e',
                                 "import {resolveBuildIdentity} from './ops/release/integrity/source-build-identity.mjs'; console.log(JSON.stringify(resolveBuildIdentity(process.cwd())))"],
                                cwd=self.repo, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def inspect_ready_images(self, state):
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', r'''import {versionedImageReference} from './ops/podman/images/versioned-images.mjs';
const {baseVersion}=JSON.parse(process.argv[1]); console.log(JSON.stringify({api:versionedImageReference('api',baseVersion),postgres:versionedImageReference('postgres',baseVersion)}));''',
                                    json.dumps(state['buildIdentity'])], cwd=self.repo,
                               capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        refs = json.loads(result.stdout)
        images = json.loads((self.root / 'images.json').read_text())
        for role, ref in refs.items():
            self.assertEqual(images[ref]['id'], state['apiImage' if role == 'api' else 'postgresImage'])
        return images

    def prepare_logs_fixture(self):
        """使用当前私有 checkout 的真实身份与部署渲染创建只读状态验证夹具。"""
        if self.api_port == self.db_port:
            raise AssertionError('fixture API and database ports must differ')
        if self.kit.exists() and any(self.kit.iterdir()):
            raise AssertionError('fixture target must be an empty per-test directory')
        self.kit.mkdir(parents=True, exist_ok=True)
        data_dir = self.kit / 'data/ecdict-source.fixture'
        data_dir.mkdir(parents=True)
        csv = b'word,translation,oxford,tag,bnc,frq,exchange\nfixture,synthetic,1,,1,1,\n'
        (data_dir / 'stardict.csv').write_bytes(csv)
        lock = json.loads((self.repo / 'ops/dataset/ecdict-source.lock.json').read_text())
        upstream = {'repository_url': lock['repository_url'], 'commit': lock['commit'],
                    'files': lock['files'], 'archive_member': {
                        'sha256': lock['archive_member']['sha256'], 'bytes': lock['archive_member']['bytes']}}
        script = r'''import { resolveBuildIdentity } from './ops/release/integrity/source-build-identity.mjs';
import { generationCodeVersion, dataGeneration } from './ops/dataset/dataset-generation-identity.mjs';
import { renderCompose } from './ops/podman/installation/installation-upgrade-workflow.mjs';
import { readFile } from 'node:fs/promises';
const [apiPort, dbPort, inputSha256, upstreamJson] = process.argv.slice(1);
const identity = resolveBuildIdentity(process.cwd());
const codeVersion = await generationCodeVersion(process.cwd());
const generation = dataGeneration({ codeVersion, inputSha256, upstream: JSON.parse(upstreamJson) });
const compose = renderCompose(process.cwd(), { apiPort: Number(apiPort), dbPort: Number(dbPort),
  external: false, dbUser: 'lexiflow', authMode: 'managed' });
console.log(JSON.stringify({ identity, generation, compose,
  schema: await readFile('infra/postgres/schema.sql', 'utf8') }));'''
        generated = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script,
                                    str(self.api_port), str(self.db_port),
                                    hashlib.sha256(csv).hexdigest(), json.dumps(upstream)],
                                   cwd=self.repo, capture_output=True, text=True, timeout=15)
        if generated.returncode:
            raise AssertionError(f'fixture identity generation failed: {generated.stderr}')
        try:
            material = json.loads(generated.stdout)
        except json.JSONDecodeError as error:
            raise AssertionError('fixture identity generation returned invalid JSON') from error
        identity, generation = material['identity'], material['generation']
        installation_id = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
        api_image = 'sha256:' + hashlib.sha256((installation_id + ':api').encode()).hexdigest()
        postgres_image = 'sha256:' + hashlib.sha256((installation_id + ':postgres').encode()).hexdigest()
        project = f'lexiflow-local-{installation_id}'
        api_labels = {'org.opencontainers.image.version': identity['softwareVersion'],
                      'org.opencontainers.image.revision': identity['sourceCommit'],
                      'io.lexiflow.build-id': identity['buildId'],
                      'io.lexiflow.source-sha256': identity['sourceSha256']}
        (self.kit / 'compose.yaml').write_text(material['compose'])
        env_text = (f'LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE={api_image}\n'
                    f'LEXIFLOW_POSTGRES_IMAGE={postgres_image}\nLEXIFLOW_INSTALLATION_ID={installation_id}\n'
                    f'LEXIFLOW_RELEASE_KEY=local-{installation_id}\n')
        (self.kit / 'release.env').write_text(env_text)
        (self.kit / 'infra/postgres').mkdir(parents=True)
        (self.kit / 'infra/postgres/schema.sql').write_text(material['schema'])
        (self.kit / 'secrets').mkdir()
        for name in ('app-password', 'postgres-password'):
            secret = self.kit / 'secrets' / name
            secret.write_text('synthetic-' + installation_id + '-' + name + '\n')
            secret.chmod(0o600)
        dataset = str(data_dir.resolve())
        digested = ['compose.yaml', 'release.env', 'secrets/postgres-password', 'secrets/app-password',
                    'infra/postgres/schema.sql', 'data/ecdict-source.fixture/stardict.csv']
        digests = {name: hashlib.sha256((self.kit / name).read_bytes()).hexdigest() for name in digested}
        state = {'schema': 1, 'id': installation_id, 'project': project, 'root': str(self.kit.resolve()),
                 'apiPort': self.api_port, 'dbPort': self.db_port, 'captionDebug': False, 'phase': 'ready',
                 'version': identity['softwareVersion'], 'buildIdentity': identity,
                 'databaseTarget': {'kind': 'managed', 'authMode': 'managed', 'host': '127.0.0.1',
                                    'port': self.db_port, 'user': 'lexiflow'}, 'packageType': 'source',
                 'apiImage': api_image, 'postgresImage': postgres_image, 'apiJar': None,
                 'inputType': 'csv', 'inputSha256': hashlib.sha256(csv).hexdigest(),
                 'generation': generation, 'generationVersion': generation['generationVersion'],
                 'dataset': dataset, 'datasetIdentity': {'version': 1,
                     'generationVersion': generation['generationVersion'],
                     'sourceSha256': hashlib.sha256(csv).hexdigest()},
                 'artifacts': {'apiImage': api_image}, 'digests': digests}
        (self.kit / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
        # Fake provider metadata models only synthetic owned resources; no install CLI result is forged.
        api_container = hashlib.sha256((installation_id + ':api-container').encode()).hexdigest()
        pg_container = hashlib.sha256((installation_id + ':pg-container').encode()).hexdigest()
        images = {api_image: {'id': api_image, 'labels': api_labels},
                  postgres_image: {'id': postgres_image, 'labels': {}}}
        (self.root / 'images.json').write_text(json.dumps(images))
        (self.root / 'runtime.json').write_text(json.dumps({'id': api_image, 'labels': api_labels,
            'containerId': api_container, 'generation': 1}))
        (self.root / 'pg-runtime.json').write_text(json.dumps({'id': postgres_image,
            'labels': {'lexiflow.installation': installation_id}, 'containerId': pg_container,
            'generation': 1}))
        (self.root / 'generation-version').write_text(generation['generationVersion'])
        # Non-empty content ensures the no-copy assertion cannot vacuously pass.
        (self.kit / 'operation-fixture.log').write_text('synthetic operation progress\n')
        self.assert_logs_fixture_contract(state, self.kit)
        result = self.invoke('status', serve=True)
        if result.returncode:
            raise AssertionError(f'fixture read-only status failed: {result.stderr}')
        return state

    def prepare_ready_fixture(self):
        """独立生成每例私有ready前态，不写入安装成功或初始化历史。"""
        if self.api_port == self.db_port or self.kit.exists() and any(self.kit.iterdir()):
            raise AssertionError('ready fixture requires distinct ports and an empty private target')
        self.kit.mkdir(parents=True, exist_ok=True)
        csv_bytes = b'word,translation,oxford,tag,bnc,frq,exchange\ntest,\xe6\xb5\x8b\xe8\xaf\x95,1,,1,1,\n'
        input_sha = hashlib.sha256(csv_bytes).hexdigest()
        lock = json.loads((self.repo / 'ops/dataset/ecdict-source.lock.json').read_text())
        upstream = {'repository_url': lock['repository_url'], 'commit': lock['commit'], 'files': lock['files'],
                    'archive_member': {'sha256': lock['archive_member']['sha256'], 'bytes': lock['archive_member']['bytes']}}
        script = r'''import { resolveBuildIdentity } from './ops/release/integrity/source-build-identity.mjs';
import { generationCodeVersion, dataGeneration } from './ops/dataset/dataset-generation-identity.mjs';
import { versionedImageReference, versionedImageLabels } from './ops/podman/images/versioned-images.mjs';
import { renderCompose } from './ops/podman/installation/installation-upgrade-workflow.mjs';
import { readFile } from 'node:fs/promises';
const [apiPort,dbPort,inputSha,upstreamJson]=process.argv.slice(1);
const identity=resolveBuildIdentity(process.cwd()), codeVersion=await generationCodeVersion(process.cwd());
const upstream=JSON.parse(upstreamJson), generation=dataGeneration({codeVersion,inputSha256:inputSha,upstream});
const apiIdentity=['--label',`org.opencontainers.image.version=${identity.softwareVersion}`,'--label',`org.opencontainers.image.revision=${identity.sourceCommit}`,'--label',`io.lexiflow.build-id=${identity.buildId}`,'--label',`io.lexiflow.source-sha256=${identity.sourceSha256}`];
console.log(JSON.stringify({identity,generation,upstream,
 api:versionedImageReference('api',identity.baseVersion),postgres:versionedImageReference('postgres',identity.baseVersion),
 apiLabels:[...apiIdentity,...versionedImageLabels('api',identity.baseVersion)],
 pgLabels:versionedImageLabels('postgres',identity.baseVersion),
 compose:renderCompose(process.cwd(),{apiPort:Number(apiPort),dbPort:Number(dbPort),external:false,dbUser:'lexiflow',authMode:'managed'}),
 schema:await readFile('infra/postgres/schema.sql','utf8')}));'''
        generated = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script,
                                    str(self.api_port), str(self.db_port), input_sha, json.dumps(upstream)],
                                   cwd=self.repo, capture_output=True, text=True, timeout=15)
        if generated.returncode:
            raise AssertionError(f'ready fixture identity generation failed: {generated.stderr}')
        material = json.loads(generated.stdout)
        self.expected_ready_compose = material['compose']
        java_build = subprocess.run([str(self.repo / 'backend/gradlew'), '--no-daemon', ':api:bootJar'],
                                    cwd=self.repo / 'backend', capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}, timeout=15)
        if java_build.returncode:
            raise AssertionError(f'ready fixture fake jar build failed: {java_build.stderr}')
        extension_build = subprocess.run([str(self.bin / 'npm'), 'run', 'build'], cwd=self.repo / 'extension',
                                         capture_output=True, text=True,
                                         env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                                              'LEXIFLOW_API_PORT': str(self.api_port)}, timeout=15)
        if extension_build.returncode:
            raise AssertionError(f'ready fixture fake extension build failed: {extension_build.stderr}')
        installation_id = os.urandom(16).hex()
        data_dir = self.kit / 'data' / f'ecdict-source.{input_sha[:16]}'
        data_dir.mkdir(parents=True, mode=0o700)
        csv_file = data_dir / 'stardict.csv'
        csv_file.write_bytes(csv_bytes)
        (data_dir / 'source-summary.json').write_text(json.dumps({'upstream': material['upstream'],
            'csv_sha256': input_sha, 'generation': material['generation']}))
        (data_dir / 'source-summary.json').chmod(0o600)
        (self.kit / 'compose.yaml').write_text(material['compose'])
        (self.kit / 'compose.yaml').chmod(0o600)
        (self.kit / 'infra/postgres').mkdir(parents=True)
        (self.kit / 'infra/postgres/schema.sql').write_text(material['schema'])
        (self.kit / 'secrets').mkdir()
        for name in ('app-password', 'postgres-password'):
            secret = self.kit / 'secrets' / name
            secret.write_text(f'synthetic-{installation_id}-{name}\n')
            secret.chmod(0o600)
        shutil.copytree(self.repo / 'extension/dist', self.kit / 'extension')
        jar_source = self.repo / 'backend/product/api/build/internal/boot/api-internal.jar'
        jar = (self.kit / '.prepared-ready-fixture/api/lexiflow-api.jar').resolve()
        jar.parent.mkdir(parents=True, mode=0o700)
        shutil.copyfile(jar_source, jar)
        images = json.loads((self.root / 'images.json').read_text()) if (self.root / 'images.json').exists() else {}
        for role, tag, labels, context in (
                ('api', material['api'], material['apiLabels'], str(self.repo / 'ops/docker')),
                ('postgres', material['postgres'], material['pgLabels'], str(self.repo / 'ops/docker'))):
            args = ['build', '--platform', 'linux/arm64', '--pull=never', *labels, '-t', tag, context]
            result = subprocess.run([str(self.bin / 'podman'), *args], capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}, timeout=15)
            if result.returncode:
                raise AssertionError(f'ready fixture fake {role} build failed: {result.stderr}')
            images = json.loads((self.root / 'images.json').read_text())
        api_image, pg_image = images[material['api']]['id'], images[material['postgres']]['id']
        release_env = (f'LEXIFLOW_PLATFORM=linux/arm64\nLEXIFLOW_API_IMAGE={api_image}\n'
                       f'LEXIFLOW_POSTGRES_IMAGE={pg_image}\nLEXIFLOW_INSTALLATION_ID={installation_id}\n'
                       f'LEXIFLOW_RELEASE_KEY=local-{installation_id}\n')
        (self.kit / 'release.env').write_text(release_env)
        (self.kit / 'release.env').chmod(0o600)
        relative_csv = str(csv_file.relative_to(self.kit))
        state = {'schema': 1, 'id': installation_id, 'project': f'lexiflow-local-{installation_id}',
                 'root': str(self.kit.resolve()), 'apiPort': self.api_port, 'dbPort': self.db_port,
                 'captionDebug': False, 'phase': 'ready', 'version': material['identity']['softwareVersion'],
                 'buildIdentity': material['identity'], 'databaseTarget': {'kind': 'managed', 'authMode': 'managed',
                     'host': '127.0.0.1', 'port': self.db_port, 'user': 'lexiflow'}, 'packageType': 'source',
                 'apiImage': api_image, 'postgresImage': pg_image, 'apiJar': str(jar), 'inputType': 'csv',
                 'inputSha256': input_sha, 'generation': material['generation'],
                 'generationVersion': material['generation']['generationVersion'], 'dataset': str(data_dir.resolve()),
                 'datasetIdentity': {'version': 1, 'generationVersion': material['generation']['generationVersion'],
                                     'sourceSha256': input_sha},
                 'artifacts': {'jar': hashlib.sha256(jar_source.read_bytes()).hexdigest(), 'apiImage': api_image},
                 'digests': {}}
        state['digests'] = {name: hashlib.sha256((self.kit / name).read_bytes()).hexdigest()
                            for name in ('compose.yaml', 'release.env', 'secrets/postgres-password',
                                         'secrets/app-password', 'infra/postgres/schema.sql', relative_csv)}
        (self.kit / 'state.json').write_text(json.dumps(state, indent=2) + '\n')
        (self.kit / 'state.json').chmod(0o600)
        # Process-lock guard is persistent inode coordination metadata, not an install-success record.
        guard = self.kit / '.lock-guard'
        guard.touch(mode=0o600, exist_ok=False)
        (self.root / 'generation-version').write_text(material['generation']['generationVersion'])
        for service in ('postgres', 'api'):
            result = subprocess.run([str(self.bin / 'podman'), 'compose', '--env-file', str(self.kit / 'release.env'),
                                     '-f', str(self.kit / 'compose.yaml'), '-p', state['project'], 'up', '-d', service],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}, timeout=15)
            if result.returncode:
                raise AssertionError(f'ready fixture fake compose failed: {result.stderr}')
        ps = subprocess.run([str(self.bin / 'podman'), 'ps', '--format', 'json'], capture_output=True, text=True,
                            env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}, timeout=15)
        if ps.returncode:
            raise AssertionError(f'ready fixture fake provider query failed: {ps.stderr}')
        self.assert_ready_fixture_contract(state, self.kit, images,
                                           json.loads((self.root / 'runtime.json').read_text()),
                                           json.loads((self.root / 'pg-runtime.json').read_text()),
                                           json.loads(ps.stdout), self.expected_ready_compose)
        status = self.invoke('status', serve=True)
        if status.returncode:
            raise AssertionError(f'ready fixture status failed: {status.stderr}\n{status.stdout}')
        return state

    def tearDown(self):
        failure = None
        try:
            if self.fake_transport is not None:
                self.fake_transport.close()
        except BaseException as exc:
            failure = exc
        finally:
            self.finished.set()
            if self.thread:
                self.thread.join(timeout=3)
            self.server.server_close()
            self.temp.cleanup()
        if failure is not None:
            raise failure

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
        argv = [shutil.which('node'), '--import', str(self.preload), str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'), action, '--dir', str(self.kit)]
        if action == 'install': argv += ['--api-port', str(self.api_port), '--db-port', str(self.db_port)]
        env = {**os.environ, 'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}
        if action in ('install', 'upgrade') and not any(value in extra for value in ('--plan', '--confirm-plan')):
            planned = subprocess.run(argv+list(extra)+['--plan'], env=env, capture_output=True, text=True, timeout=35)
            if planned.returncode: return planned
            rows = [json.loads(line) for line in planned.stdout.splitlines() if line.startswith('{') and 'planHash' in line]
            self.assertEqual(len(rows), 1, planned.stdout)
            return subprocess.run(argv+list(extra)+['--confirm-plan', rows[0]['planHash']], env=env, capture_output=True, text=True, timeout=35)
        return subprocess.run(argv+list(extra), env=env, capture_output=True, text=True, timeout=35)

    def invoke_tty_confirmation(self, answer):
        master, slave = pty.openpty()
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'),
              'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port)]
        env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}
        process=subprocess.Popen(argv,stdin=slave,stdout=slave,stderr=slave,env=env,close_fds=True)
        os.close(slave)
        output=bytearray()
        try:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline and b'[y/N]' not in output:
                ready,_,_=select.select([master],[],[],.2)
                if ready:
                    try:
                        chunk=os.read(master,4096)
                        if not chunk: break
                        output.extend(chunk)
                    except OSError: break
                if process.poll() is not None: break
            if answer is not None and b'[y/N]' in output:
                if answer == 'SIGINT':
                    process.send_signal(signal.SIGINT)
                else:
                    os.write(master,answer)
            deadline=time.monotonic()+60
            while process.poll() is None and time.monotonic()<deadline:
                if select.select([master],[],[],.2)[0]:
                    try: output.extend(os.read(master,4096))
                    except OSError: break
            if process.poll() is None:
                raise AssertionError(f'tty install timed out: {output.decode(errors="replace")}')
            code=process.returncode
            while select.select([master],[],[],.1)[0]:
                try:
                    chunk=os.read(master,4096)
                    if not chunk: break
                    output.extend(chunk)
                except OSError: break
            return code,output.decode(errors='replace')
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            os.close(master)

    def test_unknown_directory_is_not_adopted(self):
        self.kit.mkdir(); (self.kit/'keep').write_text('safe')
        result=self.invoke('install')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.kit/'keep').read_text(),'safe')
        self.assertFalse(self.calls.exists())

    def test_first_install_plan_uses_current_checkout_and_not_foreign_private_state(self):
        # Fixture 路径刻意与真实 checkout 不同；计划必须从当前源码根派生而非记住另一台机器的路径。
        result=self.invoke('install','--plan')
        self.assertEqual(result.returncode,0,result.stderr)
        row=next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{') and 'planHash' in line)
        target=row['plan']['database']['target']
        self.assertEqual(target['kind'],'managed')
        self.assertEqual(target['authMode'],'managed')
        self.assertEqual(target['host'],'127.0.0.1')
        self.assertEqual(target['port'],self.db_port)
        self.assertEqual(target['user'],'lexiflow')
        self.assertEqual(row['plan']['installation'],f'new:{self.kit}')
        self.assertNotIn(str(ROOT),result.stdout)
        self.assertNotIn('.local/credentials',result.stdout)
        self.assertFalse(self.kit.exists())
        self.assertFalse((self.repo/'.local/credentials').exists())

    def test_install_stop_up_does_not_reimport(self):
        result=self.invoke('install',serve=True)
        self.assertEqual(result.returncode,0,result.stderr)
        state=json.loads((self.kit/'state.json').read_text())
        self.assert_logs_fixture_contract(state, self.kit)
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
        self.prepare_ready_fixture()
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
        self.assertTrue((self.kit/'secrets/app-password').is_file())
        self.assertEqual((self.kit/'secrets/app-password').stat().st_mode & 0o777,0o600)
        state=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(state['databaseTarget']['kind'],'managed')
        self.assertEqual(state['databaseTarget']['authMode'],'managed')

    def test_confirmation_refusal_and_eof_do_not_prepare_or_create_install_state(self):
        # 非 TTY 的 CLI 必须显式 BLOCKED；拒绝路径由部署层本地确认合同覆盖。
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'),
              'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port)]
        env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']}
        result=subprocess.run(argv,input='',env=env,capture_output=True,text=True,timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('交互部署需要 TTY 中确认一次',result.stderr)
        self.assertIn('读取安装记录',result.stdout)
        self.assertIn('核验数据库目标',result.stdout)
        self.assertNotIn('准备阶段',result.stdout)
        self.assertFalse(self.kit.exists())
        if not self.calls.exists():
            return
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(row[:1]==['run'] for row in calls))

    def test_tty_refusal_is_single_chinese_prompt_and_exits_without_preparation(self):
        code,output=self.invoke_tty_confirmation(b'n\n')
        self.assertEqual(code,0,output)
        self.assertEqual(output.count('[y/N]'),1,output)
        self.assertIn('使用本机 LexiFlow 数据库',output)
        self.assertNotIn('确认完成，开始连接目标与准备阶段',output)
        self.assertFalse(self.kit.exists())

    def test_tty_eof_and_cancellation_exit_without_preparation(self):
        code,output=self.invoke_tty_confirmation(b'\x04')
        self.assertNotEqual(code,0,output)
        self.assertIn('[y/N]',output)
        self.assertFalse(self.kit.exists())
        code,output=self.invoke_tty_confirmation('SIGINT')
        self.assertNotEqual(code,0,output)
        self.assertIn('[y/N]',output)
        self.assertFalse(self.kit.exists())

    def test_upgrade_cli_uses_api_only_and_never_initializes_again(self):
        self.prepare_ready_fixture()
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
        self.assertTrue(any('up' in row and row[-1]=='postgres' for row in compose))
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual((after['id'],after['project'],after['apiPort'],after['dbPort']),(before['id'],before['project'],before['apiPort'],before['dbPort']))
        self.assertNotEqual(after['postgresImage'],before['postgresImage']);self.assertEqual(marker.read_text(),'preserve')
        self.assertNotEqual(after['apiImage'],before['apiImage'])
        self.assertEqual(json.loads((self.kit/'extension/build-identity.json').read_text()),after['buildIdentity'])
        self.assertEqual(after['lastOperation']['buildId'],after['buildIdentity']['buildId'])
        self.assertEqual(after['datasetIdentity'],before['datasetIdentity'])
        new_container=json.loads((self.root/'runtime.json').read_text())['containerId']
        self.assertNotEqual(old_container,new_container)
        noop_offset=len(self.calls.read_text().splitlines())
        result=self.invoke('upgrade');self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('"plan":',result.stdout)
        self.assertNotEqual(json.loads((self.root/'runtime.json').read_text())['containerId'],new_container)
        self.assertTrue(any('--force-recreate' in json.loads(line) for line in self.calls.read_text().splitlines()[noop_offset:]))
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
        self.assertNotEqual(before['postgresImage'], after['postgresImage'])
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
        self.prepare_ready_fixture()
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
        self.assertEqual(len([row for row in activations if row[-1]=='api']),1)
        self.assertTrue(all('--force-recreate' in row and '--no-deps' in row for row in activations if row[-1]=='api'))
        self.assertFalse(any('initialize' in row for row in calls))
        result=self.invoke('recover');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'ready')
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],after['apiImage'])
    def test_upgrade_build_and_start_failures_restore_then_retry(self):
        self.prepare_ready_fixture()
        before=json.loads((self.kit/'state.json').read_text())
        original_extension=(self.kit/'extension/build-identity.json').read_bytes()
        (self.repo/'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
        self.mode.write_text('api-build-fail')
        offset=len(self.calls.read_text().splitlines())
        result=self.invoke('upgrade');self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text()),before)
        self.assertEqual((self.kit/'extension/build-identity.json').read_bytes(),original_extension)
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],before['apiImage'],
                         'a different version slot does not remove the previous version')
        self.assertTrue((self.kit/'image-replacement.json').exists())
        self.assertFalse((self.kit/'.lock').exists())
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        self.assertFalse(any('stop' in row or 'initialize' in row for row in calls))
        self.mode.write_text('api-start-fail')
        result=self.invoke('upgrade');self.assertNotEqual(result.returncode,0)
        pending=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(pending['phase'],'initialized',result.stderr + result.stdout)
        self.assertNotEqual(pending['apiImage'],before['apiImage'])
        self.assertEqual(json.loads((self.kit/'deployment.json').read_text())['stage'],'data-committed')
        self.assertFalse((self.kit/'.lock').exists())
        self.mode.write_text('')
        result=self.invoke('recover');self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['phase'],'ready')
        self.assertEqual(json.loads((self.root/'runtime.json').read_text())['id'],pending['apiImage'])
    def test_same_version_postgres_build_failure_requires_confirmed_forward_retry(self):
        self.prepare_ready_fixture()
        before=json.loads((self.kit/'state.json').read_text())
        password=(self.kit/'secrets/app-password').read_bytes()
        self.mode.write_text('pg-build-fail')
        result=self.invoke('install');self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text()),before)
        self.assertTrue((self.kit/'image-replacement.json').exists())
        self.assertFalse((self.root/'pg-runtime.json').exists())
        self.assertEqual((self.kit/'secrets/app-password').read_bytes(),password)
        for action in ['up','stop','recover']:
            rejected=self.invoke(action);self.assertNotEqual(rejected.returncode,0)
        self.mode.write_text('')
        planned=self.invoke('install','--plan');self.assertNotEqual(planned.returncode,0)
        self.assertIn('托管 PostgreSQL 镜像替换尚未完成',planned.stderr)
        code,output=self.invoke_tty_confirmation(b'y\n')
        self.assertEqual(code,0,output)
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(after['phase'],'ready')
        self.assertEqual(after['postgresImage'],before['postgresImage'],
                         'identical cached PG rebuild may yield the same image ID')
        self.assertEqual(after['datasetIdentity'],before['datasetIdentity'])
        self.assertEqual((self.kit/'secrets/app-password').read_bytes(),password)
        self.assertFalse((self.kit/'image-replacement.json').exists())
        self.assertTrue((self.root/'pg-runtime.json').exists())
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(sum('--deployment-data' in row and 'apply' in row for row in calls),0)
        self.assertFalse(any('--force' in row or '-v' in row and row[:1]==['rmi'] for row in calls))
    def test_same_version_api_build_failure_retries_after_precise_removal(self):
        self.prepare_ready_fixture()
        before=json.loads((self.kit/'state.json').read_text())
        self.mode.write_text('api-build-fail')
        offset=len(self.calls.read_text().splitlines())
        failed=self.invoke('install');self.assertNotEqual(failed.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text()),before)
        self.assertFalse((self.root/'runtime.json').exists())
        self.assertTrue((self.root/'pg-runtime.json').exists())
        self.assertTrue((self.kit/'image-replacement.json').exists())
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
        api_remove=next(i for i,row in enumerate(calls) if row[:1]==['rmi'] and 'lexiflow-api:' in row[-1])
        api_build=next(i for i,row in enumerate(calls) if row[:1]==['build'] and any('localhost/lexiflow-api:' in arg for arg in row))
        self.assertLess(api_remove,api_build)
        self.mode.write_text('')
        code,output=self.invoke_tty_confirmation(b'y\n');self.assertEqual(code,0,output)
        self.assertFalse((self.kit/'image-replacement.json').exists())
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(after['phase'],'ready')
        self.assertEqual(after['datasetIdentity'],before['datasetIdentity'])
    def test_verify_plan_failure_keeps_config_bound_for_retry(self):
        self.prepare_ready_fixture()
        before=json.loads((self.kit/'state.json').read_text())
        with (self.repo/'ops/docker/Dockerfile').open('a') as output: output.write('\n# synthetic changed build input\n')
        self.mode.write_text('config-fail-once')
        failed=self.invoke('install');self.assertNotEqual(failed.returncode,0)
        self.assertEqual(json.loads((self.kit/'state.json').read_text()),before)
        self.assertTrue((self.kit/'image-replacement.json').exists())
        self.assertNotEqual(before['digests']['release.env'],hashlib.sha256((self.kit/'release.env').read_bytes()).hexdigest())
        planned=self.invoke('install','--plan');self.assertNotEqual(planned.returncode,0)
        code,output=self.invoke_tty_confirmation(b'y\n');self.assertEqual(code,0,output)
        self.assertFalse((self.kit/'image-replacement.json').exists())
        after=json.loads((self.kit/'state.json').read_text())
        self.assertEqual(after['phase'],'ready')
        self.assertEqual(after['datasetIdentity'],before['datasetIdentity'])
    def test_logs_display_does_not_copy_caption_body_to_operation_file(self):
        self.prepare_logs_fixture()
        result=self.invoke('logs');self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('synthetic-caption-diagnostic',result.stdout)
        operation_files=list(self.kit.glob('operation-*.log'))
        self.assertGreaterEqual(len(operation_files),1)
        for file in operation_files:
            self.assertNotIn('synthetic-caption-diagnostic',file.read_text())

    def test_logs_are_read_only_even_with_lock_and_pending_transaction(self):
        self.prepare_logs_fixture()
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
        self.prepare_logs_fixture()
        self.mode.write_text('logs-follow')
        offset=len(self.calls.read_text().splitlines())
        operations=set(self.kit.glob('operation-*.log'))
        state=(self.kit/'state.json').read_bytes()
        output=self.root/'follow-output'
        with output.open('w') as out:
            child=subprocess.Popen([shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'),'logs','--dir',str(self.kit)],env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']},stdout=out,stderr=out)
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
        self.prepare_logs_fixture()
        self.mode.write_text('logs-fail')
        result=self.invoke('logs')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('退出码 9',result.stderr)

    def test_source_template_copies_are_mutably_isolated(self):
        source_before = (self.repo / 'fixture.txt').read_bytes()
        git_before = (self.repo / '.git/config').read_bytes()
        template_before = self._source_snapshot(self._source_template)
        with tempfile.TemporaryDirectory(prefix="lexiflow source copy ") as temp:
            other = Path(temp) / 'source'
            shutil.copytree(self._source_template, other)
            (other / 'fixture.txt').write_text('independent mutation\n')
            (other / '.git/config').write_text('independent git mutation\n')
            self.assertEqual((self.repo / 'fixture.txt').read_bytes(), source_before)
            self.assertEqual((self.repo / '.git/config').read_bytes(), git_before)
            self.assertEqual(self._source_snapshot(self._source_template), template_before)
        self.assertFalse(other.exists())

    def test_logs_fixture_instances_are_isolated(self):
        first_state=self.prepare_logs_fixture()
        first_snapshot=(self.kit/'state.json').read_bytes()
        second=LocalEntryTest(methodName='runTest')
        # 数据库是合成 provider，并未真正监听；临时保留端口避免 OS 合法复用
        # 未绑定端口导致“两个独立实例”回归偶发失败。
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', self.db_port))
            second.setUp()
        try:
            second_state=second.prepare_logs_fixture()
            self.assertNotEqual(self.kit,second.kit)
            self.assertNotEqual(self.api_port,second.api_port)
            self.assertNotEqual(self.db_port,second.db_port)
            self.assertNotEqual(first_state['id'],second_state['id'])
            self.assertNotEqual(first_state['digests'],second_state['digests'])
            self.assertEqual((self.kit/'state.json').read_bytes(),first_snapshot)
            self.assertNotEqual((self.kit/'state.json').read_bytes(),(second.kit/'state.json').read_bytes())
        finally:
            second.tearDown()

    def test_logs_fixture_rejects_invalid_port_contract(self):
        self.db_port=self.api_port
        with self.assertRaisesRegex(AssertionError,'ports must differ'):
            self.prepare_logs_fixture()
        self.assertFalse(self.kit.exists())

    def test_ready_precondition_conforms_to_one_real_install_and_plan_reads(self):
        candidate = self.prepare_ready_fixture()
        candidate_identity = self.expected_resolved_identity()
        self.assertEqual(candidate['buildIdentity'], candidate_identity)
        candidate_images = self.inspect_ready_images(candidate)
        reference = LocalEntryTest(methodName='runTest')
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', self.db_port))
            reference.setUp()
        try:
            installed = reference.invoke('install', serve=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            actual = json.loads((reference.kit / 'state.json').read_text())
            actual_identity = reference.expected_resolved_identity()
            self.assertEqual(actual['buildIdentity'], actual_identity)
            self.assertEqual(candidate_identity.keys(), actual_identity.keys())
            for field in candidate_identity.keys() - {'sourceCommit', 'buildId', 'softwareVersion', 'chromeVersion'}:
                self.assertEqual(candidate_identity[field], actual_identity[field], field)
            self.assertEqual(candidate['generation'], actual['generation'])
            self.assertEqual(candidate['datasetIdentity'], actual['datasetIdentity'])
            actual_images = reference.inspect_ready_images(actual)
            for case, state, images in ((self, candidate, candidate_images), (reference, actual, actual_images)):
                resolved = case.expected_resolved_identity()
                self.assertEqual(state['buildIdentity'], resolved)
                self.assertEqual(images[state['apiImage']]['labels']['io.lexiflow.build-id'], resolved['buildId'])
                self.assertEqual(images[state['apiImage']]['labels']['io.lexiflow.source-sha256'], resolved['sourceSha256'])
                self.assertEqual(images[state['apiImage']]['labels']['org.opencontainers.image.revision'], resolved['sourceCommit'])
                self.assertEqual(images[state['apiImage']]['labels']['org.opencontainers.image.version'], resolved['softwareVersion'])
            self.assertNotEqual(candidate['id'], actual['id'])
            self.assertNotEqual(candidate['root'], actual['root'])
            self.assertNotEqual(candidate['apiPort'], actual['apiPort'])
            self.assertNotEqual(candidate['dbPort'], actual['dbPort'])
            self.assertEqual(candidate['databaseTarget']['port'], candidate['dbPort'])
            self.assertEqual(actual['databaseTarget']['port'], actual['dbPort'])
            self.assertNotIn('lastOperation', candidate)
            self.assertIn('lastOperation', actual)
            self.assertFalse((self.kit / 'installation-records').exists())
            self.assertFalse(list(self.kit.glob('operation-*.log')))
            self.assertTrue((reference.kit / 'installation-records').is_dir())
            self.assertTrue(list(reference.kit.glob('operation-*.log')))

            # 稳定制品逐字节及权限对照；随机凭据、安装目录与 commit 派生身份不跨实例硬比。
            stable_files = [
                (Path(candidate['dataset']) / 'stardict.csv', Path(actual['dataset']) / 'stardict.csv'),
                (self.kit / 'infra/postgres/schema.sql', reference.kit / 'infra/postgres/schema.sql'),
                (Path(candidate['apiJar']), Path(actual['apiJar'])),
            ]
            for left, right in stable_files:
                self.assertEqual(left.read_bytes(), right.read_bytes())
                self.assertEqual(left.stat().st_mode & 0o777, right.stat().st_mode & 0o777)
            summaries = [Path(state['dataset']) / 'source-summary.json' for state in (candidate, actual)]
            self.assertEqual(json.loads(summaries[0].read_text()), json.loads(summaries[1].read_text()))
            self.assertEqual(summaries[0].stat().st_mode & 0o777, summaries[1].stat().st_mode & 0o777)
            extension_files = [case.ready_files_snapshot(case.kit / 'extension') for case in (self, reference)]
            self.assertEqual(set(extension_files[0]), {'manifest.json', 'build-identity.json'})
            self.assertEqual(extension_files[0].keys(), extension_files[1].keys())
            for name in extension_files[0]:
                self.assertEqual(extension_files[0][name][1], extension_files[1][name][1])
            for case, state in ((self, candidate), (reference, actual)):
                self.assertEqual(json.loads((case.kit / 'extension/manifest.json').read_text()), {
                    'version': state['buildIdentity']['chromeVersion'],
                    'version_name': state['buildIdentity']['softwareVersion'],
                    'host_permissions': [f"http://127.0.0.1:{state['apiPort']}/*"],
                })

            def expected_plan(state, operation, build_id):
                # 明确映射私有安装 ID、端口和当前源码身份；其余键和值必须全部一致。
                return {'schema': 1, 'operation': operation, 'installation': state['id'],
                        'target': {'buildId': build_id, 'generationVersion': state['generationVersion']},
                        'database': {'target': {**state['databaseTarget'],
                            'developmentIdentityFingerprint': None, 'database': 'lexiflow',
                            'schema': 'lexiflow_release', 'owner': state['databaseTarget']['user'], 'serverMajor': 17},
                            'fingerprint': 'f' * 64, 'takeover': False, 'create': False},
                        'actions': {'applicationUpdate': True, 'applicationStart': True,
                            'applicationReason': 'INSTALL_REFRESH' if operation == 'install' else 'BUILD_CHANGED',
                            'dataRebuild': False, 'dataReason': 'OK'}}

            for case in (self, reference):
                state = json.loads((case.kit / 'state.json').read_text())
                identity_script = r'''import { renderCompose } from './ops/podman/installation/installation-upgrade-workflow.mjs';
const [api,db]=process.argv.slice(1); console.log(renderCompose(process.cwd(),
 {apiPort:Number(api),dbPort:Number(db),external:false,dbUser:'lexiflow',authMode:'managed'}));'''
                rendered = subprocess.run([shutil.which('node'), '--input-type=module', '-e', identity_script,
                                            str(state['apiPort']), str(state['dbPort'])], cwd=case.repo,
                                           capture_output=True, text=True, timeout=15)
                self.assertEqual(rendered.returncode, 0, rendered.stderr)
                provider_ps = subprocess.run([str(case.bin / 'podman'), 'ps', '--format', 'json'],
                                             capture_output=True, text=True,
                                             env={**os.environ, 'PATH': str(case.bin) + os.pathsep + os.environ['PATH']},
                                             timeout=15)
                self.assertEqual(provider_ps.returncode, 0, provider_ps.stderr)
                case.assert_ready_fixture_contract(state, case.kit,
                    json.loads((case.root / 'images.json').read_text()),
                    json.loads((case.root / 'runtime.json').read_text()),
                    json.loads((case.root / 'pg-runtime.json').read_text()),
                    json.loads(provider_ps.stdout), rendered.stdout[:-1])
                files_before_status = case.ready_files_snapshot(case.kit)
                status = case.invoke('status')
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertEqual(files_before_status, case.ready_files_snapshot(case.kit))
                before = case.ready_files_snapshot(case.kit)
                install_plan = case.invoke('install', '--plan')
                self.assertEqual(install_plan.returncode, 0, install_plan.stderr)
                install_plan_obj = json.loads(next(line for line in install_plan.stdout.splitlines()
                                                   if line.startswith('{') and 'planHash' in line))['plan']
                self.assertEqual(install_plan_obj, expected_plan(state, 'install', state['buildIdentity']['buildId']))
                self.assertEqual(before, case.ready_files_snapshot(case.kit))
                (case.repo / 'ops/release/version.txt').write_text('2.0.1-SNAPSHOT\n')
                before = case.ready_files_snapshot(case.kit)
                offset = len(case.calls.read_text().splitlines())
                upgrade_plan = case.invoke('upgrade', '--plan')
                self.assertEqual(upgrade_plan.returncode, 0, upgrade_plan.stderr)
                upgrade_plan_obj = json.loads(next(line for line in upgrade_plan.stdout.splitlines()
                                                   if line.startswith('{') and 'planHash' in line))['plan']
                expected_target = case.expected_resolved_identity()
                self.assertEqual(upgrade_plan_obj, expected_plan(state, 'upgrade', expected_target['buildId']))
                self.assertEqual(before, case.ready_files_snapshot(case.kit))
                commands = [json.loads(line) for line in case.calls.read_text().splitlines()[offset:]]
                self.assertFalse(any('apply' in row or row[:1] in (['stop'], ['rm'])
                                     or row[:1] == ['compose'] and 'up' in row for row in commands))
        finally:
            reference.tearDown()

    def test_ready_precondition_rejects_damage_with_recomputed_hashes(self):
        state = self.prepare_ready_fixture()
        images = json.loads((self.root / 'images.json').read_text())
        runtime = json.loads((self.root / 'runtime.json').read_text())
        pg_runtime = json.loads((self.root / 'pg-runtime.json').read_text())
        ps = subprocess.run([str(self.bin / 'podman'), 'ps', '--format', 'json'], capture_output=True, text=True,
                            env={**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}, timeout=15)
        rows = json.loads(ps.stdout)
        base = json.loads(json.dumps(state))
        secret = self.kit / 'secrets/app-password'
        secret_bytes = secret.read_bytes()
        compose = self.kit / 'compose.yaml'
        compose_bytes = compose.read_bytes()
        cases = [
            ('negative-api-port', lambda value: value.update(apiPort=-1), None),
            ('database-target-port', lambda value: value['databaseTarget'].update(port=value['dbPort'] + 1), None),
        ]
        for name, mutate, _ in cases:
            changed = json.loads(json.dumps(base)); mutate(changed)
            expected_error = ('port or installation relation mismatch' if name == 'negative-api-port'
                              else 'database target port mismatch')
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, expected_error):
                self.assert_ready_fixture_contract(changed, self.kit, images, runtime, pg_runtime, rows,
                                                   self.expected_ready_compose)
        secret.write_bytes(b'')
        try:
            empty_secret = json.loads(json.dumps(base))
            empty_secret['digests']['secrets/app-password'] = hashlib.sha256(b'').hexdigest()
            with self.subTest(name='empty-secret-rehashed'), self.assertRaisesRegex(AssertionError, 'secret contract mismatch'):
                self.assert_ready_fixture_contract(empty_secret, self.kit, images, runtime, pg_runtime, rows,
                                                   self.expected_ready_compose)
        finally:
            secret.write_bytes(secret_bytes)
            secret.chmod(0o600)
        compose.write_bytes(compose_bytes + b'\nunknown-network: true\n')
        bad_compose = json.loads(json.dumps(base))
        bad_compose['digests']['compose.yaml'] = hashlib.sha256(compose.read_bytes()).hexdigest()
        with self.subTest(name='compose-rehashed'), self.assertRaisesRegex(AssertionError, 'compose rendering mismatch'):
            self.assert_ready_fixture_contract(bad_compose, self.kit, images, runtime, pg_runtime, rows,
                                               self.expected_ready_compose)
        compose.write_bytes(compose_bytes)
        wrong_labels = json.loads(json.dumps(images))
        wrong_labels[base['apiImage']]['labels']['io.lexiflow.build-id'] = '0' * 64
        with self.subTest(name='image-label-mismatch'), self.assertRaisesRegex(AssertionError, 'API build label mismatch'):
            self.assert_ready_fixture_contract(base, self.kit, wrong_labels, runtime, pg_runtime, rows,
                                               self.expected_ready_compose)

    def test_ready_precondition_instances_are_isolated(self):
        first = self.prepare_ready_fixture()
        second = LocalEntryTest(methodName='runTest')
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', self.db_port))
            second.setUp()
        try:
            other = second.prepare_ready_fixture()
            self.assertNotEqual(self.kit, second.kit)
            self.assertNotEqual(first['id'], other['id'])
            self.assertNotEqual(first['root'], other['root'])
            self.assertNotEqual(first['apiPort'], other['apiPort'])
            self.assertNotEqual(first['dbPort'], other['dbPort'])
            self.assertNotEqual(self.calls, second.calls)
            def snapshot(case):
                return case.ready_files_snapshot(case.root)
            for action_case, action_state, other_case in ((self, first, second), (second, other, self)):
                action_before = snapshot(action_case)
                other_before = snapshot(other_case)
                runtime_before = (action_case.root / 'pg-runtime.json').read_bytes()
                result = action_case.invoke('up')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(runtime_before, (action_case.root / 'pg-runtime.json').read_bytes())
                self.assertNotEqual(action_before, snapshot(action_case))
                self.assertEqual(other_before, snapshot(other_case))
                self.assertEqual(json.loads((action_case.kit / 'state.json').read_text()), action_state)
            self.assertEqual(json.loads((self.root / 'runtime.json').read_text())['id'], first['apiImage'])
            self.assertEqual(json.loads((second.root / 'runtime.json').read_text())['id'], other['apiImage'])
        finally:
            second.tearDown()

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
        with (self.repo/'ops/release/integrity/source-build-identity.mjs').open('a') as stream:
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
        self.prepare_ready_fixture()
        compose = self.kit/'compose.yaml'
        before=compose.read_bytes(); state=(self.kit/'state.json').read_bytes()
        compose.write_bytes(before+b'\nunknown-network: true\n')
        for action in ['up', 'recover']:
            with self.subTest(action=action):
                offset=len(self.calls.read_text().splitlines())
                result = self.invoke(action)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(compose.read_bytes(),before+b'\nunknown-network: true\n')
                self.assertEqual((self.kit/'state.json').read_bytes(),state)
                calls=[json.loads(line) for line in self.calls.read_text().splitlines()[offset:]]
                self.assertFalse(any('apply' in row or 'down' in row or 'prune' in row for row in calls))
                self.assertFalse(any(row[:1] == ['compose'] and any(cmd in row for cmd in ['up', 'stop']) for row in calls))

    def test_readiness_deadline_keeps_initialized_data(self):
        # 只在 API 就绪阶段推进 preload 时钟，避免准备阶段先消耗超时预算。
        # 产品没有缩短超时的测试开关；保留实际 120 秒边界及数据保留断言。
        with self.preload.open('a') as output:
            output.write("""
const realNow = performance.now.bind(performance);
const originalLog = console.log.bind(console);
let readinessClock = false;
let tick = 0;
console.log = (...args) => {
  if (args.some(value => typeof value === 'string' && value.includes('等待 API 正式就绪'))) {
    readinessClock = true;
    tick = realNow();
  }
  originalLog(...args);
};
Object.defineProperty(globalThis, 'performance', {
  value: {now: () => readinessClock ? (tick += 30000) : realNow()}
});
""")
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
        self.prepare_ready_fixture()
        with (self.kit/'release.env').open('a') as file: file.write('EXTRA=value\n')
        before=self.calls.read_text()
        result=self.invoke('stop');self.assertNotEqual(result.returncode,0)
        self.assertNotIn('"stop"',self.calls.read_text()[len(before):])
    def test_foreign_project_label_refuses_stop(self):
        self.prepare_ready_fixture()
        self.mode.write_text('foreign')
        result=self.invoke('stop');self.assertNotEqual(result.returncode,0)
        self.assertIn('归属不匹配',result.stderr)
    def test_owned_postgres_port_does_not_block_preinit_retry(self):
        self.prepare_ready_fixture()
        state=json.loads((self.kit/'state.json').read_text())
        self.mode.write_text('owned-pg')
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1',state['dbPort']));listener.listen()
            result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((self.kit/'state.json').read_text())['datasetIdentity'],state['datasetIdentity'])
    def test_schema_and_csv_tampering_block_initialization(self):
        self.prepare_ready_fixture()
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
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'),'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port),'--confirm-plan',plan_hash]
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

    def test_cancel_during_source_run_removes_only_owned_untagged_tool_image(self):
        self.server.server_close()
        self.mode.write_text('slow-source-run')
        argv=[shutil.which('node'),'--import',str(self.preload),str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'),
              'install','--dir',str(self.kit),'--api-port',str(self.api_port),'--db-port',str(self.db_port),'--plan']
        proc=subprocess.Popen(argv,env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ['PATH']},
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            until=time.monotonic()+10
            while time.monotonic()<until:
                if self.calls.exists() and any('/kit/ops/dataset/prepare_ecdict_bundle.py' in json.loads(line)
                                               for line in self.calls.read_text().splitlines()): break
                time.sleep(.02)
            self.assertIsNone(proc.poll())
            proc.send_signal(signal.SIGINT)
            out,err=proc.communicate(timeout=10)
            self.assertNotEqual(proc.returncode,0)
            self.assertNotIn('private-source-output',out+err)
            calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
            self.assertTrue(any(row[:1]==['rmi'] and row[-1].startswith('sha256:') for row in calls))
            images=json.loads((self.root/'images.json').read_text())
            self.assertFalse(any('io.lexiflow.source-tools-owner' in row['labels'] for row in images.values()))
            self.assertFalse(any(row[:1]==['rmi'] and ('--force' in row or '-f' in row) for row in calls))
        finally:
            if proc.poll() is None: proc.kill();proc.communicate()

    def test_legacy_empty_lock_requires_confirmation_and_preserves_data(self):
        self.prepare_ready_fixture()
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
        self.prepare_ready_fixture()
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':os.getpid(),'token':'a'*32,'childPid':None}
        text=json.dumps(owner,separators=(',',':'))
        (self.kit/'.lock/owner.json').write_text(text)
        result=self.invoke('recover','--confirm-stopped');self.assertNotEqual(result.returncode,0)
        self.assertEqual(text,(self.kit/'.lock/owner.json').read_text())
    def test_existing_claim_refuses_recovery_without_touching_locks(self):
        self.prepare_ready_fixture()
        (self.kit/'.lock').mkdir()
        (self.kit/'.lock-claim').mkdir()
        result=self.invoke('recover','--confirm-stopped')
        self.assertNotEqual(result.returncode,0)
        self.assertTrue((self.kit/'.lock').is_dir())
        self.assertTrue((self.kit/'.lock-claim').is_dir())
    def test_dead_owned_lock_is_reclaimed_without_confirmation(self):
        self.prepare_ready_fixture()
        old=subprocess.Popen([shutil.which('node'),'-e','']);old.wait()
        state=json.loads((self.kit/'state.json').read_text())
        (self.kit/'.lock').mkdir()
        owner={'schema':1,'root':str(self.kit),'installation':state['id'],'pid':old.pid,'token':'b'*32,'childPid':None}
        (self.kit/'.lock/owner.json').write_text(json.dumps(owner,separators=(',',':')))
        result=self.invoke('up')
        self.assertEqual(result.returncode,0,result.stderr)
    def test_sigkill_lock_windows_retry_without_permanent_claim(self):
        self.prepare_ready_fixture()
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
                argv = [shutil.which('node'), '--import', str(self.preload), '--import', str(crash), str(self.repo/'ops/podman/commands/manage-podman-installation.mjs'), 'up', '--dir', str(self.kit)]
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
