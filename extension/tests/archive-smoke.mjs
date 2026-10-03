import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { checkExtensionArchive } from '../scripts/archive-check.mjs';

/** Load the verified archive in an isolated Playwright Chromium profile (not a manual Chrome install). */
export async function smokeExtensionArchive(zipPath, expectedCommit) {
  const checked = await checkExtensionArchive(zipPath, expectedCommit, undefined, { verifyCurrentIdentity: false });
  const temp = await mkdtemp(path.join(tmpdir(), 'lexiflow-extension-archive-smoke-'));
  const extensionDir = path.join(temp, 'extension');
  const profileDir = path.join(temp, 'profile');
  let context;
  const errors = [];
  try {
    for (const [name, bytes] of checked.entries) {
      const target = path.join(extensionDir, name);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, bytes, { flag: 'wx' });
    }
    context = await chromium.launchPersistentContext(profileDir, {
      headless: true,
      channel: 'chromium',
      args: [`--disable-extensions-except=${extensionDir}`, `--load-extension=${extensionDir}`],
    });
    context.on('page', page => {
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    });
    const worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker', { timeout: 15000 });
    worker.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    assert.match(worker.url(), /^chrome-extension:\/\//u);
    const extensionId = new URL(worker.url()).host;
    assert.equal(await worker.evaluate(() => chrome.runtime.getManifest().version), checked.buildIdentity.chromeVersion);
    // 只验证扩展加载，绝不连接用户现有的本机 API。
    await worker.evaluate(() => {
      const originalFetch = globalThis.fetch;
      globalThis.fetch = (input, init) => String(input).startsWith('http://127.0.0.1:')
        ? Promise.resolve(new Response('{}', { status: 503 })) : originalFetch(input, init);
    });
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    await popup.waitForLoadState('domcontentloaded');
    assert.equal(await popup.locator('#enhance-toggle').count(), 1);
    assert.equal(await popup.locator('html').getAttribute('lang'), 'zh-CN');
    await popup.waitForFunction(() => !document.querySelector('#service-status').textContent.includes('正在查询'));
    assert.deepEqual(errors, [], 'extension scripts must not emit page errors or console errors');
    return { status: 'PASS', extensionId, checks: ['manifest-loaded', 'service-worker-started', 'popup-runtime-loaded', 'no-script-errors'] };
  } finally {
    await context?.close();
    await rm(temp, { recursive: true, force: true });
  }
}

function invokedDirectly() { try { return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url); } catch { return false; } }
if (invokedDirectly()) {
  const args = process.argv.slice(2);
  if (args.length !== 2) { process.stderr.write('usage: node extension/tests/archive-smoke.mjs <zip-path> <expected-commit>\n'); process.exitCode = 2; }
  else smokeExtensionArchive(args[0], args[1]).then(result => process.stdout.write(`${JSON.stringify(result)}\n`)).catch(error => { process.stderr.write(`extension archive smoke failed: ${error.message}\n`); process.exitCode = 1; });
}
