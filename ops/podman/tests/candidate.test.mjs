import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { assembleReleaseCandidate } from '../../release/candidate.mjs';
import { resolveBuildIdentity } from '../../release/version.mjs';
import { extensionBytes } from '../../release/tests/zip-fixture.mjs';
import { readCandidate, installCandidateArtifacts } from '../candidate.mjs';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');

async function assembled(baseVersion = '2.0.0-SNAPSHOT', sqlText = 'synthetic schema\n') {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-install-')));
  const out = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-output-')));
  const artifactRoot = path.join(root, 'input'); await fs.mkdir(path.join(root, 'ops/release'), { recursive: true }); await fs.mkdir(artifactRoot);
  await fs.writeFile(path.join(root, '.gitignore'), '/input/\n/images/\n/output/\n/descriptor.json\n');
  await fs.writeFile(path.join(root, 'ops/release/version.txt'), `${baseVersion}\n`);
  for (const file of ['version.mjs', 'runtime-entry.mjs', 'runtime-verification.sh', 'lifecycle.mjs', 'lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh'])
    await fs.writeFile(path.join(root, 'ops/release', file), await fs.readFile(path.join(repo, 'ops/release', file)));
  execFileSync('git', ['init', '-q', root]); execFileSync('git', ['-C', root, 'add', '.']);
  execFileSync('git', ['-C', root, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'clean candidate fixture']);
  const identity = resolveBuildIdentity(root), commit = execFileSync('git', ['-C', root, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const licenses = [], records = [], components = ['LexiFlow', 'extension-third-party', 'API-runtime', 'PostgreSQL', 'dataset'];
  const ids = ['lexiflow', 'extension', 'api', 'postgres', 'dataset'];
  for (let i = 0; i < ids.length; i++) {
    const noticePath = `licenses/${ids[i]}.txt`, bytes = Buffer.from(`Synthetic ${ids[i]} notice\n`);
    await fs.mkdir(path.dirname(path.join(artifactRoot, noticePath)), { recursive: true }); await fs.writeFile(path.join(artifactRoot, noticePath), bytes);
    licenses.push({ id: ids[i], component: components[i], licenseId: `TEST-${ids[i]}`, licenseName: `Test ${ids[i]}`, sourceUrl: `https://example.invalid/${ids[i]}`, noticePath, noticeBytes: bytes.length, noticeSha256: hash(bytes) });
    records.push({ role: 'license', path: noticePath, bytes: bytes.length, sha256: hash(bytes), licenseIds: [ids[i]] });
  }
  const shardInputs = [];
  for (const platform of ['linux/amd64', 'linux/arm64']) {
    const shard = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'lexiflow-candidate-shard-'))), shardRecords = [];
    for (const [role, filename, imageDigest, licenseIds] of [
      ['api-image', 'api-image.tar', `sha256:${hash(identity.buildId + ':api').slice(0, 64)}`, ['lexiflow', 'api']],
      ['postgres-image', 'postgres-image.tar', `sha256:${hash(identity.buildId + ':postgres').slice(0, 64)}`, ['postgres']],
    ]) {
      const relative = `images/${platform.replace('/', '-')}/${filename}`, bytes = Buffer.from(`${platform}:${role}`);
      await fs.mkdir(path.dirname(path.join(artifactRoot, relative)), { recursive: true }); await fs.writeFile(path.join(artifactRoot, relative), bytes);
      const record = { role, platform, path: relative, bytes: bytes.length, sha256: hash(bytes), imageDigest, licenseIds };
      records.push(record); shardRecords.push(record);
      const copy = path.join(shard, relative); await fs.mkdir(path.dirname(copy), { recursive: true }); await fs.writeFile(copy, bytes);
    }
    const baseImages = [{ platform, javaRuntime: { reference: `registry.example.invalid/java/runtime@sha256:${'1'.repeat(64)}`, imageId: `sha256:${'c'.repeat(64)}` }, postgresRuntime: { reference: `registry.example.invalid/postgres/runtime@sha256:${'2'.repeat(64)}`, imageId: `sha256:${'d'.repeat(64)}` } }];
    const shardManifest = { schemaVersion: 1, kind: 'lexiflow-image-candidate', buildIdentity: identity, softwareVersion: identity.softwareVersion, sourceCommit: commit,
      platform, buildInputSha256: 'e'.repeat(64), baseImages, artifacts: shardRecords.map(({ licenseIds: _ids, ...item }) => item) };
    const marker = Buffer.from(`${JSON.stringify(shardManifest, null, 2)}\n`); await fs.writeFile(path.join(shard, 'candidate.json'), marker);
    shardInputs.push({ directory: shard, sha256: hash(marker) });
  }
  const add = async (role, relative, bytes, licenseIds, metadata) => {
    const target = path.join(artifactRoot, relative); await fs.mkdir(path.dirname(target), { recursive: true }); await fs.writeFile(target, bytes);
    records.push({ role, path: relative, bytes: bytes.length, sha256: hash(bytes), licenseIds, ...(metadata ? { metadata } : {}) });
  };
  const compose = await fs.readFile(path.join(repo, 'ops/docker/compose.yaml'));
  await add('compose', 'compose.yaml', compose, ['lexiflow']); await add('sql', 'database/schema.sql', Buffer.from(sqlText), ['lexiflow']);
  const dataset = { releaseId: 'release-test', preparationId: 'prep-test', ruleId: 'rules-test' };
  await add('dataset', 'data/dataset.zip', Buffer.from('synthetic dataset'), ['dataset'], { ...dataset, sqlVersion: 'sql-test' });
  await add('extension', 'extension.zip', extensionBytes(identity), ['lexiflow', 'extension'], { softwareVersion: identity.softwareVersion, sourceCommit: identity.sourceCommit });
  const descriptor = { schemaVersion: 1, buildIdentity: identity, softwareVersion: identity.softwareVersion, sourceCommit: commit, apiContract: 'api-test', sqlVersion: 'sql-test', dataset, platforms: ['linux/amd64', 'linux/arm64'], artifacts: records, licenses };
  const result = await assembleReleaseCandidate({ repoRoot: root, imageCandidates: shardInputs, artifactRoot, descriptor, outputParent: out });
  const marker = await fs.readFile(path.join(result.candidateDirectory, 'candidate.json'));
  return { ...result, input: { candidateDirectory: result.candidateDirectory, candidateSha256: hash(marker) }, identity, root, out, shardInputs,
    cleanup: async () => { await fs.rm(root, { recursive: true, force: true }); await fs.rm(out, { recursive: true, force: true }); for (const shard of shardInputs) await fs.rm(shard.directory, { recursive: true, force: true }); } };
}

