// 固定候选会话。进程组无法证明静止时，调用者不得清理引擎资源。
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import net from 'node:net';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const localUrl = pathToFileURL(path.join(repo, 'ops/podman/local.mjs')).href;
const sessionUrl = pathToFileURL(fileURLToPath(import.meta.url)).href;
const source = `import {runLocal} from ${JSON.stringify(localUrl)}; import {watchCancel} from ${JSON.stringify(sessionUrl)};\n` +
  `let raw=''; for await (const chunk of process.stdin) { raw += chunk; if (raw.length > 4096) throw Error('SESSION_INPUT_TOO_LARGE'); }\n` +
  `const input=JSON.parse(raw); const cancel=watchCancel(()=>process.emit('SIGTERM')); const code=await runLocal(input.argv,input.candidate); process.exitCode=Number.isInteger(code)?code:1; cancel.destroy();\n`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
/** fd3 is a fixed inherited pipe, not caller input. EOF also cancels orphaned sessions. */
export function watchCancel(onCancel, fd = 3) {
  const socket = new net.Socket({ fd, readable: true, writable: false });
  let fired = false;
  const cancel = () => { if (!fired) { fired = true; onCancel(); } };
  socket.on('data', cancel); socket.on('end', cancel); socket.on('error', cancel);
  return socket;
}
function groupGone(pid) {
  if (!pid) return false;
  try { process.kill(-pid, 0); return false; }
  catch (error) { return error.code === 'ESRCH'; }
}
export function startCandidateSession({ action, directory, candidate, dbPort, logFile, timeoutMs = 900_000 }) {
  if (!['install', 'upgrade'].includes(action) || !path.isAbsolute(directory) || !path.isAbsolute(logFile)
    || !candidate || Object.keys(candidate).sort().join(',') !== 'candidateDirectory,candidateSha256'
    || !path.isAbsolute(candidate.candidateDirectory) || !/^[a-f0-9]{64}$/.test(candidate.candidateSha256)
    || !Number.isInteger(dbPort) || dbPort < 1024 || dbPort > 65535 || dbPort === 18080
    || !Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 900_000) throw new Error('SESSION_REQUEST_INVALID');
  // 文件由本入口独占创建，不允许预创建再以 wx 打开。
  const fd = fs.openSync(logFile, 'wx', 0o600);
  let child;
  try {
    child = spawn(process.execPath, ['--input-type=module', '-e', source], {
      cwd: repo, detached: true, stdio: ['pipe', fd, fd, 'pipe'], env: process.env,
    });
  } finally { fs.closeSync(fd); }
  let closed = false;
  const exit = new Promise(resolve => {
    child.once('error', () => { closed = true; resolve({ code: null, signal: 'SPAWN_ERROR' }); });
    child.once('close', (code, signal) => { closed = true; resolve({ code, signal }); });
  });
  child.stdin.on('error', () => {});
  child.stdio[3].on('error', () => {});
  child.stdin.end(JSON.stringify({ argv: [action, '--dir', directory, ...(action === 'install' ? ['--db-port', String(dbPort)] : [])], candidate }) + '\n');
  const wait = async () => {
    let timer;
    const outcome = await Promise.race([exit, new Promise(resolve => { timer = setTimeout(() => resolve(null), timeoutMs); })]);
    clearTimeout(timer);
    if (outcome === null) {
      // 只通过本child持有的专用pipe请求取消；从不向可能复用的PID/PGID发信号。
      if (!closed) child.stdio[3].end('cancel\n');
      await Promise.race([exit, sleep(10_000)]);
      if (!closed || !groupGone(child.pid)) {
        child.stdio[3].destroy(); child.unref();
        throw new Error('SESSION_NOT_SETTLED');
      }
      throw new Error('SESSION_TIMEOUT');
    }
    child.stdio[3].destroy();
    for (let i = 0; i < 40 && !groupGone(child.pid); i++) await sleep(50);
    return { ...outcome, settled: groupGone(child.pid) };
  };
  return { wait, isClosed: () => closed };
}
export async function runCandidateSession(input) { return startCandidateSession(input).wait(); }
