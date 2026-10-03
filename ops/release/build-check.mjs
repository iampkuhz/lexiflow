import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { runTests } from './check.mjs';

// 构建输入使用真实 clean Git 与文件 fixture，不签发容器或平台支持证明。
// Harness 总预算仍为 180 秒；子进程最多 170 秒，留出报告与退出余量。
export function runBuildTests(options = {}) {
  return runTests({ timeoutMs: 170_000, ...options, args: ['--test', '--test-reporter=tap', path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/archive-identity.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/embedded-identity.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/build.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/build-execution.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/candidate.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/verified-candidate.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/candidate-proof.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/pipeline.test.mjs'), path.join(path.dirname(fileURLToPath(import.meta.url)), 'tests/workflow.test.mjs')] });
}
function invokedDirectly() { try { return Boolean(process.argv[1]) && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url)); } catch { return false; } }
if (invokedDirectly()) runBuildTests().then((result) => { process.stdout.write(`${JSON.stringify(result)}\n`); if (result.status !== 'PASS') process.exitCode = 1; });
