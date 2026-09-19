import assert from "node:assert/strict";
import test from "node:test";
import { CaptionStreamCoordinator } from "../dist/stream.js";

function deferred() {
  let resolve;
  const promise = new Promise((next) => (resolve = next));
  return { promise, resolve };
}

class ManualScheduler {
  #jobs = new Map();
  #next = 0;

  setTimeout(callback) {
    const id = ++this.#next;
    this.#jobs.set(id, callback);
    return id;
  }

  clearTimeout(id) {
    this.#jobs.delete(id);
  }

  runAll() {
    const jobs = [...this.#jobs.values()];
    this.#jobs.clear();
    jobs.forEach((job) => job());
  }
}

function event(sequence, caption = `caption ${sequence}`) {
  return {
    key: `video\u0000revision\u0000${caption}`,
    sequence,
    videoTimeMs: sequence * 1000,
    request: {
      contentId: "00000000-0000-5000-8000-000000000001",
      contentRevision: 1,
      segmentId: "a".repeat(64),
      caption,
      startOffset: 0,
      endOffset: caption.length
    }
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

test("coalesces DOM churn into one request for the visible caption", async () => {
  const scheduler = new ManualScheduler();
  const views = [];
  const calls = [];
  const result = deferred();
  const coordinator = new CaptionStreamCoordinator(
    (caption, requestId) => {
      calls.push({ caption, requestId });
      return { promise: result.promise, cancel: () => assert.fail("must not cancel the active caption") };
    },
    (view) => views.push(view),
    scheduler
  );

  coordinator.submit(event(1));
  coordinator.submit(event(1));
  scheduler.runAll();
  assert.equal(calls.length, 1);
  result.resolve({ ok: true, body: { state: "READY", hints: [{ chineseGloss: "提示" }] } });
  await flush();
  assert.deepEqual(views.at(-1), { state: "ready", event: event(1), gloss: "提示" });
});

test("cancels a replaced request and never renders its late result", async () => {
  const scheduler = new ManualScheduler();
  const views = [];
  const first = deferred();
  const second = deferred();
  let cancelled = 0;
  const coordinator = new CaptionStreamCoordinator(
    (caption) => ({
      promise: caption.sequence === 1 ? first.promise : second.promise,
      cancel: () => (cancelled += 1)
    }),
    (view) => views.push(view),
    scheduler
  );

  coordinator.submit(event(1, "old"));
  scheduler.runAll();
  coordinator.submit(event(2, "new"));
  assert.equal(cancelled, 1);
  scheduler.runAll();
  first.resolve({ ok: true, body: { state: "READY", hints: [{ chineseGloss: "旧" }] } });
  await flush();
  assert.equal(views.some((view) => view.gloss === "旧"), false);
  second.resolve({ ok: true, body: { state: "READY", hints: [{ chineseGloss: "新" }] } });
  await flush();
  assert.equal(views.at(-1).state, "ready");
  assert.equal(views.at(-1).gloss, "新");
});

test("makes NO_PENDING and failures terminal English-only states without retries", async () => {
  const scheduler = new ManualScheduler();
  const views = [];
  let calls = 0;
  const coordinator = new CaptionStreamCoordinator(
    (caption) => {
      calls += 1;
      return {
        promise: Promise.resolve(
          caption.sequence === 1
            ? { ok: true, body: { state: "NO_PENDING", hints: [] } }
            : { ok: false, reason: "network" }
        ),
        cancel: () => undefined
      };
    },
    (view) => views.push(view),
    scheduler
  );

  coordinator.submit(event(1));
  scheduler.runAll();
  await flush();
  assert.equal(views.at(-1).state, "no-pending");
  coordinator.submit(event(2));
  scheduler.runAll();
  await flush();
  assert.equal(views.at(-1).state, "fallback");
  assert.equal(calls, 2);
});

test("clearing the source invalidates in-flight work and removes the overlay state", async () => {
  const scheduler = new ManualScheduler();
  const views = [];
  const delayed = deferred();
  let cancelled = false;
  const coordinator = new CaptionStreamCoordinator(
    () => ({ promise: delayed.promise, cancel: () => (cancelled = true) }),
    (view) => views.push(view),
    scheduler
  );

  coordinator.submit(event(1));
  scheduler.runAll();
  coordinator.clear(2);
  assert.equal(cancelled, true);
  delayed.resolve({ ok: true, body: { state: "READY", hints: [{ chineseGloss: "不得显示" }] } });
  await flush();
  assert.equal(views.at(-1).state, "idle");
  assert.equal(views.some((view) => view.gloss === "不得显示"), false);
});
