// 仅将精确已知的已初始化模板切换为可发布端口的网络，不改变任何数据文件。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { boundedApiLogging } from './upgrade.mjs';
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
export function networkTemplates(template, apiPort, dbPort) {
  const current = template.replace('127.0.0.1:18080:8080', `127.0.0.1:${apiPort}:8080`).replace('127.0.0.1:15432:5432', `127.0.0.1:${dbPort}:5432`);
  const marker = '  published:\n';
  if (!current.includes(marker)) throw new Error('NETWORK_TEMPLATE_INVALID');
  const legacy = current.slice(0, current.lastIndexOf(marker)).replaceAll('networks: [private, published]', 'networks: [private]');
  return { current, legacy };
}
export function repairNetwork({ dir, state, template, actualDigests, save, enabled }) {
  if (!state.digests) return false;
  const same = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
  const { current, legacy } = networkTemplates(template, state.apiPort, state.dbPort);
  const key = 'compose.yaml';
  const loggedLegacy = hash(boundedApiLogging(legacy));
  const logged = state.digests[key] === loggedLegacy;
  const from = logged ? loggedLegacy : hash(legacy), to = hash(logged ? boundedApiLogging(current) : current);
  const actual = actualDigests();
  if (!same({ ...actual, [key]: '' }, { ...state.digests, [key]: '' })) throw new Error('安装配置或密码发生变化，拒绝网络修复');
  const pending = state.networkRepair;
  if (!pending && actual[key] === state.digests[key] && [to, hash(boundedApiLogging(current))].includes(actual[key])) return false;
  if (!enabled) {
    if (pending || !same(actual, state.digests)) throw new Error('网络修复尚未完成，请运行 up 安全续办');
    return false;
  }
  if (!['initialized', 'ready'].includes(state.phase)) {
    if (pending) throw new Error('初始化状态不允许网络修复');
    if (!same(actual, state.digests)) throw new Error('安装配置发生变化');
    return false;
  }
  if (pending) {
    if (Object.keys(pending).sort().join(',') !== 'from,to' || pending.from !== from || pending.to !== to || state.digests[key] !== from || ![from, to].includes(actual[key])) throw new Error('网络修复身份不符，保留现场');
  } else {
    if (state.digests[key] !== from || actual[key] !== from) throw new Error('安装网络模板未知，拒绝自动覆盖');
    state.networkRepair = { from, to };
    save();
  }
  if (actual[key] !== to) {
    // 唯一临时名；崩溃遗留文件不接管、不跟随符号链接。
    const temporary = path.join(dir, `.compose-${crypto.randomBytes(16).toString('hex')}.next`);
    fs.writeFileSync(temporary, logged ? boundedApiLogging(current) : current, { mode: 0o600, flag: 'wx' });
    fs.renameSync(temporary, path.join(dir, key));
  }
  state.digests[key] = to;
  delete state.networkRepair;
  save();
  return true;
}
