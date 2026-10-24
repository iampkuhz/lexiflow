// 固定入口兼容路径名；单容器验收实现位于 sibling module。
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runCandidateRuntime, REQUIRED_PHASE_IDS, validateCandidateRequest, eligibleForPass } from './single-runtime-candidate.mjs';
export { runCandidateRuntime, REQUIRED_PHASE_IDS, validateCandidateRequest, eligibleForPass };

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let input='';
  for await (const part of process.stdin) { input+=part; if(input.length>16_384) break; }
  try { process.stdout.write(`${JSON.stringify(await runCandidateRuntime(JSON.parse(input)))}\n`); }
  catch { process.stdout.write(`${JSON.stringify({schemaVersion:'lexiflow.candidate-runtime.v2',status:'FAIL',failure:{code:'REQUEST_INVALID'}})}\n`); }
}
