import { useMemo } from "react";

import type { MetricSample, RequestSample } from "../lib/api";
import {
  DECODE_THROUGHPUT_FORMULA,
  decodeThroughputPerRequest,
  engineTimingsPerRequest,
  histogramBins,
  pooledGaps,
  percentileSummary,
  telemetrySeries,
  ttftPerRequest,
  measurementSamples,
  isFiniteNumber,
  type PercentileSummary,
  type RequestSampleWithGaps,
} from "../lib/chart-data";
import { formatValue } from "../lib/chart-scale";
import { ChartFigure } from "./charts/ChartParts";
import { HistogramChart } from "./charts/HistogramChart";
import { HorizontalBarChart } from "./charts/HorizontalBarChart";
import { RequestPointChart } from "./charts/RequestPointChart";
import { TimeSeriesChart } from "./charts/TimeSeriesChart";

function metricTitle(name: string): string {
  const known: Record<string, string> = {
    host_cpu_percent: "Host CPU utilisation",
    host_memory_used_percent: "Host memory used",
    host_memory_available_bytes: "Host memory available",
    worker_process_cpu_percent: "Benchmark worker CPU",
    worker_process_rss_bytes: "Benchmark worker resident memory",
    load_duration: "Model load duration",
    prompt_eval_duration: "Prompt evaluation duration",
    eval_duration: "Generation (eval) duration",
    total_duration: "Total server duration",
  };
  const suffix = name.includes(".") ? name.slice(name.indexOf(".") + 1) : name;
  return known[suffix] ?? suffix.replaceAll("_", " ");
}

/**
 * Per-request and distribution charts for the run detail view. Everything is computed
 * from the `samples` and `metrics` already held (and live-refreshed) by RunsScreen.
 */
