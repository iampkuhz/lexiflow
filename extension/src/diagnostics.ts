/** Fixed-cardinality, in-memory diagnostics. No captions, IDs, URLs or preference values. */
export type TimingStage = "acquisition" | "coalesce" | "transport" | "query" | "rules" | "api" | "render" | "endToEnd";
export type Outcome = "observed" | "requested" | "shown" | "ready" | "no-pending" | "network" | "timeout" |
  "aborted" | "invalid-request" | "rejected" | "invalid-response" | "cancelled" | "late" | "suppressed" | "oversized" |
  "cancelled-acquisition" | "cancelled-before-request" | "cancelled-in-flight" | "late-ready" | "stale-at-render" | "missing-server-timing";
export type Observation = { stage?: TimingStage; elapsedMs?: number; outcome?: Outcome };
const stages: TimingStage[] = ["acquisition", "coalesce", "transport", "query", "rules", "api", "render", "endToEnd"];
const outcomes: Outcome[] = ["observed", "requested", "shown", "ready", "no-pending", "network", "timeout", "aborted",
  "invalid-request", "rejected", "invalid-response", "cancelled", "late", "suppressed", "oversized",
  "cancelled-acquisition", "cancelled-before-request", "cancelled-in-flight", "late-ready", "stale-at-render", "missing-server-timing"];
const SAMPLE_LIMIT = 256;

type Series = { count: number; totalMs: number; samples: number[] };

export class Diagnostics {
  private timings = new Map<TimingStage, Series>();
  private counts = new Map<Outcome, number>();

  record(observation: Observation): void {
    const { stage, elapsedMs, outcome } = observation;
    if (outcome && outcomes.includes(outcome)) this.counts.set(outcome, (this.counts.get(outcome) ?? 0) + 1);
    if (!stage || !stages.includes(stage) || typeof elapsedMs !== "number" ||
        !Number.isFinite(elapsedMs) || elapsedMs < 0 || elapsedMs > 60_000) return;
    const series = this.timings.get(stage) ?? { count: 0, totalMs: 0, samples: [] };
    series.count += 1;
    series.totalMs += elapsedMs;
    series.samples.push(elapsedMs);
    if (series.samples.length > SAMPLE_LIMIT) series.samples.shift();
    this.timings.set(stage, series);
  }

  snapshot() {
    return {
      sampleLimit: SAMPLE_LIMIT,
      counts: Object.fromEntries(outcomes.map(key => [key, this.counts.get(key) ?? 0])),
      timings: Object.fromEntries(stages.map(key => {
        const series = this.timings.get(key);
        const ordered = [...series?.samples ?? []].sort((a, b) => a - b);
        const percentile = (p: number) => ordered.length ? ordered[Math.ceil(ordered.length * p) - 1] : null;
        return [key, { count: series?.count ?? 0, sampleCount: ordered.length,
          meanMs: series ? series.totalMs / series.count : null, p50Ms: percentile(.5), p95Ms: percentile(.95) }];
      }))
    };
  }

  reset(): void {
    this.timings.clear();
    this.counts.clear();
  }
}
