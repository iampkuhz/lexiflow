import type { Observation } from "./diagnostics";
import type { ApiResult, CaptionHintRequest, Hint } from "./protocol";

export const COALESCE_MS = 16;

export type CaptionEvent = {
  key: string;
  sequence: number;
  videoTimeMs: number;
  request: CaptionHintRequest;
};

export type StreamState = "idle" | "waiting" | "ready" | "no-pending" | "fallback";
export type StreamView = { state: StreamState; event?: CaptionEvent; hints?: Hint[] };

type Operation = { promise: Promise<ApiResult>; cancel: () => void };
export type RequestTransport = (event: CaptionEvent, requestId: string) => Operation;
export type Scheduler = {
  setTimeout: (callback: () => void, timeoutMs: number) => ReturnType<typeof setTimeout>;
  clearTimeout: (timer: ReturnType<typeof setTimeout>) => void;
};

/**
 * Serializes the currently visible subtitle only. A replacement invalidates and cancels the
 * previous request; late results are ignored by sequence and generation, never rendered.
 */
export class CaptionStreamCoordinator {
  private readonly scheduler: Scheduler;
  private generation = 0;
  private latestSequence = 0;
  private current?: CaptionEvent;
  private timer?: ReturnType<typeof setTimeout>;
  private active?: { generation: number; event: CaptionEvent; cancel: () => void };
  private submittedAt = 0;
  private dispatchedAt = 0;

  constructor(
    private readonly transport: RequestTransport,
    private readonly render: (view: StreamView) => void,
    scheduler: Scheduler = globalThis,
    private readonly observe: (value: Observation) => void = () => undefined,
    private readonly now: () => number = () => performance.now()
  ) {
    this.scheduler = scheduler;
  }

  submit(event: CaptionEvent): void {
    if (event.sequence <= this.latestSequence) return;
    this.latestSequence = event.sequence;
    if (this.current?.key === event.key) return;
    this.invalidate();
    this.current = event;
    this.submittedAt = this.now();
    this.render({ state: "waiting", event });
    this.timer = this.scheduler.setTimeout(() => this.dispatch(event), COALESCE_MS);
  }

  clear(sequence: number): void {
    if (sequence <= this.latestSequence) return;
    this.latestSequence = sequence;
    this.invalidate();
    this.current = undefined;
    this.render({ state: "idle" });
  }

  private invalidate(): void {
    this.generation += 1;
    if (this.timer !== undefined) {
      this.scheduler.clearTimeout(this.timer);
      this.observe({ outcome: "cancelled" });
      this.observe({ outcome: "cancelled-before-request" });
      this.timer = undefined;
    }
    if (this.active) {
      this.active.cancel();
      this.observe({ outcome: "cancelled" });
      this.observe({ outcome: "cancelled-in-flight" });
    }
    this.active = undefined;
  }

  private dispatch(event: CaptionEvent): void {
    this.timer = undefined;
    if (this.current?.key !== event.key) return;
    const generation = this.generation;
    const requestId = `caption-${event.sequence}-${generation}`;
    this.dispatchedAt = this.now();
    this.observe({ stage: "coalesce", elapsedMs: this.dispatchedAt - this.submittedAt, outcome: "requested" });
    let operation: Operation;
    try { operation = this.transport(event, requestId); }
    catch { this.accept(generation, event, { ok: false, reason: "network" }); return; }
    this.active = { generation, event, cancel: operation.cancel };
    void operation.promise
      .then((result) => this.accept(generation, event, result))
      .catch(() => this.accept(generation, event, { ok: false, reason: "network" }));
  }

  private accept(generation: number, event: CaptionEvent, result: ApiResult): void {
    if (this.generation !== generation || this.current?.key !== event.key) {
      this.observe({ outcome: "late" });
      if (result.ok && result.body.state === "READY" && result.body.hints.length > 0) {
        this.observe({ outcome: "late-ready" });
      }
      return;
    }
    this.observe({ stage: "transport", elapsedMs: this.now() - this.dispatchedAt });
    if (result.ok && (["query", "rules", "api"] as const).some(stage => result.timings?.[stage] === undefined)) {
      this.observe({ outcome: "missing-server-timing" });
    }
    if (result.timings) {
      for (const stage of ["query", "rules", "api"] as const) {
        const elapsedMs = result.timings[stage];
        if (elapsedMs !== undefined) this.observe({ stage, elapsedMs });
      }
    }
    this.active = undefined;
    if (!result.ok) {
      this.observe({ outcome: result.reason });
      this.render({ state: "fallback", event });
      return;
    }
    if (result.body.state === "READY" && result.body.hints.length > 0) {
      this.observe({ outcome: "ready" });
      this.render({ state: "ready", event, hints: result.body.hints });
      return;
    }
    this.observe({ outcome: "no-pending" });
    this.render({ state: "no-pending", event });
  }
}
