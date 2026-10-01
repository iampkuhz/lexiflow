import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { runTests } from './check.mjs';

// 构建机运行真实 shell fixture，不把容器生命周期归入此结果。
export function runRuntimeEntryTests(options = {}) {
  return runTests({ ...options, args: ['--test', '--test-reporter=tap', path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/runtime-entry.test.mjs')] });
}
function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) runRuntimeEntryTests().then((result) => { process.stdout.write(`${JSON.stringify(result)}\n`); if (result.status !== 'PASS') process.exitCode = 1; });
