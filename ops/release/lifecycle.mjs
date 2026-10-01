import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { generateRuntimeEntry } from './runtime-entry.mjs';

const templates = ['lifecycle-state.sh', 'lifecycle-docker.sh', 'lifecycle.sh'];

/** 生成包含固定生命周期模板的自包含发行入口。 */
export async function generateLifecycleEntry({ repoRoot, descriptor, artifactRoot }) {
  if (typeof repoRoot !== 'string' || typeof artifactRoot !== 'string') throw new Error('ARGUMENTS_INVALID');
  const root = path.resolve(repoRoot, 'ops/release');
  const parts = await Promise.all(templates.map((name) => readFile(path.join(root, name), 'utf8')));
  const lifecycleBody = `${parts[0]}\n${parts[1]}\n${parts[2]}`;
  return generateRuntimeEntry({ repoRoot, descriptor, artifactRoot, lifecycleBody });
}
