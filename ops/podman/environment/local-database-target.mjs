// 外部数据库连接适配：只连接本机固定目标，不管理 PostgreSQL 实例或共享角色。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { canonicalJson } from '../../dataset/dataset-generation-identity.mjs';

const fail = code => { throw new Error(code); };
const metadataSql = `SELECT json_build_object(
  'serverVersion', current_setting('server_version_num')::integer / 10000,
  'currentUser', current_user,
  'userOid', (SELECT oid::bigint FROM pg_roles WHERE rolname=current_user),
  'databaseOid', (SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
  'databaseOwner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()));`;
const schemaSql = `SELECT json_build_object(
  'schemaExists', EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='lexiflow_release'),
  'schemaOid', (SELECT oid::bigint FROM pg_namespace WHERE nspname='lexiflow_release'),
  'owner', (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='lexiflow_release'),
  'canCreateSchema', has_database_privilege(current_user, current_database(), 'CREATE'));`;
const clientScript = 'export PGPASSWORD="$(cat /run/secrets/db-password)"; exec psql --no-psqlrc --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 --host host.containers.internal --port "$1" --username "$2" --dbname "$3" --command "$4"';
const trustClientScript = 'unset PGPASSWORD PGPASSFILE; exec psql --no-psqlrc --quiet --no-align --tuples-only --no-password --set ON_ERROR_STOP=1 --host host.containers.internal --port "$1" --username "$2" --dbname "$3" --command "$4"';
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
function oneEnv(env, name) {
  const matches = env.filter(value => value.startsWith(`${name}=`));
  return matches.length === 1 ? matches[0].slice(name.length + 1) : undefined;
}

function isDevelopmentContainer(row, { repo, port, sourceValid }) {
  const labels = row.Config?.Labels ?? row.Labels ?? {};
  const config = row.Config ?? {};
  const image = row.ImageName ?? config.Image ?? row.Image ?? '';
  const entrypoint = config.Entrypoint;
  const env = config.Env ?? [];
  const command = config.Cmd;
  const mounts = row.Mounts ?? [];
  const ports = row.HostConfig?.PortBindings?.['5432/tcp'] ?? row.NetworkSettings?.Ports?.['5432/tcp'] ?? [];
  const expectedCompose = path.join(repo, 'infra/local/compose.yaml');
  const expectedWorking = path.join(repo, 'infra/local');
  const sourceMatches = labels['com.docker.compose.project.config_files'] === expectedCompose
    && labels['com.docker.compose.project.working_dir'] === expectedWorking;
  const targetMappings = ports.filter(value => (value.HostIp ?? value.host_ip) === '127.0.0.1'
    && String(value.HostPort ?? value.host_port) === String(port));
  const forbiddenEnv = Array.isArray(env) && env.some(value => {
    const name = String(value).split('=', 1)[0];
    return /PASSWORD|PASSFILE/.test(name) || /^POSTGRES_/.test(name)
      && !['POSTGRES_DB', 'POSTGRES_HOST_AUTH_METHOD', 'POSTGRES_USER'].includes(name);
  });
  const envValid = Array.isArray(env) && !forbiddenEnv
    && env.filter(value => value.startsWith('POSTGRES_DB=')).length === 1
    && env.filter(value => value.startsWith('POSTGRES_HOST_AUTH_METHOD=')).length === 1
    && env.filter(value => value.startsWith('POSTGRES_USER=')).length <= 1
    && oneEnv(env, 'POSTGRES_DB') === 'lexiflow'
    && oneEnv(env, 'POSTGRES_HOST_AUTH_METHOD') === 'trust'
    && [undefined, 'postgres'].includes(oneEnv(env, 'POSTGRES_USER'))
    ;
  const pgdata = oneEnv(env, 'PGDATA');
  const volume = mounts.filter(value => value.Destination === '/var/lib/postgresql/data');
  const volumeValid = volume.length === 1 && volume[0].Type === 'volume'
    && volume[0].Name === 'lexiflow-local_lexiflow-postgres-data' && volume[0].RW === true
    && !mounts.some(value => value.Destination !== '/var/lib/postgresql/data'
      && (value.Destination === '/var/lib/postgresql' || value.Destination?.startsWith('/var/lib/postgresql/data/')));
  const ownershipCandidate = labels['com.docker.compose.project'] === 'lexiflow-local'
    && labels['com.docker.compose.service'] === 'postgres';
  if (!ownershipCandidate) return null;
  if (!sourceValid) fail('DEVELOPMENT_DATABASE_SOURCE_INVALID');
  if (labels['io.podman.compose.project'] !== 'lexiflow-local' || labels['io.podman.compose.service'] !== 'postgres'
      || !sourceMatches) fail('DEVELOPMENT_DATABASE_REPOSITORY_CHANGED');
  if (image !== 'docker.io/library/postgres:17-alpine') fail('DEVELOPMENT_DATABASE_IMAGE_CHANGED');
  if (row.State?.Running !== true) fail('DEVELOPMENT_DATABASE_CONTAINER_CHANGED');
  if (targetMappings.length !== 1 || ports.length !== 1) fail('DEVELOPMENT_DATABASE_PORT_CHANGED');
  if (!envValid) fail('DEVELOPMENT_DATABASE_AUTH_CHANGED');
  if (pgdata !== '/var/lib/postgresql/data' || !Array.isArray(entrypoint)
      || entrypoint.length !== 1 || entrypoint[0] !== 'docker-entrypoint.sh' || !Array.isArray(command)
      || command.length !== 1 || command[0] !== 'postgres') fail('DEVELOPMENT_DATABASE_RUNTIME_CHANGED');
  if (!volumeValid) fail('DEVELOPMENT_DATABASE_VOLUME_CHANGED');
  if (typeof row.Id !== 'string' || !/^[a-f0-9]{64}$/.test(row.Id)
      || typeof row.Image !== 'string' || !/^(?:sha256:)?[a-f0-9]{64}$/.test(row.Image)) fail('DEVELOPMENT_DATABASE_INSPECTION_INVALID');
  const facts = { containerId: row.Id, imageId: row.Image, imageName: image,
    composePath: labels['com.docker.compose.project.config_files'], workingDirectory: labels['com.docker.compose.project.working_dir'],
    composeProject: labels['com.docker.compose.project'], service: labels['com.docker.compose.service'],
    database: oneEnv(env, 'POSTGRES_DB'), authMethod: oneEnv(env, 'POSTGRES_HOST_AUTH_METHOD'),
    user: oneEnv(env, 'POSTGRES_USER') ?? 'postgres', hostIp: '127.0.0.1', hostPort: String(port), containerPort: '5432',
    volumeName: volume[0].Name, volumeDestination: volume[0].Destination };
  return { ...facts, fingerprint: sha(canonicalJson(facts)) };
}

