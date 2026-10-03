// 安装调度锁归内核持有；文件只是固定 inode，不因退出或回收而 unlink。
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

export function acquireProcessLock(directory) {
  const file = path.join(directory, '.lock-guard');
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW, 0o600);
    const before = fs.fstatSync(fd);
    if (!before.isFile() || before.nlink !== 1 || before.uid !== process.getuid() || (before.mode & 0o077)) {
      throw new Error('INSTALL_LOCK_GUARD_INVALID');
    }
    // lockf 的 FD 模式在 macOS 上锁住与父 Node 共享的 open-file description。
    // Linux flock 仅供无发行资格的托管合成 CI；不扩展产品平台合同。
    const command = process.platform === 'darwin' ? '/usr/bin/lockf' : process.platform === 'linux' ? '/usr/bin/flock' : null;
    if (!command) throw new Error('INSTALL_LOCK_PRIMITIVE_UNAVAILABLE');
    const args = process.platform === 'darwin' ? ['-s', '-t', '0', '3'] : ['--nonblock', '3'];
    const result = spawnSync(command, args, { stdio: ['ignore', 'ignore', 'pipe', fd], timeout: 5000 });
    if (result.error || result.signal) throw new Error('INSTALL_LOCK_PRIMITIVE_UNAVAILABLE');
    if (result.status !== 0) throw new Error('INSTALL_LOCK_BUSY');
    const named = fs.lstatSync(file), opened = fs.fstatSync(fd);
    if (named.isSymbolicLink() || named.dev !== opened.dev || named.ino !== opened.ino || opened.nlink !== 1) {
      throw new Error('INSTALL_LOCK_GUARD_CHANGED');
    }
    let released = false;
    return () => {
      if (!released) { released = true; fs.closeSync(fd); }
    };
  } catch (error) {
    if (fd !== undefined) fs.closeSync(fd);
    throw error;
  }
}
