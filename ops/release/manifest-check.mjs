import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { runTests } from './check.mjs';

export function runManifestTests(options = {}) {
  return runTests({ ...options, args: ['--test', '--test-reporter=tap', path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/manifest.test.mjs')] });
}
function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) runManifestTests().then((result) => { process.stdout.write(`${JSON.stringify(result)}\n`); if (result.status !== 'PASS') process.exitCode = 1; });
