// 部署子进程的有界等待与低噪声反馈；原始工具输出只进入私有日志。
import fs from 'node:fs';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';

export const HEARTBEAT_MS = 20000;
const MAX_OUTPUT = 24 * 1024 * 1024;

export function runCommand(command, argv, { cwd, env, timeout = 600000, capture = false, logFile, label = '执行辅助检查', announce = false, onStart, onClose } = {}) {
  return new Promise((resolve, reject) => {
    const started = performance.now();
    let lastOutput, bytes = 0, output = [], failure, killTimer, child, closed, finishing = false;
    const elapsed = () => Math.floor((performance.now() - started) / 1000);
    const append = (data) => { if (logFile) fs.appendFileSync(logFile, data, { mode: 0o600 }); };
    if (announce) console.log(`[LexiFlow]   ${label}…`);
    try {
      append(`\n[${label}]\n`);
      // 有归属回调时先启动不执行外部动作的 shell 屏障。父进程须同步持久记录
      // PGID，再关闭带许可消息的 stdin；父进程强杀/记录失败只有 EOF，不会执行命令。
      const barrier = 'IFS= read -r permit && [ "$permit" = lexiflow-start ] || exit 125; exec "$@"';
      child = onStart
        ? spawn('/bin/sh', ['-c', barrier, 'lexiflow-command', command, ...argv], { cwd, env, detached: true, stdio: ['pipe', 'pipe', 'pipe'] })
        : spawn(command, argv, { cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    } catch {
      reject(new Error(`${label}无法启动${logFile ? `；详情见 ${logFile}` : ''}`)); return;
    }
    const signalGroup = (signal) => {
      if (!child.pid) return;
      try { process.kill(-child.pid, signal); }
      catch (error) { if (error.code !== 'ESRCH') child.kill(signal); }
    };
    const stop = (reason) => {
      if (failure) return;
      failure = reason;
      signalGroup('SIGTERM');
      killTimer = setTimeout(() => {
        signalGroup('SIGKILL'); killTimer = undefined;
        if (closed) finish(...closed);
      }, 2000);
    };
    const interrupt = () => stop('收到中断请求');
    const heartbeat = setInterval(() => {
      const activity = lastOutput === undefined ? '尚无子进程输出' : `最近输出距今 ${Math.floor((performance.now() - lastOutput) / 1000)} 秒`;
      console.log(`[LexiFlow]   仍在${label}（已用 ${elapsed()} 秒；${activity}）`);
    }, HEARTBEAT_MS);
    const deadline = setTimeout(() => stop(`超过 ${Math.ceil(timeout / 1000)} 秒时限`), timeout);
    process.on('SIGINT', interrupt);
    process.on('SIGTERM', interrupt);
    const collect = (buffer, stdout) => {
      lastOutput = performance.now(); bytes += buffer.length;
      if (bytes > MAX_OUTPUT) { stop('子进程输出超过 24 MiB 上限'); return; }
      try { append(buffer); }
      catch { stop('无法写入运行日志'); return; }
      if (stdout && capture) output.push(buffer);
    };
    if (onStart) child.stdin.on('error', () => stop('无法释放子进程启动屏障'));
    try {
      if (child.pid) {
        onStart?.(child.pid);
        if (onStart) child.stdin.end('lexiflow-start\n');
      }
    } catch {
      if (onStart) child.stdin.destroy();
      stop('无法记录子进程归属');
    }
    child.stdout.on('data', buffer => collect(buffer, true));
    child.stderr.on('data', buffer => collect(buffer, false));
    child.on('error', () => { failure ||= '子进程无法启动'; });
    const finish = async (code, signal) => {
      if (finishing) return;
      finishing = true;
      clearInterval(heartbeat); clearTimeout(deadline); clearTimeout(killTimer);
      process.removeListener('SIGINT', interrupt); process.removeListener('SIGTERM', interrupt);
      try { append(`\nexit=${code}; signal=${signal || 'none'}; elapsed=${elapsed()}s\n`); }
      catch { failure ||= '无法写入运行日志'; }
      // close 只说明直接子进程和stdio结束；锁释放还需确认同组后代已退出。
      const groupAlive = () => {
        if (!child.pid) return false;
        try { process.kill(-child.pid, 0); return true; }
        catch (error) { return error.code !== 'ESRCH'; }
      };
      const waitUntil = performance.now() + 2000;
      while (groupAlive() && performance.now() < waitUntil) await new Promise(done => setTimeout(done, 50));
      const groupGone = !groupAlive();
      if (!groupGone) failure = '子进程组尚未完全退出，保留安装锁与子进程身份';
      try { onClose?.({ groupGone }); } catch { failure ||= '无法更新子进程归属'; }
      if (failure || code !== 0) {
        reject(new Error(`${label}失败（${failure || `退出码 ${code}${signal ? `，信号 ${signal}` : ''}`}，已用 ${elapsed()} 秒）${logFile ? `；详情见 ${logFile}` : '；检查工具安装与运行状态'}`));
        return;
      }
      if (announce) console.log(`[LexiFlow]   ${label}完成（${elapsed()} 秒）`);
      resolve(capture ? Buffer.concat(output).toString('utf8').trim() : undefined);
    };
    child.on('close', (code, signal) => {
      closed = [code, signal];
      if (!killTimer) finish(code, signal);
    });
  });
}
