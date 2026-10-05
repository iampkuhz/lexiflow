import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';
import { chmod, mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolveBuildIdentity } from '../version.mjs';
import { jarBytes, extensionBytes } from './zip-fixture.mjs';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const cli = path.join(repo, 'ops/release/pipeline.mjs');
const sha = (b) => createHash('sha256').update(b).digest('hex');
const git = (cwd, args) => execFileSync('git', ['-C', cwd, ...args], { timeout: 10000, stdio: ['ignore', 'pipe', 'pipe'] });
const run = (args, options = {}) => new Promise((resolve) => {
    const child = spawn(process.execPath, [cli, ...args], { ...options, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    let timer = setTimeout(() => child.kill('SIGKILL'), 20000);
    child.stdout.on('data', (b) => { stdout += b; });
    child.stderr.on('data', (b) => { stderr += b; });
    child.on('error', (error) => { clearTimeout(timer); resolve({ code: null, stdout, stderr, error: error.message }); });
    child.on('close', (code, signal) => { clearTimeout(timer); resolve({ code, signal, stdout, stderr }); });
});
async function fixture() {
    const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-pipeline-src-')));
    const artifactRoot = path.join(root, 'artifact');
    await mkdir(artifactRoot);
    await mkdir(path.join(root, 'ops/docker'), { recursive: true });
    await mkdir(path.join(root, 'ops/release'), { recursive: true });
    const templates = ['.dockerignore', 'Dockerfile', 'Dockerfile.postgres', 'bootstrap.sh', 'entrypoint.sh'];
    for (const n of templates)
        await writeFile(path.join(root, 'ops/docker', n), await readFile(path.join(repo, 'ops/docker', n)));
    const digest = (c) => `sha256:${c.repeat(64)}`;
    const baseImages = ['linux/amd64', 'linux/arm64'].map((platform, i) => ({ platform,
        javaRuntime: { reference: `registry.example.invalid/java@${digest(i ? 'c' : 'a')}`, imageId: digest(i ? 'c' : 'a') },
        postgresRuntime: { reference: `registry.example.invalid/postgres@${digest(i ? 'd' : 'b')}`, imageId: digest(i ? 'd' : 'b') } }));
    await writeFile(path.join(root, 'ops/docker/base-images.json'), `${JSON.stringify({ schemaVersion: 1, baseImages }, null, 2)}\n`);
    for (const n of ['version.mjs', 'manifest.mjs', 'runtime-entry.mjs', 'runtime-verification.sh', 'lifecycle.mjs', 'lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh'])
        await writeFile(path.join(root, 'ops/release', n), await readFile(path.join(repo, 'ops/release', n)));
    await writeFile(path.join(root, 'ops/release/version.txt'), '2.0.0\n');
    await writeFile(path.join(root, '.gitignore'), '/artifact/\n');
    await writeFile(path.join(root, 'ops/docker/.gitignore'), '');
    execFileSync('git', ['init', '-q', root], { timeout: 10000 });
    git(root, ['add', '.']);
    git(root, ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture']);
    const commit = git(root, ['rev-parse', 'HEAD']).toString().trim();
    const jar = jarBytes(resolveBuildIdentity(root));
    await writeFile(path.join(artifactRoot, 'app.jar'), jar);
    const descriptor = { schemaVersion: 1, softwareVersion: '2.0.0', sourceCommit: commit, jar: { path: 'app.jar', bytes: jar.length, sha256: sha(jar) }, baseImages };
    const out = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-pipeline-out-')));
    const fake = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-pipeline-docker-')));
    const bin = path.join(fake, 'bin');
    await mkdir(bin);
    const finalIds = { 'linux/amd64': [digest('e'), digest('f')], 'linux/arm64': [digest('1'), digest('2')] };
    const state = { refs: Object.fromEntries(baseImages.flatMap((x) => [[x.javaRuntime.reference, { platform: x.platform, id: x.javaRuntime.imageId }], [x.postgresRuntime.reference, { platform: x.platform, id: x.postgresRuntime.imageId }]])), finalIds };
    await writeFile(path.join(fake, 'state.json'), JSON.stringify(state));
    const driver = path.join(fake, 'driver.mjs');
    await writeFile(driver, `import fs from 'node:fs';import path from 'node:path';const root=path.dirname(new URL(import.meta.url).pathname),s=JSON.parse(fs.readFileSync(path.join(root,'state.json'))),a=process.argv.slice(2).slice(4),op=a[0];fs.appendFileSync(path.join(root,'calls.jsonl'),JSON.stringify({a,op})+'\\n');
if(s.fail===op){process.stderr.write('SECRET-DO-NOT-LEAK');process.exit(7)}
if(op==='version')process.stdout.write(JSON.stringify({Server:{Version:'fake'}}));else if(op==='info')process.stdout.write(JSON.stringify({OSType:'linux',Architecture:s.architecture||'amd64'}));
else if(op==='image'&&a[1]==='inspect'){let x=s.refs[a[2]],id=x?.id,p=x?.platform;for(const [q,ids] of Object.entries(s.finalIds))if(ids.includes(a[2])){id=a[2];p=q}if(!id){process.exit(8)}process.stdout.write(JSON.stringify([{Id:id,Os:'linux',Architecture:p.endsWith('amd64')?'amd64':'arm64',Config:{Labels:s.builtLabels?.[id]}}]))}
else if(op==='build'){const iid=a[a.indexOf('--iidfile')+1],p=a[a.indexOf('--platform')+1],ix=iid.startsWith('api')?0:1,id=s.finalIds[p][ix],labels={};for(let i=0;i<a.length;i++)if(a[i]==='--label'){const value=a[i+1],eq=value.indexOf('=');labels[value.slice(0,eq)]=value.slice(eq+1)}s.builtLabels??={};s.builtLabels[id]=labels;fs.writeFileSync(path.join(root,'state.json'),JSON.stringify(s));fs.writeFileSync(path.join(process.cwd(),iid),id+'\\n')}
else if(op==='image'&&a[1]==='save')fs.writeFileSync(a[a.indexOf('--output')+1],'synthetic archive');else process.exit(9);`);
    await writeFile(path.join(bin, 'docker'), `#!/bin/sh\nexec '${process.execPath}' '${driver}' "$@"\n`);
    await chmod(path.join(bin, 'docker'), 0o700);
    const env = { PATH: `${bin}:${process.env.PATH}`, HOME: '/private/unused', DOCKER_HOST: 'tcp://evil', DOCKER_CONFIG: '/evil', COMPOSE_FILE: '/evil', BUILDX_CONFIG: '/evil', SSH_AUTH_SOCK: '/evil' };
    async function request(name, value) { const f = path.join(fake, name); await writeFile(f, JSON.stringify(value)); return f; }
    const imagesReq = await request('images-request.json', { repoRoot: root, artifactRoot, descriptor, outputParent: out, endpoint: 'unix:///tmp/synthetic.sock', platform: 'linux/amd64' });
    return { root, artifactRoot, out, fake, env, state, imagesReq, request, descriptor, cleanup: async () => { await rm(root, { recursive: true, force: true }); await rm(out, { recursive: true, force: true }); await rm(fake, { recursive: true, force: true }); } };
}
function assertShape(result, mode, status, reason) { assert.equal(result.stderr, ''); const rows = result.stdout.trim().split('\n'); assert.equal(rows.length, 1); const value = JSON.parse(rows[0]); assert.equal(value.status, status); assert.equal(value.scope, 'candidate-only'); assert.equal(value.operation, mode); if (reason)
    assert.equal(value.reason, reason);
else
    assert.ok(value.candidateDirectory); return value; }
test('CLI images 在真实子进程中完成合成双平台候选构建', async () => { const f = await fixture(); try {
    const r = await run(['images', f.imagesReq], { env: f.env });
    const v = assertShape(r, 'images', 'PASS');
    assert.equal(r.code, 0);
    const c = JSON.parse(await readFile(path.join(v.candidateDirectory, 'candidate.json'), 'utf8'));
    assert.equal(c.artifacts.length, 2);
    const calls = (await readFile(path.join(f.fake, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(calls.filter(x => x.op === 'build').length, 2);
    assert.equal(calls.filter(x => x.op === 'image' && x.a[1] === 'save').length, 2);
}
finally {
    await f.cleanup();
} });
test('CLI images 输出实际交接 assemble 并保留匹配候选目录', async () => {
    const f = await fixture();
    try {
        const built = await run(['images', f.imagesReq], { env: f.env });
        assert.equal(built.code, 0);
        const imageDir = assertShape(built, 'images', 'PASS').candidateDirectory;
        const armRequest = await f.request('images-arm64-request.json', { ...JSON.parse(await readFile(f.imagesReq, 'utf8')), platform: 'linux/arm64' });
        await writeFile(path.join(f.fake, 'state.json'), JSON.stringify({ ...f.state, architecture: 'arm64' }));
        const armBuilt = await run(['images', armRequest], { env: f.env });
        const armDir = assertShape(armBuilt, 'images', 'PASS').candidateDirectory;
        const ic = JSON.parse(await readFile(path.join(imageDir, 'candidate.json'), 'utf8'));
        const records = [...ic.artifacts, ...JSON.parse(await readFile(path.join(armDir, 'candidate.json'), 'utf8')).artifacts];
        const assemblyParent = path.join(f.fake, 'assembly-output');
        await mkdir(assemblyParent);
        const artifacts = [], licenses = [];
        for (const [id, component] of [['lexiflow', 'LexiFlow'], ['ext', 'extension-third-party'], ['api', 'API-runtime'], ['pg', 'PostgreSQL'], ['data', 'dataset']]) {
            const notice = `licenses/${id}.txt`, data = Buffer.from(`synthetic ${id}`);
            await mkdir(path.dirname(path.join(f.artifactRoot, notice)), { recursive: true });
            await writeFile(path.join(f.artifactRoot, notice), data);
            licenses.push({ id, component, licenseId: `TEST-${id}`, licenseName: `Synthetic ${id}`, sourceUrl: `https://example.invalid/${id}`, noticePath: notice, noticeBytes: data.length, noticeSha256: sha(data) });
            artifacts.push({ role: 'license', path: notice, bytes: data.length, sha256: sha(data), licenseIds: [id] });
        }
        const add = (role, p, data, more = {}) => { artifacts.push({ role, path: p, bytes: data.length, sha256: sha(data), licenseIds: ['lexiflow'], ...more }); return writeFile(path.join(f.artifactRoot, p), data); };
        for (const x of records) {
            artifacts.push({ ...x, licenseIds: x.role === 'api-image' ? ['lexiflow', 'api'] : ['pg'] });
            await mkdir(path.dirname(path.join(f.artifactRoot, x.path)), { recursive: true });
            await writeFile(path.join(f.artifactRoot, x.path), await readFile(path.join(x.platform === 'linux/arm64' ? armDir : imageDir, x.path)));
        }
        const extras = [['compose', 'compose.yaml', 'services: {}'], ['sql', 'schema.sql', 'CREATE TABLE t(i int);'], ['dataset', 'dataset.bin', 'data']];
        for (const [role, p, s] of extras)
            await add(role, p, Buffer.from(s), role === 'dataset' ? { metadata: { releaseId: 'rel', preparationId: 'prep', ruleId: 'rule', sqlVersion: 'sql' }, licenseIds: ['data'] } : {});
        await add('extension', 'extension.zip', extensionBytes(ic.buildIdentity), { metadata: { softwareVersion: '2.0.0', sourceCommit: ic.sourceCommit }, licenseIds: ['lexiflow', 'ext'] });
        for (const [id, component] of [['lexiflow', 'LexiFlow'], ['ext', 'extension-third-party'], ['api', 'API-runtime'], ['pg', 'PostgreSQL'], ['data', 'dataset']])
            licenses.find(x => x.id === id).component = component;
        const descriptor = { schemaVersion: 1, buildIdentity: ic.buildIdentity, softwareVersion: '2.0.0', sourceCommit: ic.sourceCommit, apiContract: 'api', sqlVersion: 'sql', dataset: { releaseId: 'rel', preparationId: 'prep', ruleId: 'rule' }, platforms: ['linux/amd64', 'linux/arm64'], artifacts, licenses };
        const req = await f.request('assemble-request.json', { repoRoot: f.root, imageCandidates: [{ directory: imageDir, sha256: sha(await readFile(path.join(imageDir, 'candidate.json'))) }, { directory: armDir, sha256: sha(await readFile(path.join(armDir, 'candidate.json'))) }], artifactRoot: f.artifactRoot, descriptor, outputParent: assemblyParent });
        const r = await run(['assemble', req]);
        const v = assertShape(r, 'assemble', 'PASS');
        assert.equal(r.code, 0);
        assert.ok(JSON.parse(await readFile(path.join(v.candidateDirectory, 'candidate.json'), 'utf8')).kind === 'lexiflow-release-candidate');
        const marker = await readFile(path.join(v.candidateDirectory, 'candidate.json'));
        const verifyReq = await f.request('verify-request.json', { candidateDirectory: v.candidateDirectory, candidateSha256: sha(marker) });
        const verified = await run(['verify', verifyReq]);
        assert.equal(verified.code, 0);
        const proof = JSON.parse(verified.stdout);
        assert.equal(proof.status, 'PASS'); assert.equal(proof.scope, 'candidate-integrity-only'); assert.equal(proof.operation, 'verify');
        assert.equal(proof.candidateSha256, sha(marker)); assert.equal(Object.hasOwn(proof, 'formalEligible'), false);
        const badReq = await f.request('verify-extra-request.json', { candidateDirectory: v.candidateDirectory, candidateSha256: sha(marker), actor: 'test' });
        const rejected = await run(['verify', badReq]);
        assert.equal(rejected.code, 1); assert.deepEqual(JSON.parse(rejected.stdout), { status: 'FAIL', scope: 'candidate-integrity-only', operation: 'verify', reason: 'PIPELINE_OPERATION_FAILED' });
    }
    finally {
        await f.cleanup();
    }
});
test('参数与请求读取拒绝条件返回固定JSON错误', async () => { const f = await fixture(); try {
    for (const args of [[], ['wat', f.imagesReq], ['images', f.imagesReq, 'extra'], ['images', 'relative.json']]) {
        const r = await run(args);
        const v = JSON.parse(r.stdout);
        assert.equal(r.code, 1);
        assert.equal(v.status, 'FAIL');
        assert.equal(v.reason, 'PIPELINE_ARGUMENTS_INVALID');
    }
    for (const [name, body] of [['bad.json', '{'], ['large.json', ' '.repeat(2000001)]]) {
        const p = path.join(f.fake, name);
        await writeFile(p, body);
        const r = await run(['images', p], { env: f.env });
        assertShape(r, 'images', 'FAIL', 'PIPELINE_REQUEST_INVALID');
        assert.equal(r.code, 1);
    }
    const absent = path.join(f.fake, 'missing.json');
    let r = await run(['images', absent]);
    assertShape(r, 'images', 'FAIL', 'PIPELINE_REQUEST_INVALID');
    const dir = path.join(f.fake, 'directory');
    await mkdir(dir);
    r = await run(['images', dir]);
    assertShape(r, 'images', 'FAIL', 'PIPELINE_REQUEST_INVALID');
    const link = path.join(f.fake, 'link.json');
    await symlink(f.imagesReq, link);
    r = await run(['images', link]);
    assertShape(r, 'images', 'FAIL', 'PIPELINE_REQUEST_INVALID');
    const extra = await f.request('extra.json', { ...JSON.parse(await readFile(f.imagesReq, 'utf8')), surprise: true });
    r = await run(['images', extra], { env: f.env });
    assertShape(r, 'images', 'FAIL', 'PIPELINE_OPERATION_FAILED');
    assert.equal(r.stdout.includes('surprise'), false);
}
finally {
    await f.cleanup();
} });
test('dirty source 与下层Docker错误不回显原始异常', async () => { const f = await fixture(); try {
    const dirty = path.join(f.root, 'README.local');
    await writeFile(dirty, 'dirty');
    let r = await run(['images', f.imagesReq], { env: f.env });
    assertShape(r, 'images', 'FAIL', 'PIPELINE_OPERATION_FAILED');
    assert.equal(r.stdout.includes(dirty), false);
    await rm(dirty);
    const s = { ...f.state, fail: 'version' };
    await writeFile(path.join(f.fake, 'state.json'), JSON.stringify(s));
    r = await run(['images', f.imagesReq], { env: f.env });
    assertShape(r, 'images', 'FAIL', 'PIPELINE_OPERATION_FAILED');
    assert.equal(r.stdout.includes('SECRET'), false);
    assert.equal(r.stderr, '');
}
finally {
    await f.cleanup();
} });

// 文件类型拒绝必须发生在读取之前，FIFO 没有写入者时也不能挂起。
test('请求祖先链接及 FIFO 被拒绝，空 JSON 不冒充候选成功', async () => {
    const f = await fixture();
    try {
        const directoryLink = path.join(f.fake, 'directory-link');
        await symlink(f.fake, directoryLink);
        const fifo = path.join(f.fake, 'request.fifo');
        execFileSync('mkfifo', [fifo], { timeout: 5_000 });
        for (const request of [path.join(directoryLink, 'images-request.json'), fifo]) {
            const result = await run(['images', request]);
            assert.equal(result.code, 1);
            assertShape(result, 'images', 'FAIL', 'PIPELINE_REQUEST_INVALID');
        }
        const nullRequest = await f.request('null.json', null);
        const result = await run(['assemble', nullRequest]);
        assert.equal(result.code, 1);
        assertShape(result, 'assemble', 'FAIL', 'PIPELINE_OPERATION_FAILED');
    }
    finally { await f.cleanup(); }
});

test('导入不执行，符号链接 CLI 仍实际处理参数而非静默退出零', async () => {
    const imported = execFileSync(process.execPath,
        ['--input-type=module', '-e', `await import(${JSON.stringify(pathToFileURL(cli).href)})`],
        { timeout: 5_000, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    assert.equal(imported, '');
    const temporary = await realpath(await mkdtemp(path.join(os.tmpdir(), 'lexiflow-pipeline-link-')));
    try {
        const link = path.join(temporary, 'pipeline.mjs');
        await symlink(cli, link);
        assert.throws(() => execFileSync(process.execPath, [link, 'unknown'],
            { timeout: 5_000, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }), (error) => {
            assert.equal(error.status, 1);
            assert.equal(error.stderr, '');
            assert.deepEqual(JSON.parse(error.stdout), {
                status: 'FAIL', scope: 'candidate-only', operation: null,
                reason: 'PIPELINE_ARGUMENTS_INVALID',
            });
            return true;
        });
    }
    finally { await rm(temporary, { recursive: true, force: true }); }
});

test('verify参数、请求读取及自报权限字段始终保持候选完整性scope', async () => {
    const f = await fixture();
    try {
        for (const args of [['verify'], ['verify', 'relative.json'], ['verify', f.imagesReq, 'extra']]) {
            const r = await run(args);
            assert.equal(r.code, 1);
            assert.deepEqual(JSON.parse(r.stdout), { status: 'FAIL', scope: 'candidate-integrity-only', operation: 'verify', reason: 'PIPELINE_ARGUMENTS_INVALID' });
        }
        const missing = await run(['verify', path.join(f.fake, 'absent.json')]);
        assert.equal(missing.code, 1);
        assert.deepEqual(JSON.parse(missing.stdout), { status: 'FAIL', scope: 'candidate-integrity-only', operation: 'verify', reason: 'PIPELINE_REQUEST_INVALID' });
        const request = await f.request('verify-actor.json', { candidateDirectory: f.out, candidateSha256: 'a'.repeat(64), actor: 'self', runtime: { formalReleaseEligible: true } });
        const rejected = await run(['verify', request]);
        assert.equal(rejected.code, 1);
        assert.deepEqual(JSON.parse(rejected.stdout), { status: 'FAIL', scope: 'candidate-integrity-only', operation: 'verify', reason: 'PIPELINE_OPERATION_FAILED' });
    } finally { await f.cleanup(); }
});
