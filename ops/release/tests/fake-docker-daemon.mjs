#!/usr/bin/env node
// 生命周期合成 Docker daemon：只保存非秘密资源状态与动作摘要。
import { createHash } from 'node:crypto';
import { readFile, writeFile, appendFile } from 'node:fs/promises';

const [statePath, ledgerPath, controlPath] = process.argv.slice(2);
const argv = process.argv.slice(5);
const fail = () => process.exit(64);
const sha = (text) => createHash('sha256').update(text).digest('hex');
if (!statePath || !ledgerPath || !controlPath || !argv.length) fail();
function valueAfter(args, flag) { const at = args.indexOf(flag); return at < 0 ? null : args[at + 1]; }

async function load() { return JSON.parse(await readFile(statePath, 'utf8')); }
async function save(data) { await writeFile(statePath, JSON.stringify(data)); }
async function record(action, resource = null, daemon = null) {
  await appendFile(ledgerPath, `${JSON.stringify({ action, resource, daemon })}\n`);
}
function host(args) {
  if (args[0] !== '--host' || !args[1]?.startsWith('unix:///')) fail();
  return args.splice(0, 2)[1];
}
function renderResource(resource, kind) {
  const labels = resource.labels;
  if (kind === 'container') return `${resource.id}|/${resource.name}|${labels.project}|${labels.installation}|${labels.release}`;
  return `${resource.id}|${resource.name}|${labels.project}|${labels.installation}|${labels.release}`;
}
function makeResource(data, kind, name, project, installation, release, running = true) {
  const existing = data.resources.find((item) => item.kind === kind && item.name === name && item.labels.project === project);
  if (existing) {
    if (existing.labels.installation !== installation || existing.labels.release !== release) fail();
    if (kind === 'container') {
      existing.running = running;
      existing.health = running ? 'healthy' : 'none';
    }
    return;
  }
  const id = kind === 'volume' ? name : sha(`${kind}:${project}:${name}`);
  data.resources.push({ kind, id, name, project, installation, release, running,
    health: kind === 'container' && running ? 'healthy' : 'none',
    labels: { project, installation, release } });
}

