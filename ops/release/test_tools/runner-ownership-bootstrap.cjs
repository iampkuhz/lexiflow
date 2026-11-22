const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const { mkdtempSync, rmSync, readSync, writeSync } = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { fileURLToPath, pathToFileURL } = require('node:url');
const { isMainThread, Worker, parentPort, workerData } = require('node:worker_threads');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { setTimeout: sleep } = require('node:timers/promises');

const contextKey = 'LEXIFLOW_RUNNER_CONTEXT';
const bootstrapKey = 'LEXIFLOW_RUNNER_BOOTSTRAP';
const secret = () => randomBytes(24).toString('hex');
const send = (socket, message) => { if (!socket.destroyed) socket.write(`${JSON.stringify(message)}\n`); };
const absent = pid => {
  if (!Number.isSafeInteger(pid) || pid <= 1) throw new Error('invalid owned group');
  try { process.kill(-pid, 0); return false; }
  catch (error) { if (error.code === 'ESRCH') return true; if (error.code === 'EPERM') return false; throw error; }
};
function messages(socket, consume) {
  let buffer = '';
  socket.on('data', chunk => {
    buffer += chunk;
    if (buffer.length > 4096) { socket.destroy(); return; }
    while (buffer.includes('\n')) {
      const end = buffer.indexOf('\n'); const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
      try { consume(JSON.parse(line)); } catch { socket.destroy(); return; }
    }
  });
}

// 同步 --require 在 Node --test orchestrator 及载荷之前执行；--import 不覆盖前者。
// fork 的重复 preload 只认证既有组，不打开根进程的私有 fd3 或再创建 guard。
if (isMainThread && process.env[bootstrapKey]) {
  const boot = JSON.parse(process.env[bootstrapKey]);
  delete process.env[bootstrapKey];
  if (boot.nodeOptions === undefined) delete process.env.NODE_OPTIONS;
  else process.env.NODE_OPTIONS = boot.nodeOptions;
  const actualGroup = Number(execFileSync('/bin/ps', ['-o', 'pgid=', '-p', String(process.pid)], { encoding: 'utf8', timeout: 100 }));
  if (actualGroup !== process.pid) {
    const authenticate = `const net=require('node:net'),fs=require('node:fs');const x=JSON.parse(fs.readFileSync(0,'utf8'));const s=net.createConnection(x.endpoint);let input='',ok=false;s.on('connect',()=>s.write(JSON.stringify({op:'inherit',token:x.token,group:x.group})+'\\n'));s.on('error',()=>process.exit(1));s.on('data',c=>{input+=c;if(input.length>4096)process.exit(1);if(input.includes('\\n')){if(JSON.parse(input.trim()).op!=='inherited')process.exit(1);ok=true;s.end();}});s.on('close',()=>process.exit(ok?0:1));`;
    execFileSync(process.execPath, ['-e', authenticate], { input: JSON.stringify({ endpoint: boot.endpoint, token: boot.token, group: actualGroup }), env: { NODE_OPTIONS: '' }, stdio: ['pipe', 'ignore', 'ignore'], timeout: 100 });
  } else {
    const die = () => process.kill(0, 'SIGKILL');
    const pendingSignal = () => {};
    process.on('SIGTERM', pendingSignal); process.on('SIGINT', pendingSignal);
    const guard = spawn(process.execPath, [fileURLToPath(new URL('./runner-group-guard.mjs', pathToFileURL(__filename)))], {
      stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      env: { LEXIFLOW_RUNNER_GUARD: JSON.stringify({ endpoint: boot.endpoint, token: boot.token }) },
    });
    if (!guard.pid) die();
    guard.on('error', die);
    // 只在载荷前同步等待根授权；root/guard 在其他进程，不依赖此事件循环。
    let input = ''; const buffer = Buffer.alloc(256); const wait = new Int32Array(new SharedArrayBuffer(4));
    const next = () => {
      while (!input.includes('\n')) {
        try { const size = readSync(3, buffer, 0, buffer.length, null); if (size === 0) die(); input += buffer.subarray(0, size).toString(); }
        catch (error) { if (error.code !== 'EAGAIN' && error.code !== 'EWOULDBLOCK') die(); Atomics.wait(wait, 0, 0, 1); }
        if (input.length > 4096) die();
      }
      const end = input.indexOf('\n'); const line = input.slice(0, end); input = input.slice(end + 1);
      try { return JSON.parse(line).op; } catch { die(); }
    };
    if (next() !== 'armed') die();
    writeSync(3, '{"op":"ready"}\n');
    if (next() !== 'go') die();
    const gate = new net.Socket({ fd: 3, readable: true, writable: true });
    gate.on('end', die); gate.on('error', die); gate.on('data', die); gate.unref();
    guard.unref(); guard.channel?.unref();
    process.removeListener('SIGTERM', pendingSignal); process.removeListener('SIGINT', pendingSignal);
  }
  process.env[contextKey] = JSON.stringify({ endpoint: boot.endpoint, token: boot.token });
}

