import assert from 'node:assert/strict';
import test from 'node:test';
import { readE2EState, waitForE2EState } from './e2e-state.mjs';

test('E2E wait keeps the exact assertion and ten-second deadline without retries', async () => {
  let calls = 0;
  await waitForE2EState({ async waitForFunction(predicate, state, options) {
    calls++;
    assert.equal(state, 'ready');
    assert.deepEqual(options, { timeout: 10_000 });
    const previous = globalThis.document;
    try {
      globalThis.document = { querySelector: () => ({ getAttribute: () => 'waiting' }) };
      assert.equal(predicate(state), false);
      globalThis.document = { querySelector: () => ({ getAttribute: () => state }) };
      assert.equal(predicate(state), true);
    } finally { globalThis.document = previous; }
  } }, 'ready');
  assert.equal(calls, 1);
});

test('E2E wait preserves failure identity and adds the observed state', async () => {
  const error = new Error('original timeout');
  let calls = 0;
  const page = {
    async waitForFunction() { calls++; throw error; },
    async evaluate() { return { state: 'idle', hidden: true, focused: false, counts: { source_hidden: 1 } }; }
  };
  await assert.rejects(waitForE2EState(page, 'ready'), value => {
    assert.equal(value, error);
    assert.match(value.message, /^original timeout\nsynthetic E2E state:/);
    assert.match(value.message, /"state":"idle"/);
    assert.match(value.stack, /synthetic E2E state:/);
    return true;
  });
  assert.equal(calls, 1, 'no pass-only retry');
});

test('closed page cannot replace the original E2E failure with diagnostic failure', async () => {
  const error = new Error('page closed');
  await assert.rejects(waitForE2EState({
    async waitForFunction() { throw error; },
    async evaluate() { throw new Error('diagnostics unavailable'); }
  }, 'ready'), value => value === error && value.message.includes('"unavailable":true'));
});

test('state diagnostics expose structure but never fixture captions or glosses', async () => {
  const previous = globalThis.document;
  const raw = 'private text must not appear';
  try {
    globalThis.document = {
      hidden: false, hasFocus: () => true,
      querySelector(selector) {
        if (selector === 'video') return { currentTime: 2, ended: false };
        if (selector === '#player') return { classList: { contains: () => true } };
        if (selector === '#ytp-caption-window-container') return { textContent: raw };
        return {
          getAttribute: name => name === 'data-lexiflow-state' ? 'ready' : JSON.stringify({ counts: { ready: 1 } }),
          shadowRoot: { querySelector: () => ({ textContent: raw }) }
        };
      }
    };
    const facts = await readE2EState({ evaluate: callback => callback() });
    assert.deepEqual(facts, { state: 'ready', hidden: false, focused: true, videoTime: 2, videoEnded: false,
      sourceLength: raw.length, overlayLength: raw.length, masked: true, counts: { ready: 1 } });
    assert.equal(JSON.stringify(facts).includes(raw), false);
  } finally { globalThis.document = previous; }
});
