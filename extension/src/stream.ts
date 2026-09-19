import type { ApiResult, CaptionHintRequest } from "./protocol";

export type CaptionEvent = {
  key: string;
  sequence: number;
  videoTimeMs: number;
  request: CaptionHintRequest;
};

export type StreamState = "idle" | "waiting" | "ready" | "no-pending" | "fallback";
export type StreamView = { state: StreamState; event?: CaptionEvent; gloss?: string };

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

  constructor(
    private readonly transport: RequestTransport,
    private readonly render: (view: StreamView) => void,
    scheduler: Scheduler = globalThis
  ) {
    this.scheduler = scheduler;
  }

  submit(event: CaptionEvent): void {
    if (event.sequence <= this.latestSequence) return;
    this.latestSequence = event.sequence;
    if (this.current?.key === event.key) return;
    this.invalidate();
    this.current = event;
    this.render({ state: "waiting", event });
    this.timer = this.scheduler.setTimeout(() => this.dispatch(event), 150);
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
      this.timer = undefined;
    }
    this.active?.cancel();
    this.active = undefined;
  }

  private dispatch(event: CaptionEvent): void {
    this.timer = undefined;
    if (this.current?.key !== event.key) return;
    const generation = this.generation;
    const requestId = `caption-${event.sequence}-${generation}`;
    const operation = this.transport(event, requestId);
    this.active = { generation, event, cancel: operation.cancel };
    void operation.promise
      .then((result) => this.accept(generation, event, result))
      .catch(() => this.accept(generation, event, { ok: false, reason: "network" }));
  }

  private accept(generation: number, event: CaptionEvent, result: ApiResult): void {
    if (this.generation !== generation || this.current?.key !== event.key) return;
    this.active = undefined;
    if (!result.ok) {
      this.render({ state: "fallback", event });
      return;
    }
    if (result.body.state === "READY" && result.body.hints.length > 0) {
      this.render({ state: "ready", event, gloss: result.body.hints[0].chineseGloss });
      return;
    }
    this.render({ state: "no-pending", event });
  }
}
