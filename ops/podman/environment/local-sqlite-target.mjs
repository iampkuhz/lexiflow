// 唯一数据库目标是安装专属 SQLite 文件；不接受服务端 URL 或非文件目标。
import fs from 'node:fs';
import path from 'node:path';

const fail = code => { throw new Error(code); };

export function validateSqliteDatabaseFile(file, { installationRoot, allowMissing = true } = {}) {
  if (typeof file !== 'string' || !path.isAbsolute(file) || /[\r\n\0]/.test(file)
      || path.basename(file) !== 'lexicon.db' || typeof installationRoot !== 'string'
      || !path.isAbsolute(installationRoot)) fail('DEPLOYMENT_DATABASE_FILE_INVALID');
  const root = path.resolve(installationRoot), target = path.resolve(file);
  if (!target.startsWith(`${root}${path.sep}`)) fail('DEPLOYMENT_DATABASE_FILE_OUTSIDE_INSTALLATION');
  let cursor = root;
  for (const component of path.relative(root, target).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, component);
    try { if (fs.lstatSync(cursor).isSymbolicLink()) fail('DEPLOYMENT_DATABASE_FILE_INVALID'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  try {
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink()) fail('DEPLOYMENT_DATABASE_FILE_INVALID');
  } catch (error) {
    if (error.code !== 'ENOENT' || !allowMissing) fail('DEPLOYMENT_DATABASE_FILE_INVALID');
  }
  return target;
}

export function sqliteJdbcUrl(file) {
  if (typeof file !== 'string' || !path.isAbsolute(file)
      || /[?\r\n\0]/.test(file)) fail('DEPLOYMENT_DATABASE_FILE_INVALID');
  return `jdbc:sqlite:${file}`;
}
