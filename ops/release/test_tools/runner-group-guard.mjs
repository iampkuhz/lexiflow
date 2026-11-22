import net from 'node:net';
import { execFileSync } from 'node:child_process';

// 独立 CLI／伪造环境不取得任何自组清理能力。
if (!process.connected) process.exit(1);
const actualGroup = Number(execFileSync('/bin/ps', ['-o', 'pgid=', '-p', String(process.pid)], { encoding: 'utf8', timeout: 100 }));
if (actualGroup !== process.ppid) process.exit(1);

// 与 payload 同组的活身份锚。协议从不接收 PID／PGID，也不向数值 PID 补杀。
const selfDestruct = () => process.kill(0, 'SIGKILL');
let socket;
const cancel = () => { if (socket?.writable) socket.write(`${JSON.stringify({ op: 'cancel' })}\n`); };
process.on('SIGTERM', cancel);
process.on('SIGINT', cancel);
process.on('SIGHUP', cancel);
process.on('disconnect', selfDestruct);
process.on('uncaughtException', selfDestruct);
try {
  const { endpoint, token } = JSON.parse(process.env.LEXIFLOW_RUNNER_GUARD);
  delete process.env.LEXIFLOW_RUNNER_GUARD;
  socket = net.createConnection(endpoint);
  socket.on('error', selfDestruct);
  socket.on('close', selfDestruct); // root 突然死亡的兜底，不是正常屏障 ACK。
  let input = '';
  socket.on('data', chunk => {
    input += chunk;
    if (input.length > 4096) return selfDestruct();
    while (input.includes('\n')) {
      const end = input.indexOf('\n'); const message = JSON.parse(input.slice(0, end)); input = input.slice(end + 1);
      if (message.op === 'armed') {
        // root 已直接持有控制能力，才允许 bootstrap 放行载荷。
        process.send({ ready: true });
        process.disconnect();
      } else if (message.op === 'kill') selfDestruct();
      else selfDestruct();
    }
  });
  // disconnect 只监视 bootstrap 在 ready 之前的死亡；ready 后仅 root EOF 可结束 guard。
  const disconnect = () => process.removeListener('disconnect', selfDestruct);
  socket.on('connect', () => {
    disconnect();
    socket.write(`${JSON.stringify({ op: 'guard', token, leader: process.ppid })}\n`);
  });
} catch { selfDestruct(); }
