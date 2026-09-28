import type { Observation } from "./diagnostics";
import { parseHintResponse, resolveHint, snapshotSegments, snapshotText, type ApiResult, type CaptionHintRequest, type CaptionSnapshot, type Hint } from "./protocol";
import { remapHints, retainedOverlap } from "./caption-snapshot";
export const COALESCE_MS = 16;
export type CaptionEvent = { key: string; sequence: number; videoTimeMs: number; request: CaptionHintRequest };
export type StreamState = "idle" | "waiting" | "ready" | "no-pending" | "fallback";
export type StreamView = { state: StreamState; event?: CaptionEvent; hints?: Hint[] };
type Operation = { promise: Promise<ApiResult>; cancel: () => void };
export type RequestTransport = (event: CaptionEvent, requestId: string) => Operation;
export type Scheduler = { setTimeout: (callback: () => void, timeoutMs: number) => ReturnType<typeof setTimeout>;
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void };
/** 单请求串行；观察快照与已处理覆盖分离，新增不会清除已有提示。 */
export class CaptionStreamCoordinator {
  private generation = 0;
  private latestSequence = 0;
  private current?: CaptionEvent;
  private timer?: ReturnType<typeof setTimeout>;
  private active?: { generation: number; event: CaptionEvent; cancel: () => void; started: number };
  private lastAcknowledged: CaptionSnapshot | null = null;
  private completed = new Set<string>();
  private hints: Hint[] = [];
  private submittedAt = 0;
  private requestNumber = 0;
  constructor(private readonly transport: RequestTransport, private readonly render: (view: StreamView) => void,
    private readonly scheduler: Scheduler = globalThis, private readonly observe: (value: Observation) => void = () => undefined,
    private readonly now: () => number = () => performance.now()) {}
  submit(event: CaptionEvent): void {
    if (event.sequence <= this.latestSequence) return;
    this.latestSequence = event.sequence;
    const oldText = this.current ? snapshotText(this.current.request.currentSnapshot) : "";
    const nextText = snapshotText(event.request.currentSnapshot);
    if (this.current && (this.current.request.captionTopicKey !== event.request.captionTopicKey ||
        this.current.request.trackKey !== event.request.trackKey || !retainedOverlap(oldText, nextText))) this.invalidate(true);
    this.hints = remapHints(oldText, nextText, this.hints);
    this.current = structuredClone(event);
    const keys = new Set(snapshotSegments(event.request.currentSnapshot).map(segment => segment.key));
    this.completed = new Set([...this.completed].filter(key => keys.has(key)));
    this.submittedAt = this.now();
    this.publish(this.pending() ? "waiting" : this.hints.length ? "ready" : "no-pending");
    this.schedule(COALESCE_MS);
  }
  clear(sequence: number): void {
    if (sequence <= this.latestSequence) return;
    this.latestSequence = sequence; this.invalidate(true); this.current = undefined;
    this.render({ state: "idle" });
  }
  private invalidate(reset: boolean): void {
    this.generation++;
    if (this.timer !== undefined) { this.scheduler.clearTimeout(this.timer); this.timer = undefined; this.observe({ outcome: "cancelled-before-request" }); }
    if (this.active) { this.active.cancel(); this.active = undefined; this.observe({ outcome: "cancelled-in-flight" }); }
    if (reset) { this.completed.clear(); this.hints = []; this.lastAcknowledged = null; }
  }
  private pending(): boolean {
    return !!this.current && snapshotSegments(this.current.request.currentSnapshot).some(segment => !this.completed.has(segment.key));
  }
  private publish(state: StreamState): void { this.render({ state, event: this.current, hints: this.hints.map(hint => ({ ...hint })) }); }
  private schedule(delay: number): void {
    if (this.active || this.timer !== undefined || !this.pending()) return;
    this.timer = this.scheduler.setTimeout(() => { this.timer = undefined; this.dispatch(); }, delay);
  }
  private dispatch(): void {
    if (!this.current || !this.pending()) return;
    const event = structuredClone(this.current);
    for (const segment of snapshotSegments(event.request.currentSnapshot)) segment.append = !this.completed.has(segment.key);
    event.request.lastRequestedSnapshot = structuredClone(this.lastAcknowledged);
    const generation = this.generation, started = this.now();
    this.observe({ stage: "coalesce", elapsedMs: started - this.submittedAt, outcome: "requested" });
    // 同步抛错与异步通信失败一致：保留确认快照，等待下一次字幕变化。
    let operation: Operation;
    try { operation = this.transport(event, `caption-${++this.requestNumber}`); }
    catch { operation = { promise: Promise.resolve({ ok: false, reason: "network" }), cancel: () => undefined }; }
    this.active = { generation, event, started, cancel: operation.cancel };
    void operation.promise.then(result => this.accept(generation, event, result)).catch(() => this.accept(generation, event, { ok: false, reason: "network" }));
  }
  private accept(generation: number, event: CaptionEvent, result: ApiResult): void {
    if (generation !== this.generation || !this.current || this.active?.event !== event) { this.observe({ outcome: "late" }); return; }
    this.observe({ stage: "transport", elapsedMs: this.now() - this.active.started });
    this.active = undefined;
    for (const stage of ["query", "rules", "api"] as const) {
      if (result.timings?.[stage] !== undefined) this.observe({ stage, elapsedMs: result.timings[stage] });
    }
    if (result.ok && !parseHintResponse(result.body, event.request)) result = { ok: false, reason: "invalid-response" };
    if (!result.ok) {
      this.observe({ outcome: result.reason }); this.publish("fallback");
      return;
    }
    // 只确认已校验响应对应的发送快照，不把在途新增内容误标为成功。
    this.lastAcknowledged = structuredClone(event.request.currentSnapshot);
    const visible = new Set(snapshotSegments(this.current.request.currentSnapshot).map(segment => segment.key));
    for (const key of result.body.processedKeys) if (visible.has(key)) this.completed.add(key);
    const resolved = result.body.hints.map(hint => resolveHint(hint, event.request.currentSnapshot)).filter((hint): hint is Hint => !!hint);
    const mapped = remapHints(snapshotText(event.request.currentSnapshot), snapshotText(this.current.request.currentSnapshot), resolved);
    // 版本不能在同一视图混合；已展示资料保持不动，后到的异版本提示不混入。
    const version = this.hints[0]?.lexiconVersion;
    for (const hint of mapped) {
      if (version !== undefined && hint.lexiconVersion !== version) continue;
      if (!this.hints.some(old => old.lexiconEntryId === hint.lexiconEntryId ||
          (old.startOffset < hint.endOffset && hint.startOffset < old.endOffset))) this.hints.push(hint);
    }
    this.hints.sort((left, right) => left.startOffset - right.startOffset);
    this.observe({ outcome: this.hints.length ? "ready" : "no-pending" });
    this.publish(this.pending() ? "waiting" : this.hints.length ? "ready" : "no-pending");
    this.schedule(COALESCE_MS);
  }
}