test('candidate adapter consumes a clean assembled archive and loads only inspected ARM64 identities', async t => {
  const f = await assembled(); t.after(f.cleanup);
  const candidate = await readCandidate(f.input);
  assert.equal(candidate.candidateSha256, f.input.candidateSha256);
  const ids = { api: candidate.artifacts['api-image:linux/arm64'].imageDigest, postgres: candidate.artifacts['postgres-image:linux/arm64'].imageDigest }, calls = [];
  const run = async (_cmd, argv, options = {}) => {
    calls.push(argv);
    if (argv[0] === 'images') return JSON.stringify([{ Id: ids.api }, { Id: ids.postgres }]);
    if (argv[0] === 'image' && argv[1] === 'inspect') {
      const id = argv.at(-1);
      return JSON.stringify([{ Id: id, Os: 'linux', Architecture: 'arm64', Config: { Labels: {
        'org.opencontainers.image.version': f.identity.softwareVersion, 'org.opencontainers.image.revision': f.identity.sourceCommit,
        'io.lexiflow.build-id': f.identity.buildId, 'io.lexiflow.source-sha256': f.identity.sourceSha256,
      } } }]);
    }
    return '';
  };
  const installDir = path.join(f.root, 'installation'); await fs.mkdir(installDir);
  const result = await installCandidateArtifacts(candidate, { run, installDir, apiPort: 18080 });
  assert.equal(result.apiImage, ids.api); assert.equal(result.postgresImage, ids.postgres);
  assert.ok(calls.some(argv => argv[0] === 'load'));
  assert.equal(await fs.readFile(path.join(installDir, 'extension/manifest.json'), 'utf8').then(text => JSON.parse(text).version_name), f.identity.softwareVersion);
  await assert.rejects(installCandidateArtifacts(candidate, { run, installDir, apiPort: 18081 }), /CANDIDATE_API_PORT_FIXED/);
  await assert.rejects(readCandidate({ ...f.input, candidateSha256: '0'.repeat(64) }), /CANDIDATE/);
  const prep = path.join(f.root, 'upgrade-prep'); await fs.mkdir(prep);
  const before = calls.filter(argv => argv[0] === 'load').length;
  await installCandidateArtifacts(candidate, { run, installDir: prep, apiPort: 18080, includePostgres: false });
  assert.equal(calls.filter(argv => argv[0] === 'load').length, before + 1, 'upgrade preparation must not load PostgreSQL');
  await fs.writeFile(path.join(prep, 'extension/unknown'), 'private');
  await assert.rejects(installCandidateArtifacts(candidate, { run, installDir: prep, apiPort: 18080, includePostgres: false }), /EXTENSION_TARGET_EXISTS/);
  assert.equal(await fs.readFile(path.join(prep, 'extension/unknown'), 'utf8'), 'private');
});