/** 仅通过 Podman 元数据识别本仓库运行的开发 PostgreSQL；不连接、不启停容器。 */
export async function inspectDevelopmentTrust({ run, repo, port = 15432 }) {
  if (typeof run !== 'function' || typeof repo !== 'string' || !path.isAbsolute(repo)
      || !Number.isInteger(port) || port < 1 || port > 65535) fail('DEVELOPMENT_DATABASE_INSPECTION_INVALID');
  const source = path.join(repo, 'infra/local/compose.yaml');
  let sourceValid = false;
  try {
    const sourceInfo = fs.lstatSync(source), repoRealPath = fs.realpathSync(repo);
    sourceValid = sourceInfo.isFile() && !sourceInfo.isSymbolicLink()
      && fs.realpathSync(source).startsWith(`${repoRealPath}${path.sep}`);
  } catch { sourceValid = false; }
  let ids;
  try { ids = (await run('podman', ['ps', '-q', '-a', '--filter', 'label=com.docker.compose.project=lexiflow-local'],
    { cwd: repo, capture: true, logOutput: false, timeout: 15000 })).trim().split(/\s+/).filter(Boolean); }
  catch { fail('DEVELOPMENT_DATABASE_INSPECTION_UNAVAILABLE'); }
  const found = [];
  try {
    for (const id of ids) {
      if (!/^[a-f0-9]{12,64}$/.test(id)) fail('DEVELOPMENT_DATABASE_INSPECTION_INVALID');
      const output = await run('podman', ['inspect', id], { cwd: repo, capture: true, logOutput: false, timeout: 15000 });
      const rows = JSON.parse(output);
      if (!Array.isArray(rows) || rows.length !== 1 || rows[0].Id !== id && !rows[0].Id?.startsWith(id))
        fail('DEVELOPMENT_DATABASE_INSPECTION_INVALID');
      const identity = isDevelopmentContainer(rows[0], { repo: fs.realpathSync(repo), port, sourceValid });
      if (identity) found.push(identity);
    }
  } catch (error) {
    if (/^DEVELOPMENT_DATABASE_/.test(error?.message ?? '')) throw error;
    fail('DEVELOPMENT_DATABASE_INSPECTION_UNAVAILABLE');
  }
  if (found.length > 1) fail('DEVELOPMENT_DATABASE_AMBIGUOUS');
  return found[0] ?? null;
}

