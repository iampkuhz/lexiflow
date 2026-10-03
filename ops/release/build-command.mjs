import { spawn } from 'node:child_process';

const OUTPUT_LIMIT = 1024 * 1024;
const GRACE_MS = 200;

function terminateOwnedProcess(child, signal) {
  if (!child.pid) return;
  try {
    if (process.platform !== 'win32') process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) {
    if (error.code !== 'ESRCH') {
      try { child.kill(signal); } catch { /* 仍以实际 close 事件确认终态。 */ }
    }
  }
}

/** 以无 shell 和受限环境运行固定 Docker 可执行文件。 */
export function runDocker(args, { cwd, configDirectory, endpoint, timeoutMs = 30_000 } = {}) {
  if (!Array.isArray(args) || args.some((arg) => typeof arg !== 'string' || arg.includes('\0'))
    || typeof cwd !== 'string' || typeof configDirectory !== 'string' || typeof endpoint !== 'string'
    || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1) return Promise.reject(new Error('DOCKER_COMMAND_FAILED'));
  const argv = ['--config', configDirectory, '--host', endpoint, ...args];
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawn('docker', argv, {
        cwd,
        env: { PATH: process.env.PATH ?? '', LANG: 'C', LC_ALL: 'C', HOME: configDirectory },
        stdio: ['ignore', 'pipe', 'pipe'],
        shell: false,
        detached: process.platform !== 'win32',
      });
    } catch { reject(new Error('DOCKER_UNAVAILABLE')); return; }
    let outputBytes = 0; let stdout = ''; let failure = null; let closed = false;
    let timer; let killTimer;
    const stop = (reason) => {
      if (failure) return;
      failure = reason;
      terminateOwnedProcess(child, 'SIGTERM');
      killTimer = setTimeout(() => terminateOwnedProcess(child, 'SIGKILL'), GRACE_MS);
      killTimer.unref?.();
    };
    const collect = (chunk, isStdout) => {
      outputBytes += chunk.length;
      if (outputBytes > OUTPUT_LIMIT) { stop('DOCKER_OUTPUT_LIMIT'); return; }
      if (isStdout && stdout.length < OUTPUT_LIMIT) stdout += chunk.toString('utf8');
    };
    child.stdout.on('data', (chunk) => collect(chunk, true));
    child.stderr.on('data', (chunk) => collect(chunk, false));
    timer = setTimeout(() => stop('DOCKER_COMMAND_TIMEOUT'), timeoutMs);
    child.once('close', (code) => {
      closed = true;
      clearTimeout(timer); clearTimeout(killTimer);
      if (failure) { reject(new Error(failure)); return; }
      if (code !== 0) { reject(new Error('DOCKER_COMMAND_FAILED')); return; }
      resolve(stdout);
    });
    // 进程报错后仍等待 close，避免遗留本次拥有的子进程。
    child.once('error', () => { if (!closed) stop('DOCKER_UNAVAILABLE'); });
  });
}

export const dockerOutputLimit = OUTPUT_LIMIT;