// library 调用者可能阻塞主事件循环；独立原生线程持有 ChildProcess 并及时 reap。
// 否则 Darwin 的已死子组会因中间层未 waitpid 永远只返回 EPERM，无法证明 ESRCH。
// 固定 CLI 只做监督、不执行调用者代码，保留原有直接 spawn 观察合同。
if (!isMainThread && workerData?.role === 'owned-test-reaper' && parentPort) {
  let child;
  parentPort.on('message', message => {
    if (message.op === 'start' && !child) {
      try {
        child = spawn(workerData.command, workerData.args, workerData.options);
        parentPort.postMessage({ event: 'spawn', pid: child.pid });
        child.stdout.on('data', chunk => parentPort.postMessage({ event: 'out', chunk }));
        child.stderr.on('data', chunk => parentPort.postMessage({ event: 'err', chunk }));
        child.stdio[3].on('data', chunk => parentPort.postMessage({ event: 'gate', chunk }));
        child.stdio[3].on('error', () => parentPort.postMessage({ event: 'gate-error' }));
        child.on('error', error => parentPort.postMessage({ event: 'spawn-error', code: error.code }));
        child.on('exit', (code, signal) => parentPort.postMessage({ event: 'exit', code, signal }));
        child.on('close', (code, signal) => { parentPort.postMessage({ event: 'close', code, signal }); parentPort.close(); });
      } catch (error) { parentPort.postMessage({ event: 'setup-error', code: error.code }); parentPort.close(); }
    } else if (message.op === 'gate' && child?.stdio[3]?.writable) child.stdio[3].write(message.text);
    else if (message.op === 'destroy-gate') child?.stdio[3]?.destroy();
  });
  parentPort.postMessage({ event: 'ready' });
}

async function spawnOwned(command, args, options, signal, directEntry) {
  if (directEntry) return spawn(command, args, options);
  signal.throwIfAborted();
  const worker = new Worker(pathToFileURL(__filename), {
    execArgv: [], env: { NODE_OPTIONS: '' }, workerData: { role: 'owned-test-reaper', command, args, options },
  });
  const child = new EventEmitter(); const gate = new EventEmitter();
  child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.stdio = [null, child.stdout, child.stderr, gate];
  gate.writable = true;
  gate.write = text => { worker.postMessage({ op: 'gate', text }); return true; };
  gate.destroy = () => { gate.writable = false; if (!worker.threadId || worker.threadId < 0) return; worker.postMessage({ op: 'destroy-gate' }); };
  let started = false; let announced = false; let closed = false;
  const abort = () => { if (!started) void worker.terminate(); };
  signal.addEventListener('abort', abort, { once: true });
  child.reaperDone = new Promise(resolve => worker.once('exit', resolve));
  return new Promise((resolve, reject) => {
    worker.on('message', message => {
      if (message.event === 'ready') {
        if (signal.aborted) { abort(); return; }
        started = true; signal.removeEventListener('abort', abort); worker.postMessage({ op: 'start' });
      } else if (message.event === 'spawn') { announced = true; child.pid = message.pid; resolve(child); }
      else if (message.event === 'out') child.stdout.write(Buffer.from(message.chunk));
      else if (message.event === 'err') child.stderr.write(Buffer.from(message.chunk));
      else if (message.event === 'gate') gate.emit('data', Buffer.from(message.chunk));
      else if (message.event === 'gate-error') gate.emit('error', new Error('owned gate closed'));
      else if (message.event === 'spawn-error') child.emit('error', Object.assign(new Error('spawn failed'), { code: message.code }));
      else if (message.event === 'setup-error') reject(new Error('owned spawn failed'));
      else if (message.event === 'exit') child.emit('exit', message.code, message.signal);
      else if (message.event === 'close') {
        closed = true; gate.writable = false; child.stdout.end(); child.stderr.end(); child.emit('close', message.code, message.signal);
      }
    });
    worker.once('error', error => { if (announced) child.emit('error', error); else reject(error); });
    worker.once('exit', () => {
      signal.removeEventListener('abort', abort);
      if (!announced) reject(new Error('owned reaper stopped before spawn'));
      else if (!closed) child.emit('error', new Error('owned reaper failed'));
    });
  });
}