export function RunRequestCharts({
  samples,
  metrics,
  catchingUp,
}: {
  samples: RequestSample[];
  metrics: MetricSample[];
  catchingUp: boolean;
}) {
  const withGaps = samples as RequestSampleWithGaps[];
  const ttft = useMemo(() => ttftPerRequest(samples), [samples]);
  const throughput = useMemo(() => decodeThroughputPerRequest(samples), [samples]);
  const gaps = useMemo(() => pooledGaps(withGaps), [withGaps]);
  const bins = useMemo(() => histogramBins(gaps.values), [gaps]);
  const engineTimings = useMemo(
    () => engineTimingsPerRequest(metrics, samples),
    [metrics, samples],
  );
  const percentileFamilies = useMemo(() => {
    const measured = measurementSamples(samples).filter((sample) => sample.outcome === "succeeded");
    const tokenGaps = pooledGaps(withGaps);
    return [
      {
        id: "latency",
        title: "Request latency",
        summary: percentileSummary(
          metrics,
          "request_latency_ms",
          measured.map((sample) => sample.latency_ms).filter(isFiniteNumber),
          "ms",
        ),
      },
      {
        id: "ttft",
        title: "Time to first token",
        summary: percentileSummary(
          metrics,
          "ttft_ms",
          measured.map((sample) => sample.ttft_ms).filter(isFiniteNumber),
          "ms",
        ),
      },
      {
        id: "inter-event",
        title: "Inter-event latency",
        summary: percentileSummary(
          metrics,
          "inter_event_latency_ms",
          pooledEventGaps(withGaps),
          "ms",
        ),
      },
      {
        id: "inter-token",
        title: "Inter-token latency",
        summary: percentileSummary(
          metrics,
          "inter_token_latency_ms",
          tokenGaps.kind === "token" ? tokenGaps.values : [],
          "ms",
        ),
      },
      {
        id: "tpot",
        title: "Time per output token",
        summary: percentileSummary(metrics, "tpot_ms_per_token", [], "ms/token"),
      },
    ];
  }, [metrics, samples, withGaps]);

  const measuredCount = measurementSamples(samples).length;
  const restoring = catchingUp && measuredCount === 0;
  const tokenSourceText = throughput.tokenSources.length
    ? ` Token counts: ${throughput.tokenSources.join(", ").replaceAll("_", " ")}.`
    : "";
  const throughputExclusions = [
    throughput.missingTokenCount
      ? `${throughput.missingTokenCount} succeeded request(s) excluded: the engine reported no completion token count.`
      : null,
    throughput.missingTiming
      ? `${throughput.missingTiming} excluded: latency or TTFT not recorded.`
      : null,
    throughput.emptyWindow
      ? `${throughput.emptyWindow} excluded: no time between first output and completion.`
      : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <section className="run-subsection" aria-labelledby="run-request-charts-title">
      <div className="section-title-row">
        <div>
          <h3 id="run-request-charts-title">Request timing charts</h3>
          <p className="helper-copy">
            Measurement-phase requests only (warmup excluded) · computed from stored request samples
            and persisted metrics · updates while the run is live. Missing values are shown as
            missing, never as zero.
          </p>
        </div>
      </div>
      <div className="lf-chart-grid">
        <ChartFigure
          id="run-ttft-chart"
          title="Time to first token per request"
          subtitle="Client-measured ttft_ms · milliseconds"
          empty={
            ttft.points.length > 0
              ? null
              : restoring
                ? "Restoring persisted request samples."
                : ttft.missing > 0
                  ? `${ttft.missing} measured request(s) finished without a first-output observation, so no TTFT is available.`
                  : "No measured request has produced a first output yet."
          }
          caption={
            ttft.missing > 0
              ? `${ttft.missing} measured request(s) have no TTFT observation and are not plotted.`
              : undefined
          }
          table={{
            caption: "Time to first token for each measured request",
            columns: ["Request", "TTFT", "Outcome"],
            rows: ttft.points.map((point) => ({
              key: point.key,
              cells: [point.requestNumber, formatValue(point.value, "ms"), point.detail ?? ""],
            })),
          }}
        >
          <RequestPointChart
            ariaLabel="Time to first token per measured request, milliseconds"
            points={ttft.points}
            unit="ms"
            valueLabel="TTFT"
            yLabel="TTFT (ms)"
          />
        </ChartFigure>

        <ChartFigure
          id="run-decode-chart"
          title="Decode throughput per request"
          subtitle="Output tokens per second after the first output"
          empty={
            throughput.points.length > 0
              ? null
              : restoring
                ? "Restoring persisted request samples."
                : throughput.missingTokenCount > 0
                  ? `The engine did not report completion token counts for ${throughput.missingTokenCount} succeeded request(s), so per-request decode throughput cannot be computed.`
                  : "No succeeded measured request with token counts and timing is available yet."
          }
          caption={
            <>
              Formula: {DECODE_THROUGHPUT_FORMULA}.{tokenSourceText}
              {throughputExclusions ? ` ${throughputExclusions}` : ""}
            </>
          }
          table={{
            caption: "Decode throughput for each succeeded measured request",
            columns: ["Request", "Decode throughput", "Inputs"],
            rows: throughput.points.map((point) => ({
              key: point.key,
              cells: [
                point.requestNumber,
                formatValue(point.value, "tokens/s"),
                point.detail ?? "",
              ],
            })),
          }}
        >
          <RequestPointChart
            ariaLabel="Decode throughput per measured request, output tokens per second"
            points={throughput.points}
            unit="tokens/s"
            valueLabel="Decode"
            yLabel="Tokens / s"
          />
        </ChartFigure>

        <ChartFigure
          id="run-gap-histogram"
          title={
            gaps.kind === "event"
              ? "Inter-event latency distribution"
              : "Inter-token latency distribution"
          }
          subtitle={
            gaps.kind
              ? `${gaps.values.length.toLocaleString()} gaps from ${gaps.requestCount} measured request(s) · milliseconds`
              : undefined
          }
          empty={
            gaps.kind
              ? null
              : restoring
                ? "Restoring persisted request samples."
                : "No inter-token or inter-event gaps have been recorded for measured requests yet."
          }
          caption={
            gaps.kind === "event"
              ? "Source: inter_event_latency_ms. The engine did not provide per-token timestamps, so these are gaps between streamed output events; one event can carry more than one token."
              : gaps.kind === "token"
                ? "Source: inter_token_latency_ms (per-token timestamps)."
                : undefined
          }
          table={{
            caption: "Histogram bins of output gaps",
            columns: ["Bin (ms)", "Count"],
            rows: bins.map((bin) => ({
              key: `${bin.start}`,
              cells: [`${formatValue(bin.start)}–${formatValue(bin.end)}`, bin.count],
            })),
          }}
        >
          <HistogramChart
            ariaLabel={`Histogram of ${gaps.kind === "event" ? "inter-event" : "inter-token"} latency in milliseconds`}
            bins={bins}
            unit="ms"
            xLabel={`${gaps.kind === "event" ? "Gap between output events" : "Gap between tokens"} (ms)`}
          />
        </ChartFigure>
      </div>

      <h4 className="lf-chart-group-title">Latency percentiles</h4>
      <div className="lf-chart-grid">
        {percentileFamilies.map((family) => (
          <PercentileChart family={family} key={family.id} restoring={restoring} />
        ))}
      </div>

      <h4 className="lf-chart-group-title">Engine-reported timings per request</h4>
      {engineTimings.length === 0 ? (
        <p className="chart-no-data" role="status">
          {restoring
            ? "Restoring persisted metrics."
            : "The engine reported no per-request native timings for measured requests. Engine-native values are unavailable, not zero."}
        </p>
      ) : (
        <div className="lf-chart-grid">
          {engineTimings.map((series) => (
            <ChartFigure
              id={`run-engine-${series.metricName.replaceAll(".", "-")}`}
              key={series.metricName}
              title={metricTitle(series.metricName)}
              subtitle={`${series.metricName} · engine native · ${series.unit}`}
              caption={
                series.sourceUnit !== series.unit
                  ? `${series.definition} Reported in ${series.sourceUnit}; shown in ${series.unit}.`
                  : series.definition
              }
              table={{
                caption: `${series.metricName} for each measured request`,
                columns: ["Request", "Value"],
                rows: series.points.map((point) => ({
                  key: point.key,
                  cells: [point.requestNumber, formatValue(point.value, series.unit)],
                })),
              }}
            >
              <RequestPointChart
                ariaLabel={`${metricTitle(series.metricName)} per measured request, ${series.unit}`}
                height={180}
                points={series.points}
                unit={series.unit}
                valueLabel={metricTitle(series.metricName)}
                yLabel={
                  ["ms", "s", "ns", "us"].includes(series.unit)
                    ? `Duration (${series.unit})`
                    : series.unit
                }
              />
            </ChartFigure>
          ))}
        </div>
      )}
    </section>
  );
}

