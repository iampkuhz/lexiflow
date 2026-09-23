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
  assert.deepEqual(views.at(-1), { state: "ready", event: event(1), hints: [{ chineseGloss: "提示" }] });
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
  assert.equal(views.some((view) => view.hints?.[0].chineseGloss === "旧"), false);
  second.resolve({ ok: true, body: { state: "READY", hints: [{ chineseGloss: "新" }] } });
  await flush();
  assert.equal(views.at(-1).state, "ready");
  assert.equal(views.at(-1).hints[0].chineseGloss, "新");
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
  assert.equal(views.some((view) => view.hints?.[0].chineseGloss === "不得显示"), false);
});

test("measures coalescing and transport and distinguishes late/cancelled/timeout", async () => {
  const scheduler = new ManualScheduler();
  const samples = [];
  let time = 0;
  const old = deferred();
  const coordinator = new CaptionStreamCoordinator(
    e => ({ promise: e.sequence === 1 ? old.promise : Promise.resolve({ ok: false, reason: "timeout" }), cancel: () => {} }),
    () => {}, scheduler, value => samples.push(value), () => time
  );
  coordinator.submit(event(1)); time = 150; scheduler.runAll();
  coordinator.submit(event(2)); time = 300; scheduler.runAll();
  await flush();
  old.resolve({ ok: false, reason: "aborted" }); await flush();
  assert.equal(samples.filter(s => s.outcome === "requested").length, 2);
  assert.equal(samples.filter(s => s.outcome === "cancelled").length, 1);
  assert.equal(samples.filter(s => s.outcome === "late").length, 1);
  assert.equal(samples.filter(s => s.outcome === "timeout").length, 1);
  assert.equal(samples.filter(s => s.stage === "coalesce").every(s => s.elapsedMs === 150), true);
  assert.equal(samples.filter(s => s.stage === "transport").length, 1);
  assert.equal(JSON.stringify(samples).includes("caption"), false);
});

test("synchronous transport failure still falls back without preventing future captions", async () => {
  const scheduler = new ManualScheduler();
  const views = [];
  const coordinator = new CaptionStreamCoordinator(() => { throw new Error("transport"); }, view => views.push(view), scheduler);
  coordinator.submit(event(1)); scheduler.runAll();
  assert.equal(views.at(-1).state, "fallback");
  coordinator.submit(event(2)); scheduler.runAll();
  assert.equal(views.at(-1).state, "fallback");
});

test('uses frame-sized coalescing and attributes missed results to specific boundaries', async () => {
  const {COALESCE_MS}=await import('../dist/stream.js');
  assert.equal(COALESCE_MS,16);
  const scheduler=new ManualScheduler(), observations=[];
  const old=deferred(); let scheduledMs;
  const original=scheduler.setTimeout.bind(scheduler);
  scheduler.setTimeout=(fn,ms)=>{scheduledMs=ms;return original(fn);};
  const c=new CaptionStreamCoordinator(()=>({promise:old.promise,cancel:()=>{}}),()=>{},scheduler,o=>observations.push(o));
  c.submit(event(1)); c.submit(event(2));
  assert.equal(scheduledMs,16);
  scheduler.runAll(); c.clear(3);
  old.resolve({ok:true,body:{state:'READY',hints:[{chineseGloss:'提示'}]}}); await flush();
  for(const outcome of ['cancelled-before-request','cancelled-in-flight','late-ready'])
    assert.equal(observations.filter(o=>o.outcome===outcome).length,1);
});