test('candidate request rejects caller-injected fields', async () => {
  await assert.rejects(readCandidate({ candidateDirectory: '/tmp/candidate', candidateSha256: 'a'.repeat(64), pass: true }), /ARGUMENTS_INVALID/);
});

test('runLocal installs, repeats, upgrades and no-ops candidates through shared lifecycle with isolated Podman/network substitutes', async t => {
  const first = await assembled('2.0.0-SNAPSHOT'), second = await assembled('2.0.1-SNAPSHOT');
  const incompatible = await assembled('2.0.2-SNAPSHOT', 'incompatible schema\n');
  t.after(async () => { await first.cleanup(); await second.cleanup(); await incompatible.cleanup(); });
  const root = path.join(first.root, 'local-run'), dir = path.join(root, 'private-install'), bin = path.join(root, 'bin');
  await fs.mkdir(bin, { recursive: true }); const callsFile = path.join(root, 'podman-calls.jsonl');
  const badLabelMarker = path.join(root, 'bad-label-once'), failMarker = path.join(root, 'api-start-failure');
  const cancelMarker = path.join(root, 'cancel-once');
  const imageMap = {};
  for (const candidate of [first, second, incompatible]) for (const role of ['api-image', 'postgres-image']) {
    const key = role + ':linux/arm64';
    const proof = await readCandidate(candidate.input), artifact = proof.artifacts[key];
    imageMap[artifact.imageDigest] = { Id: artifact.imageDigest, Os: 'linux', Architecture: 'arm64', Config: { Labels: {
      'org.opencontainers.image.version': proof.buildIdentity.softwareVersion, 'org.opencontainers.image.revision': proof.buildIdentity.sourceCommit,
      'io.lexiflow.build-id': proof.buildIdentity.buildId, 'io.lexiflow.source-sha256': proof.buildIdentity.sourceSha256,
    } } };
  }
  const podman = '#!/usr/bin/env python3\n';
  const podmanBody = String.raw`
import json, os, signal, sys
from pathlib import Path
CALLS=Path(${JSON.stringify(callsFile)}); images=json.loads(${JSON.stringify(JSON.stringify(imageMap))}); kit=Path.cwd()
BAD_LABEL=Path(${JSON.stringify(badLabelMarker)}); FAIL_API=Path(${JSON.stringify(failMarker)}); CANCEL=Path(${JSON.stringify(cancelMarker)})
a=sys.argv[1:]; CALLS.open('a').write(json.dumps(a)+'\n')
labels={}
if a[:1]==['info']: print('{}')
elif a[:2]==['compose','version']: print('podman compose test')
elif a[:1]==['load']:
    if CANCEL.exists(): CANCEL.unlink(); os.kill(os.getppid(),signal.SIGINT)
    print('UNTRUSTED load output ignored')
elif a[:2]==['image','inspect']:
    fmt=a[a.index('--format')+1] if '--format' in a else None; key=a[-1]; item=images.get(key)
    if item is None:
        env=(kit/'release.env').read_text(); key=next(x.split('=',1)[1] for x in env.splitlines() if x.startswith('LEXIFLOW_API_IMAGE=')); item=images[key]
    if BAD_LABEL.exists() and fmt is None: BAD_LABEL.unlink(); item={**item,'Config':{**item['Config'],'Labels':{**item['Config']['Labels'],'io.lexiflow.build-id':'0'*64}}}
    if fmt=='{{json .Labels}}': print(json.dumps(item['Config']['Labels']))
    elif fmt=='{{json .Os}} {{json .Architecture}}': print('"linux" "arm64"')
    elif fmt=='{{.Id}}': print(item['Id'])
    else: print(json.dumps([item]))
elif a[:1]==['ps']:
    if '-a' in a: print('[]')
    else:
        state=json.loads((kit/'state.json').read_text()); env=(kit/'release.env').read_text()
        key=next(x.split('=',1)[1] for x in env.splitlines() if x.startswith('LEXIFLOW_API_IMAGE='))
        print(json.dumps([{'Id':'f'*64,'Labels':{'lexiflow.installation':state['id'],'com.docker.compose.project':state['project'],'com.docker.compose.service':'api'}}]))
elif a[:1] in (['volume'],['network']): print('[]')
elif a[:1]==['inspect']:
    env=(kit/'release.env').read_text(); key=next(x.split('=',1)[1] for x in env.splitlines() if x.startswith('LEXIFLOW_API_IMAGE=')); print(images[key]['Id'])
elif a[:1]==['compose'] and 'logs' in a: print('synthetic logs')
elif a[:1]==['compose'] and 'ps' in a: print('synthetic status')
elif a[:1]==['compose'] and 'run' in a: pass
elif a[:1]==['compose'] and 'config' in a: print('valid')
elif a[:1]==['compose'] and 'up' in a and a[-1]=='api' and FAIL_API.exists():
    FAIL_API.unlink(); sys.exit(8)
elif a[:1]==['compose'] and 'up' in a: pass
elif a[:1]==['compose'] and 'stop' in a: pass
else: print('unexpected command '+json.dumps(a), file=sys.stderr); sys.exit(7)
`;
  const script = podman + podmanBody;
  const podmanFile = path.join(bin, 'podman'); await fs.writeFile(podmanFile, script); await fs.chmod(podmanFile, 0o755);
  const preload = path.join(root, 'preload.mjs');
  await fs.writeFile(preload, `import fs from 'node:fs'; import os from 'node:os'; import net from 'node:net'; import http from 'node:http'; import { EventEmitter } from 'node:events'; import { Readable } from 'node:stream';
os.platform=()=> 'darwin'; os.arch=()=> 'arm64';
net.createServer=()=>({ once(){return this;}, listen(_port,_host,cb){setImmediate(cb);return this;}, close(cb){setImmediate(cb);return this;} });
http.get=(options,callback)=>{const req=new EventEmitter(); req.destroy=(err)=>{if(err)req.emit('error',err);req.emit('close');}; setImmediate(()=>{const env=fs.readFileSync(process.env.INSTALL_DIR+'/release.env','utf8');const image=env.match(/^LEXIFLOW_API_IMAGE=(.+)$/m)?.[1];const versions=JSON.parse(process.env.IMAGE_VERSIONS);const value=options.path.endsWith('/readiness')?{status:'UP'}:{mode:'formal',ready:true,reason:'OK',softwareVersion:versions[image],apiContract:'caption-hints.v1',datasetVersion:7};const res=Readable.from([Buffer.from(JSON.stringify(value))]);res.statusCode=200;res.on('end',()=>req.emit('close'));callback(res);});return req;};`);
  const source = `import fs from 'node:fs'; import path from 'node:path'; import {runLocal} from ${JSON.stringify(pathToFileURL(path.join(repo,'ops/podman/local.mjs')).href)};\n`+
    `if(await runLocal(['install'],${JSON.stringify(first.input)})===0)throw new Error('candidate default dir accepted'); if(await runLocal(['install','--dir',${JSON.stringify(dir)},'--api-port','18081'],${JSON.stringify(first.input)})===0)throw new Error('candidate port override accepted'); if(fs.existsSync(${JSON.stringify(dir)}))throw new Error('invalid inputs created installation');\n`+
    `fs.writeFileSync(${JSON.stringify(badLabelMarker)},'1'); const rejected=await runLocal(['install','--dir',${JSON.stringify(dir)}],${JSON.stringify(first.input)}); if(rejected===0)throw new Error('bad image labels accepted');\n`+
    `const one=await runLocal(['install','--dir',${JSON.stringify(dir)}],${JSON.stringify(first.input)}); if(one!==0)process.exitCode=one;\n`+
    `const repeat=await runLocal(['install','--dir',${JSON.stringify(dir)}],${JSON.stringify(first.input)}); if(repeat!==0)process.exitCode=repeat;\n`+
    `if(await runLocal(['upgrade','--dir',${JSON.stringify(dir)}],${JSON.stringify(incompatible.input)})===0)throw new Error('incompatible SQL accepted');\n`+
    `const beforeUpgrade=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')); const oldPassword=fs.readFileSync(${JSON.stringify(path.join(dir,'secrets/app-password'))}); const oldDataset=fs.readFileSync(path.join(beforeUpgrade.dataset,'dataset.zip')); fs.writeFileSync(${JSON.stringify(path.join(root,'before-upgrade.json'))},JSON.stringify({state:beforeUpgrade,password:oldPassword.toString('hex'),dataset:oldDataset.toString('hex')})); fs.writeFileSync(${JSON.stringify(failMarker)},'1');\n`+
    `const failed=await runLocal(['upgrade','--dir',${JSON.stringify(dir)}],${JSON.stringify(second.input)}); if(failed===0)throw new Error('failed API start accepted');\n`+
    `const restored=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')); if(restored.buildIdentity.buildId!==beforeUpgrade.buildIdentity.buildId||restored.apiImage!==beforeUpgrade.apiImage||restored.postgresImage!==beforeUpgrade.postgresImage||restored.apiPort!==beforeUpgrade.apiPort||restored.dbPort!==beforeUpgrade.dbPort)throw new Error('upgrade recovery drift'); if(!fs.readFileSync(${JSON.stringify(path.join(dir,'secrets/app-password'))}).equals(oldPassword))throw new Error('password changed');\n`+
    `fs.writeFileSync(${JSON.stringify(cancelMarker)},'1'); const cancelled=await runLocal(['upgrade','--dir',${JSON.stringify(dir)}],${JSON.stringify(second.input)}); if(cancelled===0)throw new Error('cancelled upgrade accepted'); if(JSON.parse(fs.readFileSync(${JSON.stringify(path.join(dir,'state.json'))},'utf8')).apiImage!==beforeUpgrade.apiImage)throw new Error('cancel recovery drift');\n`+
    `const upgrade=await runLocal(['upgrade','--dir',${JSON.stringify(dir)}],${JSON.stringify(second.input)}); if(upgrade!==0)process.exitCode=upgrade;\n`+
    `const noop=await runLocal(['upgrade','--dir',${JSON.stringify(dir)}],${JSON.stringify(second.input)}); if(noop!==0)process.exitCode=noop;\n`+
    `if(await runLocal(['install','--dir',${JSON.stringify(dir)}],${JSON.stringify(second.input)})!==0)throw new Error('repeat install after upgrade rejected');\n`+
    `for(const action of ['up','status','logs','stop']) { const code=await runLocal([action,'--dir',${JSON.stringify(dir)}]); if(code!==0)throw new Error(action+' requires candidate again or failed'); }\n`;
  const result = spawnSync(process.execPath, ['--import', preload, '--input-type=module', '-e', source], {
    encoding: 'utf8', timeout: 240000, env: { ...process.env, INSTALL_DIR: dir, IMAGE_VERSIONS: JSON.stringify(Object.fromEntries(Object.entries(imageMap).map(([id, image]) => [id, image.Config.Labels['org.opencontainers.image.version']]))), PATH: `${bin}${path.delimiter}${process.env.PATH}` },
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const installed = JSON.parse(await fs.readFile(path.join(dir, 'state.json'), 'utf8'));
  assert.equal(installed.phase, 'ready'); assert.equal(installed.version, (await readCandidate(second.input)).buildIdentity.softwareVersion);
  assert.equal(installed.apiPort, 18080); assert.equal(installed.packageType, 'release-package');
  const before = JSON.parse(await fs.readFile(path.join(root, 'before-upgrade.json'), 'utf8'));
  assert.equal(installed.id, before.state.id); assert.equal(installed.project, before.state.project);
  assert.equal(installed.postgresImage, before.state.postgresImage); assert.equal(installed.apiPort, before.state.apiPort); assert.equal(installed.dbPort, before.state.dbPort);
  assert.deepEqual(installed.datasetIdentity, before.state.datasetIdentity);
  assert.equal((await fs.readFile(path.join(dir, 'secrets/app-password'))).toString('hex'), before.password);
  assert.equal((await fs.readFile(path.join(installed.dataset, 'dataset.zip'))).toString('hex'), before.dataset);
  assert.equal((await fs.readFile(path.join(dir, 'infra/postgres/schema.sql'), 'utf8')), 'synthetic schema\n');
  const calls = (await fs.readFile(callsFile, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
  assert.equal(calls.filter(row => row[0] === 'load').length, 6, 'invalid install, valid install API+PG, failed/cancelled/successful upgrade API; repeat/no-op do not load');
  assert.equal(calls.filter(row => row.includes('initialize')).length, 1);
  assert.equal(calls.some(row => row[0] === 'images'), false);
});
