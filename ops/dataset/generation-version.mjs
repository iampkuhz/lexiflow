#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFile, lstat, readdir } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const CONFIG = Object.freeze({ sourceId: 'ecdict-stardict', licenseId: 'MIT' });
const sha = (value) => createHash('sha256').update(value).digest('hex');
export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
async function filesUnder(root, pattern) {
  if (typeof pattern !== 'string' || path.isAbsolute(pattern) || pattern.includes('\\')
      || pattern.split('/').some(part => !part || part === '.' || part === '..')) throw new Error('invalid generation input path');
  const base = pattern.endsWith('/**') ? pattern.slice(0, -3) : null;
  if (!base) return [pattern];
  const dir = path.join(root, base);
  await checkedPath(root, base, true);
  const out = [];
  async function visit(current) {
    const names = (await readdir(current)).sort();
    for (const name of names) {
      const abs = path.join(current, name); const st = await lstat(abs);
      if (st.isSymbolicLink()) throw new Error(`symlink input forbidden: ${path.relative(root, abs)}`);
      if (st.isDirectory()) await visit(abs);
      else if (st.isFile()) out.push(path.relative(root, abs).split(path.sep).join('/'));
      else throw new Error('generation input must be a regular file');
    }
  }
  await visit(dir); return out;
}
async function checkedPath(root, relative, directory = false) {
  let cursor = root;
  for (const component of relative.split('/')) {
    cursor = path.join(cursor, component);
    if ((await lstat(cursor)).isSymbolicLink()) throw new Error(`symlink input forbidden: ${relative}`);
  }
  const stat = await lstat(cursor);
  if (!(directory ? stat.isDirectory() : stat.isFile())) throw new Error(`required regular input missing: ${relative}`);
  return cursor;
}
export async function generationCodeVersion(repoRoot) {
  const root = path.resolve(repoRoot);
  const manifestPath = path.join(root, 'ops/dataset/generation-inputs.json');
  await checkedPath(root, 'ops/dataset/generation-inputs.json');
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
  if (manifest.schemaVersion !== 1 || !Array.isArray(manifest.include)) throw new Error('invalid generation input manifest');
  const paths = new Set(['ops/dataset/generation-inputs.json', 'ops/dataset/generation-version.mjs']);
  for (const pattern of manifest.include) for (const p of await filesUnder(root, pattern)) paths.add(p);
  const rows = [];
  for (const rel of [...paths].sort()) {
    const abs = await checkedPath(root, rel);
    rows.push([rel, sha(await readFile(abs))]);
  }
  return sha(canonicalJson(rows));
}
export function dataGeneration({ codeVersion, inputSha256, upstream, config = CONFIG }) {
  if (!/^[0-9a-f]{64}$/.test(codeVersion) || !/^[0-9a-f]{64}$/.test(inputSha256)) throw new Error('invalid generation digest');
  if (canonicalJson(config) !== canonicalJson(CONFIG)) throw new Error('unsupported generation config');
  if (!upstream || typeof upstream !== 'object' || Array.isArray(upstream)) throw new Error('invalid upstream identity');
  const sourceDigest = sha(canonicalJson({ inputSha256, upstream }));
  return { codeVersion, sourceDigest, generationVersion: sha(canonicalJson({ codeVersion, sourceDigest, config })), inputSha256, config, upstream };
}
async function cli() {
  const args = process.argv.slice(2);
  if (args[0] === '--print-code-version') { process.stdout.write(`${await generationCodeVersion(process.cwd())}\n`); return; }
  const outAt = args.indexOf('--output-dir');
  if (outAt < 0 || !args[outAt + 1]) throw new Error('usage: generation-version.mjs --output-dir <dir>');
  const root = process.cwd(); const codeVersion = await generationCodeVersion(root);
  const targetDir = path.resolve(args[outAt + 1], 'META-INF');
  const { mkdir, writeFile } = await import('node:fs/promises');
  await mkdir(targetDir, { recursive: true });
  await writeFile(path.join(targetDir, 'lexiflow-generation.json'), `${canonicalJson({ codeVersion })}\n`);
}
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) cli().catch(e => { console.error(e.message); process.exitCode = 2; });