try {
  const all = await load();
  const control = JSON.parse(await readFile(controlPath, 'utf8'));
  if (argv[0] === 'context' && argv[1] === 'inspect') {
    if (argv.includes('--format')) { console.log(control.contextEndpoint); await record('context-inspect'); process.exit(0); }
    fail();
  }
  const endpoint = host(argv);
  const daemonName = control.endpoints[endpoint];
  const data = all.daemons[daemonName];
  if (!data || !argv.length) fail();
  const [command, subcommand] = argv;
  if (command === 'compose') {
    const project = valueAfter(argv, '--project-name');
    const installation = process.env.LEXIFLOW_INSTALLATION_ID;
    const release = process.env.LEXIFLOW_RELEASE_KEY;
    const operationAt = argv.indexOf('-f') + 2;
    const operation = argv[operationAt];
    if (argv[1] === 'version') { console.log('Docker Compose version v2.0.0'); await record('compose-version',null,daemonName); process.exit(0); }
    if (control.composeFail === true) { await record('compose-failed', { project, operation },daemonName); process.exit(71); }
    if (!project || !installation || !release || !/^[a-f0-9]{32}$/.test(installation) || !/^[a-f0-9]{64}$/.test(release)) fail();
    if (operation === 'up') {
      const service = argv.at(-1);
      if (service === 'postgres') {
        makeResource(data, 'container', `${project}-postgres-1`, project, installation, release);
        makeResource(data, 'network', `${project}_private`, project, installation, release);
        makeResource(data, 'volume', `${project}_pgdata`, project, installation, release);
      } else if (service === 'api') {
        makeResource(data, 'container', `${project}-api-1`, project, installation, release);
      } else fail();
      await save(all); await record('compose-up', { project, installation, release, service },daemonName); process.exit(0);
    }
    if (operation === 'run') {
      const name = valueAfter(argv, '--name');
      const role = argv.at(-1);
      if (!name || role !== 'initialize' && role !== 'api') fail();
      if (role === 'api') makeResource(data, 'container', name, project, installation, release);
      await save(all); await record('compose-run', { project, installation, release, role, name },daemonName); process.exit(0);
    }
    fail();
  }
  if (command === 'info') {
    const format = valueAfter(argv, '--format');
    if (format === '{{.ID}}') {
      if (control.idQueryFail === true) { await record('info-failed',{format},daemonName); fail(); }
      console.log(control.idOverride ?? data.id);
      if (control.switchAfterIdQueries) {
        const {remaining,endpoint:switchEndpoint,target} = control.switchAfterIdQueries;
        if (remaining === 1) {
          control.endpoints[switchEndpoint] = target;
          delete control.switchAfterIdQueries;
        } else control.switchAfterIdQueries.remaining = remaining - 1;
        await writeFile(controlPath,JSON.stringify(control));
      }
    }
    else if (format === '{{.OSType}}') console.log('linux');
    else if (format === '{{.Architecture}}') console.log('x86_64');
    else fail();
    await record('info', { format },daemonName); process.exit(0);
  }
  if (command === 'load') { if (!valueAfter(argv, '-i')) fail(); await record('image-load',null,daemonName); process.exit(0); }
  if (command === 'image' && subcommand === 'inspect') {
    const image = argv.at(-1);
    if (!image?.startsWith('sha256:')) fail();
    console.log(`${image}|linux|amd64`); await record('image-inspect', { image },daemonName); process.exit(0);
  }
  if (command === 'inspect' && argv.includes('--format')) {
    const id = argv.at(-1); const format = valueAfter(argv, '--format');
    const resource = data.resources.find((item) => item.id === id);
    if (!resource) process.exit(2);
    if (format === '{{.State.Running}}' && resource.kind === 'container') console.log(String(resource.running));
    else if (format === '{{.State.Health.Status}}' && resource.kind === 'container') console.log(resource.health);
    else if (argv.includes('container') || resource.kind === 'container') console.log(renderResource(resource, 'container'));
    else console.log(renderResource(resource, resource.kind));
    await record('inspect', { kind: resource.kind, id: resource.id, name: resource.name },daemonName); process.exit(0);
  }
  if (['container', 'network', 'volume'].includes(command)) {
    const kind = command;
    if (subcommand === 'ls') {
      const format = valueAfter(argv, '--format');
      if (!format) fail();
      const projectFilter = (argv.filter((item) => item.startsWith('--filter=')).map((item) => item.slice(9)));
      const separateFilter = valueAfter(argv, '--filter');
      const filters = [...projectFilter, ...(separateFilter ? [separateFilter] : [])];
      if (!filters.length || filters.some((filter) => !filter.startsWith('name=') && !filter.startsWith('label=com.docker.compose.project='))) fail();
      for (const resource of data.resources.filter((item) => item.kind === kind && filters.some((filter) =>
        filter === `name=${item.project}` || filter === `label=com.docker.compose.project=${item.project}` ||
        (filter.startsWith('name=') && item.name.includes(filter.slice(5)))))) {
        console.log(`${resource.id}|${kind === 'container' ? resource.name : resource.name}`);
      }
      await record('list', { kind, filters },daemonName); process.exit(0);
    }
    if (subcommand === 'inspect' && argv.includes('--format')) {
      const id = argv.at(-1); const resource = data.resources.find((item) => item.kind === kind && item.id === id);
      if (!resource) process.exit(2);
      console.log(renderResource(resource, kind)); await record('inspect', { kind, id, name: resource.name },daemonName); process.exit(0);
    }
    if (subcommand === 'stop' && kind === 'container') {
      const id = argv.at(-1); const resource = data.resources.find((item) => item.kind === kind && item.id === id);
      if (!resource || !valueAfter(argv, '--time')) process.exit(2);
      resource.running = false; await save(all); await record('stop', { kind, id, name: resource.name },daemonName); process.exit(0);
    }
    if (subcommand === 'rm') {
      const id = argv.at(-1); const index = data.resources.findIndex((item) => item.kind === kind && item.id === id);
      if (index < 0) process.exit(2);
      const resource = data.resources[index]; data.resources.splice(index, 1);
      await save(all); await record('remove', { kind, id, name: resource.name, project: resource.project },daemonName); process.exit(0);
    }
  }
  fail();
} catch { process.exit(70); }