// execution root 独占的内存树；已清租约保留为墓碑，旧能力不可复活。
async function broker() {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'lf-owned-'));
  const endpoint = path.join(directory, 'relay'); const authority = secret();
  const records = new Map(); const sockets = new Set();
  let closing = false;
  const frozen = record => record && (record.ending || frozen(record.parent));
  // 每次父屏障检查完整保留树。子租约的首次结果不可变，但失败墓碑不是
  // 当前存活证明；后续父屏障须在自己的同一 100ms 内重新取得 ESRCH。
  // 已证明 CLEAN 的能力永久失效，不再读取可能重用的数字 PGID。
  const prove = async (record, deadline) => {
    if (record.clean) return true;
    record.ending = true;
    const children = await Promise.all([...record.children].map(child => prove(child, deadline)));
    if (children.some(clean => !clean)) return false;
    while (performance.now() < deadline) {
      if (record.clean || record.noSpawn) { record.clean = true; return true; }
      // 只向仍存活的 guard 使用原能力；失败墓碑只读观察，绝不按 PID 补杀。
      if (record.guard && !record.guard.destroyed && !record.killSent) {
        record.killSent = true; send(record.guard, { op: 'kill' });
      }
      if (record.pid && absent(record.pid)) { record.clean = true; return true; }
      await sleep(1);
    }
    return false;
  };
  const stop = record => {
    if (record.cleanup) return record.cleanup;
    record.ending = true;
    record.cleanup = prove(record, performance.now() + 100).catch(() => false);
    return record.cleanup;
  };
  const cancel = record => {
    if (record.clean) return;
    send(record.owner, { event: 'cancel' });
    void stop(record);
  };
  const server = net.createServer(socket => {
    sockets.add(socket); let record; let role;
    socket.on('error', () => {});
    socket.on('close', () => {
      sockets.delete(socket);
      if (record && !record.clean && !record.ending) cancel(record);
    });
    messages(socket, message => {
      if (!role) {
        if (message.op === 'inherit') {
          const candidate = records.get(message.token);
          if (!candidate || !candidate.attached || frozen(candidate) || candidate.clean || !candidate.guard || candidate.guard.destroyed || candidate.pid !== message.group) throw new Error('invalid inherited group');
          role = 'inherited'; send(socket, { op: 'inherited' }); socket.end(); return;
        }
        if (message.op === 'reserve') {
          const parent = message.parent ? records.get(message.parent) : null;
          if (closing || (message.parent ? !parent || frozen(parent) : message.authority !== authority)) throw new Error('invalid owner');
          record = { token: secret(), parent, children: new Set(), owner: socket, ending: false, clean: false };
          records.set(record.token, record); parent?.children.add(record); role = 'owner';
          send(socket, { seq: message.seq, value: { endpoint, token: record.token } });
        } else if (message.op === 'guard') {
          const candidate = records.get(message.token);
          if (!candidate || candidate.clean || candidate.noSpawn || candidate.guard || !Number.isSafeInteger(message.leader) || message.leader <= 1 || (candidate.pid && candidate.pid !== message.leader)) throw new Error('invalid guard');
          record = candidate;
          record.pid = message.leader; record.guard = socket; role = 'guard';
          send(socket, { op: 'armed' }); send(record.owner, { event: 'guard-ready' });
        } else throw new Error('invalid registration');
        return;
      }
      if (role === 'guard') {
        if (message.op !== 'cancel') throw new Error('invalid guard command');
        cancel(record); return;
      }
      if (message.op === 'attach') {
        if (record.attached || record.noSpawn || (record.pid && record.pid !== message.pid) || !Number.isSafeInteger(message.pid) || message.pid <= 1) throw new Error('invalid spawn');
        record.attached = true; record.pid = message.pid;
        send(socket, { seq: message.seq, value: !frozen(record) });
      } else if (message.op === 'no-spawn') {
        if (record.attached || record.guard || record.noSpawn) throw new Error('invalid spawn failure');
        record.noSpawn = true; send(socket, { seq: message.seq, value: true });
      } else if (message.op === 'finish' || message.op === 'cancel') {
        void stop(record).then(clean => send(socket, { seq: message.seq, value: clean }));
      } else throw new Error('invalid owner command');
    });
  });
  try { await new Promise((resolve, reject) => { server.once('error', reject); server.listen(endpoint, resolve); }); }
  catch (error) { rmSync(directory, { recursive: true, force: true }); throw error; }
  return { endpoint, authority, async dispose() {
    closing = true;
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    rmSync(directory, { recursive: true, force: true });
  } };
}