function validate({ run, port, user, passwordFile, postgresImage, authMode = 'password', developmentIdentity }) {
  if (typeof run !== 'function' || !Number.isInteger(port) || port < 1 || port > 65535
      || typeof user !== 'string' || !user || user.length > 63 || /[\r\n\0]/.test(user)
      || !['password', 'development-trust'].includes(authMode)
      || authMode === 'development-trust' && (user !== 'postgres' || passwordFile !== undefined
        || !developmentIdentity || !/^[a-f0-9]{64}$/.test(developmentIdentity.fingerprint ?? ''))
      || authMode === 'password' && (typeof passwordFile !== 'string' || !path.isAbsolute(passwordFile) || /[\r\n\0:]/.test(passwordFile))
      || typeof postgresImage !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$/.test(postgresImage)) fail('DATABASE_CONNECTION_INPUT_INVALID');
  if (authMode === 'password') {
    let cursor = path.parse(passwordFile).root;
    for (const component of passwordFile.slice(cursor.length).split(path.sep).filter(Boolean)) {
      cursor = path.join(cursor, component);
      let part;
      try { part = fs.lstatSync(cursor); } catch { fail('DATABASE_PASSWORD_FILE_INVALID'); }
      if (part.isSymbolicLink()) fail('DATABASE_PASSWORD_FILE_INVALID');
    }
    const info = fs.lstatSync(passwordFile);
    if (!info.isFile() || info.uid !== process.getuid() || info.size < 1 || info.size > 4096 || (info.mode & 0o077) !== 0) fail('DATABASE_PASSWORD_FILE_INVALID');
  }
}
async function client(input, database, sql) {
  validate(input);
  try {
    const args = ['run', '--rm', '--pull=never', '--read-only', '--cap-drop=ALL',
      '--security-opt=no-new-privileges', '--memory=128m', '--env', 'PGCONNECT_TIMEOUT=5',
      '--env', 'PGOPTIONS=-c statement_timeout=10000 -c lock_timeout=5000',
      '--tmpfs', '/tmp:rw,nosuid,noexec,size=16m'];
    if (input.authMode === 'development-trust') args.push('--env', 'PGPASSWORD=', '--env', 'PGPASSFILE=');
    else args.push('--volume', `${input.passwordFile}:/run/secrets/db-password:ro`);
    args.push('--entrypoint', '/bin/sh', input.postgresImage, '-ec', input.authMode === 'development-trust' ? trustClientScript : clientScript,
      'lexiflow-client', String(input.port), input.user, database, sql);
    const output = await input.run('podman', args, { capture: true, logOutput: false, timeout: 15000 });
    if (typeof output !== 'string' || Buffer.byteLength(output) > 8192) fail('DATABASE_PROBE_INVALID');
    return output.trim();
  } catch { fail('DATABASE_AUTH_OR_CONNECT_FAILED'); }
}
function parsed(text) {
  try { const value = JSON.parse(text); if (!value || typeof value !== 'object' || Array.isArray(value)) fail('DATABASE_PROBE_INVALID'); return value; }
  catch { fail('DATABASE_PROBE_INVALID'); }
}

/** 经工具容器真实连接本机端口；不拉镜像、不写数据库。 */
export async function probeExternal(input) {
  validate(input);
  const meta = parsed(await client(input, 'lexiflow', metadataSql));
  if (meta.serverVersion !== 17) fail('DATABASE_POSTGRES_17_REQUIRED');
  if (meta.currentUser !== input.user || !Number.isSafeInteger(meta.userOid)
      || !Number.isSafeInteger(meta.databaseOid) || typeof meta.databaseOwner !== 'string') fail('DATABASE_PROBE_INVALID');
  const schema = parsed(await client(input, 'lexiflow', schemaSql));
  if (typeof schema.schemaExists !== 'boolean' || typeof schema.canCreateSchema !== 'boolean') fail('DATABASE_PROBE_INVALID');
  if (schema.schemaExists && (!Number.isSafeInteger(schema.schemaOid) || schema.owner !== input.user)) fail('DATABASE_SCHEMA_OWNER_MISMATCH');
  if (!schema.schemaExists && (schema.schemaOid !== null || schema.owner !== null)) fail('DATABASE_PROBE_INVALID');
  if (!schema.canCreateSchema) fail('DATABASE_SCHEMA_CREATE_PERMISSION_REQUIRED');
  const facts = { serverVersion: meta.serverVersion, currentUser: meta.currentUser, userOid: meta.userOid,
    databaseOid: meta.databaseOid,
    databaseOwner: meta.databaseOwner, schemaExists: schema.schemaExists, schemaOid: schema.schemaOid,
    owner: schema.owner, canCreateSchema: schema.canCreateSchema };
  const fingerprintFacts = input.authMode === 'development-trust'
    ? { ...facts, authMode: input.authMode, developmentIdentityFingerprint: input.developmentIdentity.fingerprint }
    : facts;
  return { ...facts, fingerprint: crypto.createHash('sha256').update(canonicalJson(fingerprintFacts)).digest('hex') };
}
