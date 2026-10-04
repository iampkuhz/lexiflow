// 源码预检与 Gradle 使用同一 Java；不安装工具，不修改调用方或继承环境白名单。
import path from 'node:path';
import { spawnSync } from 'node:child_process';

export function javaBuildEnvironment(environment) {
  const env = { ...environment };
  const home = env.JAVA_HOME;
  if (home) {
    if (!path.isAbsolute(home)) throw new Error('JAVA_HOME_INVALID：JAVA_HOME 必须是 JDK 25 的绝对路径');
    env.PATH = [path.join(home, 'bin'), env.PATH].filter(Boolean).join(path.delimiter);
  }
  const command = home ? path.join(home, 'bin', 'java') : 'java';
  const source = home ? 'JAVA_HOME' : 'PATH';
  const result = spawnSync(command, ['-version'], {
    env, encoding: 'utf8', timeout: 10000, maxBuffer: 64 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  // 只报告结构化原因和经过语法验证的版本，不回显可能带私密内容的原始 stdout/stderr。
  const fail = (code, message) => {
    throw new Error(`${code}：${message}（Java 来源=${source}；命令=${JSON.stringify(command)}）`);
  };
  if (result.error) {
    const code = result.error.code;
    if (code === 'ETIMEDOUT') fail('JAVA_PROBE_TIMEOUT', 'Java 版本检查超过 10 秒，不代表未安装 JDK');
    if (code === 'ENOBUFS') fail('JAVA_PROBE_OUTPUT_LIMIT', 'Java 版本检查输出超过限制');
    if (code === 'ENOENT') fail('JAVA_EXECUTABLE_MISSING', '找不到 Java 可执行文件；请检查所选 JDK 路径');
    if (code === 'EACCES') fail('JAVA_EXECUTABLE_DENIED', 'Java 可执行文件没有执行权限');
    fail('JAVA_PROBE_START_FAILED', '无法启动 Java 版本检查');
  }
  if (result.signal) fail('JAVA_PROBE_SIGNAL', 'Java 版本检查被信号终止');
  if (result.status !== 0) fail('JAVA_PROBE_EXIT', `Java 版本检查失败，退出码=${Number.isInteger(result.status) ? result.status : '未知'}`);
  const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`;
  const versions = [...new Set([...output.matchAll(/^(?:openjdk|java) version "([0-9]+(?:\.[0-9]+)*(?:[-+][0-9A-Za-z.+-]+)?)"(?:\s|$)/gm)].map(match => match[1]))];
  if (versions.length !== 1) fail('JAVA_VERSION_UNRECOGNIZED', 'Java 已运行，但版本输出缺失或冲突，不能确认 Java 25');
  const version = versions[0];
  if (!/^25(?:\.[0-9]+)*(?:\+[0-9A-Za-z.-]+)?$/.test(version)) {
    fail('JAVA_VERSION_UNSUPPORTED', `需要正式 Java 25 JDK，实际版本=${version.slice(0, 80)}`);
  }
  return env;
}
