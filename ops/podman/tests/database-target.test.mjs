import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { validateSqliteDatabaseFile, sqliteJdbcUrl } from '../environment/local-sqlite-target.mjs';
import { renderCompose } from '../installation/installation-upgrade-workflow.mjs';

test('installation database target is a dedicated absolute SQLite file', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-sqlite-target-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const db = path.join(root, 'data', 'lexicon.db');
  assert.equal(validateSqliteDatabaseFile(db, { installationRoot: root }), db);
  assert.equal(sqliteJdbcUrl(db), `jdbc:sqlite:${db}`);
  for (const oldTarget of ['https://invalid-target.invalid/lexicon.db', path.join(root, 'data', 'other.db'), path.join(os.tmpdir(), 'lexicon.db')]) {
    assert.throws(() => validateSqliteDatabaseFile(oldTarget, { installationRoot: root }), /DEPLOYMENT_DATABASE_/);
  }
});

test('existing target must be a regular non-symlink file and missing inspect target remains absent', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lexiflow-sqlite-target-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const db = path.join(root, 'data', 'lexicon.db');
  fs.mkdirSync(path.dirname(db));
  fs.writeFileSync(db, 'synthetic');
  assert.equal(validateSqliteDatabaseFile(db, { installationRoot: root, allowMissing: false }), db);
  fs.unlinkSync(db);
  assert.throws(() => validateSqliteDatabaseFile(db, { installationRoot: root, allowMissing: false }), /DEPLOYMENT_DATABASE_FILE_INVALID/);
  assert.equal(fs.existsSync(db), false);
  fs.symlinkSync(path.join(root, 'outside.db'), db);
  assert.throws(() => validateSqliteDatabaseFile(db, { installationRoot: root }), /DEPLOYMENT_DATABASE_FILE_INVALID/);
});

test('runtime compose binds only the installation SQLite file read-only', () => {
  const databaseFile = '/private/lexiflow/data/lexicon.db';
  const config = JSON.parse(renderCompose('/unused', { apiPort: 18081, databaseFile }));
  assert.deepEqual(Object.keys(config.services), ['api']);
  assert.deepEqual(config.services.api.volumes, ['${LEXIFLOW_DATABASE_FILE:?LEXIFLOW_DATABASE_FILE is required}:/var/lib/lexiflow/lexicon.db:ro']);
  assert.equal(config.services.api.environment.SPRING_DATASOURCE_URL, 'jdbc:sqlite:/var/lib/lexiflow/lexicon.db?mode=ro');
  assert.deepEqual(config.services.api.ports, ['127.0.0.1:18081:8080']);
  assert.deepEqual(Object.keys(config.services), ['api']);
  assert.throws(() => renderCompose('/unused', { apiPort: 18081, databaseFile: 'relative/lexicon.db' }), /DEPLOYMENT_COMPOSE_INPUT_INVALID/);
});