function pooledEventGaps(samples: RequestSampleWithGaps[]): number[] {
  return measurementSamples(samples)
    .filter((sample) => sample.outcome === "succeeded")
    .flatMap((sample) =>
      Array.isArray(sample.inter_event_latency_ms)
        ? sample.inter_event_latency_ms.filter(isFiniteNumber)
        : [],
    );
}

function PercentileChart({
  family,
  restoring,
}: {
  family: { id: string; title: string; summary: PercentileSummary };
  restoring: boolean;
}) {
  const { summary } = family;
  return (
    <ChartFigure
      id={`run-percentiles-${family.id}`}
      title={`${family.title} percentiles`}
      subtitle={
        summary.source === "api"
          ? `Persisted run metrics · ${summary.unit}`
          : summary.source === "derived"
            ? `Derived in the browser from request samples (provisional) · ${summary.unit}`
            : undefined
      }
      empty={
        summary.rows.length === 0
          ? restoring
            ? "Restoring persisted metrics."
            : "No observations are available for this metric yet."
          : summary.rows.every((row) => row.value === null)
            ? `All percentiles are unavailable: ${summary.rows[0]?.reason ?? "not reported"}.`
            : null
      }
      caption={summary.formula ? `Method: ${summary.formula}.` : undefined}
      table={{
        caption: `${family.title} percentiles`,
        columns: ["Percentile", "Value", "Samples", "Status"],
        rows: summary.rows.map((row) => ({
          key: row.key,
          cells: [
            row.label,
            formatValue(row.value, summary.unit),
            row.sampleCount,
            row.reason ?? (summary.source === "derived" ? "Derived" : "Reported"),
          ],
        })),
      }}
    >
      <HorizontalBarChart
        ariaLabel={`${family.title} percentiles in ${summary.unit}`}
        rows={summary.rows.map((row) => ({
          key: row.key,
          label: row.label,
          value: row.value,
          // Short inline reason; the full quality flags stay in the data table.
          reason: row.reason?.match(/needs ≥\d+[^·]*/)?.[0]?.trim() ?? row.reason,
          note: `n=${row.sampleCount}`,
        }))}
        unit={summary.unit}
        xLabel={`${family.title} (${summary.unit})`}
      />
    </ChartFigure>
  );
}

/** Host and worker resource telemetry (source `system_observed`) over the run timeline. */
export function RunTelemetryCharts({
  metrics,
  runStartedAt,
}: {
  metrics: MetricSample[];
  runStartedAt: string | null;
}) {
  const series = useMemo(() => telemetrySeries(metrics, runStartedAt), [metrics, runStartedAt]);
  if (series.length === 0) return null;
  return (
    <div className="lf-chart-grid lf-chart-grid--compact">
      {series.map((item) => (
        <ChartFigure
          id={`run-telemetry-${item.id.replace(/[^a-z0-9]+/gi, "-")}`}
          key={item.id}
          title={metricTitle(item.metricName)}
          subtitle={`${item.metricName}${item.scope ? ` · ${item.scope.replaceAll("_", " ")}` : ""} · ${item.unit}`}
          caption={
            item.sourceUnit !== item.unit
              ? `${item.definition} Reported in ${item.sourceUnit}; shown in ${item.unit}.`
              : item.definition
          }
          table={{
            caption: `${item.metricName} samples over the run`,
            columns: ["Seconds since start", "Value"],
            rows: item.points.map((point) => ({
              key: point.key,
              cells: [formatValue(point.t, "s"), formatValue(point.value, item.unit)],
            })),
          }}
        >
          <TimeSeriesChart
            ariaLabel={`${metricTitle(item.metricName)} over the run, ${item.unit}`}
            points={item.points}
            unit={item.unit}
            yLabel={item.unit === "% of one logical CPU" ? "% of 1 CPU" : item.unit}
            zeroBased
          />
        </ChartFigure>
      ))}
    </div>
  );
}