async function openOwnership(onCancel, signal) {
  signal?.throwIfAborted();
  if (process.platform === 'win32') throw new Error('owned groups unsupported');
  const inherited = process.env[contextKey] ? JSON.parse(process.env[contextKey]) : null;
  const root = inherited ? null : await broker();
  if (signal?.aborted) { if (root) await root.dispose(); signal.throwIfAborted(); }
  const socket = net.createConnection(inherited?.endpoint ?? root.endpoint);
  const abort = () => socket.destroy(new Error('ownership acquisition cancelled'));
  signal?.addEventListener('abort', abort, { once: true });
  const waiting = new Map(); const readyWaiters = new Set(); let ready = false; let seq = 0;
  const rejectAll = () => { for (const resolve of readyWaiters) resolve(false); readyWaiters.clear(); for (const { reject } of waiting.values()) reject(new Error('ownership relay closed')); waiting.clear(); };
  socket.on('error', rejectAll); socket.on('close', rejectAll);
  messages(socket, message => {
    if (message.event === 'cancel') { onCancel(); return; }
    if (message.event === 'guard-ready') { ready = true; for (const resolve of readyWaiters) resolve(true); readyWaiters.clear(); return; }
    const pending = waiting.get(message.seq);
    if (!pending) throw new Error('unknown reply');
    waiting.delete(message.seq); pending.resolve(message.value);
  });
  const request = (op, fields = {}) => new Promise((resolve, reject) => {
    if (socket.destroyed) { reject(new Error('ownership relay closed')); return; }
    const id = ++seq; waiting.set(id, { resolve, reject }); send(socket, { op, seq: id, ...fields });
  });
  try {
    const context = await request('reserve', inherited ? { parent: inherited.token } : { authority: root.authority });
    signal?.removeEventListener('abort', abort);
    const nodeOptions = process.env.NODE_OPTIONS;
    const env = { ...process.env, [bootstrapKey]: JSON.stringify({ ...context, nodeOptions }),
      NODE_OPTIONS: `${nodeOptions ? `${nodeOptions} ` : ''}--require=${JSON.stringify(__filename)}` };
    return { env, ready: () => ready ? Promise.resolve(true) : new Promise(resolve => readyWaiters.add(resolve)), attach: pid => request('attach', { pid }), noSpawn: () => request('no-spawn'),
      finish: () => request('finish'), cancel: () => request('cancel'),
      async dispose() { socket.destroy(); if (root) await root.dispose(); } };
  } catch (error) { signal?.removeEventListener('abort', abort); socket.destroy(); if (root) await root.dispose(); throw error; }
}

exports.openOwnership = openOwnership;
exports.spawnOwned = spawnOwned;
