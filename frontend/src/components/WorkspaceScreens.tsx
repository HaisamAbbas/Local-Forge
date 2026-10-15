import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { IconApi, IconTerminal2 } from "@tabler/icons-react";

import {
  ApiError,
  createEngineProfile,
  deleteEngineProfile,
  enableEngineProfile,
  fetchSystemProfile,
  listBenchmarkPlans,
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  listRunMetrics,
  listRuns,
  probeEngineProfile,
  saveCurrentMachineProfile,
  startEngineProfile,
  stopEngineProfile,
  updateEngineProfile,
  type AcceleratorDetails,
  type AdapterManifest,
  type AdapterModel,
  type BenchmarkRun,
  type CapabilityValue,
  type EngineModel,
  type EngineMode,
  type EngineProfile,
  type EngineProfileInput,
  type HardwareDetails,
  type MachineProfile,
  type DiscoveredEngine,
  type EngineRecommendation,
  type ProbeResult,
} from "../lib/api";
import { probeOutcome, probeProblem } from "../lib/engine-onboarding";
import {
  scanStatusMessage,
  useEngineDiscovery,
  type CandidateAddState,
} from "../lib/engine-discovery";
import { EngineTuner } from "./EngineTuner";
import { EngineDiscoveryPanel, EngineSetupChecklist } from "./EngineOnboarding";
import { EngineRecommendations } from "./EngineRecommendations";
import { recommendationProfileLabel, type AddOutcome } from "../lib/engine-recommendations";
import { formatValue, integerTicks, linearScale } from "../lib/chart-scale";
import { findRunMetric } from "../lib/run-measurements";
import { useChartCursor, useMeasuredWidth } from "./charts/chart-hooks";
import {
  BottomAxis,
  ChartFigure,
  ChartTooltip,
  CursorAnnouncement,
  LeftAxisLabel,
  ValueGrid,
  type PlotBox,
} from "./charts/ChartParts";

type Resource<T> =
  { status: "loading" } | { status: "error"; message: string } | { status: "ready"; value: T };

const DIALOG_FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function AccessibleDialog({
  titleId,
  className,
  children,
}: {
  titleId: string;
  className: string;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const initialFocus =
      dialog?.querySelector<HTMLElement>("[data-initial-focus]") ??
      dialog?.querySelector<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR);
    initialFocus?.focus();
    return () => {
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const dialog = event.currentTarget;
    if (event.key === "Escape") {
      event.preventDefault();
      dialog.querySelector<HTMLButtonElement>('[aria-label="Close dialog"]')?.click();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR),
    ).filter(
      (element) =>
        !element.closest("details:not([open])") &&
        !element.closest("[hidden]") &&
        element.getAttribute("aria-hidden") !== "true",
    );
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    const activeElement = document.activeElement;
    if (event.shiftKey && (activeElement === first || !dialog.contains(activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (activeElement === last || !dialog.contains(activeElement))) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <section
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-modal="true"
      className={className}
      onKeyDown={onKeyDown}
      role="dialog"
    >
      {children}
    </section>
  );
}

function useResource<T>(load: () => Promise<T>) {
  const [resource, setResource] = useState<Resource<T>>({ status: "loading" });
  const reload = useCallback(async () => {
    setResource({ status: "loading" });
    try {
      setResource({ status: "ready", value: await load() });
    } catch (error) {
      setResource({
        status: "error",
        message: error instanceof Error ? error.message : "The local API request failed.",
      });
    }
  }, [load]);
  useEffect(() => {
    let active = true;
    load().then(
      (value) => {
        if (active) setResource({ status: "ready", value });
      },
      (error: unknown) => {
        if (active) {
          setResource({
            status: "error",
            message: error instanceof Error ? error.message : "The local API request failed.",
          });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [load]);
  return { resource, reload };
}

function ResourcePanel<T>({
  resource,
  onRetry,
  loadingLabel,
  children,
}: {
  resource: Resource<T>;
  onRetry: () => void;
  loadingLabel: string;
  children: (value: T) => ReactNode;
}) {
  if (resource.status === "loading") {
    return (
      <div className="resource-skeleton">
        <span className="visually-hidden" role="status" aria-busy="true">
          {loadingLabel}
        </span>
        <span aria-hidden="true" />
        <span aria-hidden="true" />
      </div>
    );
  }
  if (resource.status === "error") {
    return (
      <div className="state-message state-message--error" role="alert">
        <span>{resource.message}</span>
        <button className="button button--quiet" onClick={onRetry} type="button">
          Retry
        </button>
      </div>
    );
  }
  return <>{children(resource.value)}</>;
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

type ManifestConfigProperty = {
  type?: string | string[];
  format?: string;
  title?: string;
  description?: string;
  default?: unknown;
  required?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  properties?: unknown;
};

type AdapterConfigField = {
  namespace: string;
  name: string;
  property: ManifestConfigProperty;
  required: boolean;
};

function schemaProperties(value: unknown): Record<string, ManifestConfigProperty> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).filter(
      (entry): entry is [string, ManifestConfigProperty] =>
        Boolean(entry[1]) && typeof entry[1] === "object" && !Array.isArray(entry[1]),
    ),
  );
}

function requiredSchemaFields(value: unknown): Set<string> {
  return new Set(
    Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [],
  );
}

function adapterConfigFields(manifest: AdapterManifest): AdapterConfigField[] {
  const rootProperties = schemaProperties(manifest.config_schema.properties);
  const defaultNamespace = adapterExtensionNamespace(manifest);
  if (manifest.category === "library_pipeline") {
    const required = requiredSchemaFields(manifest.config_schema.required);
    return Object.entries(rootProperties).map(([name, property]) => ({
      namespace: defaultNamespace,
      name,
      property,
      required: required.has(name),
    }));
  }

  const extensionProperties = schemaProperties(rootProperties.engine_extensions?.properties);
  const fields: AdapterConfigField[] = [];
  for (const [name, value] of Object.entries(extensionProperties)) {
    const nestedProperties = schemaProperties(value.properties);
    if (Object.keys(nestedProperties).length > 0) {
      const required = requiredSchemaFields(value.required);
      fields.push(
        ...Object.entries(nestedProperties).map(([fieldName, property]) => ({
          namespace: name,
          name: fieldName,
          property,
          required: required.has(fieldName),
        })),
      );
    } else {
      fields.push({
        namespace: defaultNamespace,
        name,
        property: value,
        required: false,
      });
    }
  }
  return fields;
}

function adapterExtensionNamespace(manifest: AdapterManifest): string {
  return manifest.adapter_id.split(".").slice(1).join(".");
}

function integrationModeLabel(mode: EngineMode): string {
  switch (mode) {
    case "attached_endpoint":
      return "Attached endpoint (local or remote)";
    case "managed_process":
      return "Managed local process";
    case "library_pipeline":
      return "Isolated local library pipeline";
    case "composed_serving_stack":
      return "Composed serving stack";
  }
}

function operatingSystemLabel(platform: string): string {
  return platform === "macos" ? "macOS" : titleCase(platform);
}

function profileConnectionLabel(profile: EngineProfile): string {
  if (profile.integration_mode === "managed_process") return "Managed local process";
  if (profile.integration_mode === "library_pipeline") return "Local library pipeline";
  if (profile.integration_mode === "composed_serving_stack") {
    return profile.is_remote ? "Remote serving stack" : "Local serving stack";
  }
  if (!profile.endpoint_url) return "Endpoint not configured";
  return profile.is_remote ? "Remote endpoint" : "Local endpoint";
}

function versionCoverageLabel(manifest: AdapterManifest): string {
  if (manifest.implementation_status !== "implemented") return "No fixture coverage (planned)";
  if (manifest.version_tested.length === 0) return "No fixture version recorded";
  const fixtureVersions = manifest.version_tested.filter((value) =>
    /local api fixture/i.test(value),
  );
  const releaseVersions = manifest.version_tested.filter(
    (value) => !/local api fixture/i.test(value),
  );
  return [
    fixtureVersions.length ? `Local fixture: ${fixtureVersions.join(" · ")}` : "",
    releaseVersions.length ? `Recorded version: ${releaseVersions.join(" · ")}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function statusLabel(value: string): string {
  return titleCase(value);
}

function capabilityLabel(value: CapabilityValue | undefined): string {
  return value ? titleCase(value.state) : "Unknown";
}

function MachineFacts({ machine }: { machine: MachineProfile }) {
  const hardware = machine.hardware;
  const facts: Array<[string, string | number | null | undefined]> = [
    ["Operating system", hardware?.os_name],
    ["OS version", hardware?.os_version],
    ["Architecture", hardware?.architecture],
    ["Processor", hardware?.cpu_model],
    ["Logical cores", hardware?.cpu_logical_cores],
    ["Physical cores", (hardware as HardwareDetails | null | undefined)?.cpu_physical_cores],
    [
      "Memory",
      hardware?.memory_total_bytes == null ? null : formatBytes(hardware.memory_total_bytes),
    ],
  ];
  return (
    <div className="facts-grid">
      {facts.map(([label, rawValue]) => {
        const value =
          rawValue === null || rawValue === undefined || rawValue === ""
            ? "Unknown / not detected"
            : String(rawValue);
        const provenance = machine.provenance[labelToField(label)];
        return (
          <div className="fact-card" key={label}>
            <span className="fact-label">{label}</span>
            <strong>{value}</strong>
            <small>
              {provenance
                ? `${titleCase(provenance.source)}${provenance.observed_at ? ` · ${formatDate(provenance.observed_at)}` : ""}`
                : "Source unknown · unverified"}
            </small>
          </div>
        );
      })}
      <div className="fact-card fact-card--wide">
        <span className="fact-label">Accelerators</span>
        {hardware?.accelerators === null || hardware?.accelerators === undefined ? (
          <strong>Unknown / not detected</strong>
        ) : hardware.accelerators.length === 0 ? (
          <strong>No accelerators reported</strong>
        ) : (
          <ul className="accelerator-list">
            {hardware.accelerators.map((accelerator: AcceleratorDetails, index) => (
              <li key={`${accelerator.name}-${index}`}>
                <strong>{accelerator.name}</strong>
                <span>
                  {[
                    accelerator.vendor,
                    accelerator.integrated === true
                      ? "Integrated"
                      : accelerator.integrated === false
                        ? "Discrete"
                        : null,
                    accelerator.runtime,
                    accelerator.driver_version,
                    accelerator.memory_bytes === null
                      ? accelerator.memory_kind === "shared"
                        ? "Shared system memory"
                        : null
                      : `${formatBytes(accelerator.memory_bytes)}${accelerator.memory_kind === "dedicated" ? " dedicated" : ""}`,
                    accelerator.detected_by ? `via ${accelerator.detected_by}` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "Details unknown"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <small>
          {machine.provenance.accelerators
            ? `${titleCase(machine.provenance.accelerators.source)} · ${formatDate(machine.provenance.accelerators.observed_at)}`
            : "Source unknown · unverified"}
        </small>
      </div>
    </div>
  );
}

function labelToField(label: string): string {
  return (
    (
      {
        "Operating system": "os_name",
        "OS version": "os_version",
        Architecture: "architecture",
        Processor: "cpu_model",
        "Logical cores": "cpu_logical_cores",
        "Physical cores": "cpu_physical_cores",
        Memory: "memory_total_bytes",
      } as Record<string, string>
    )[label] ?? ""
  );
}

const TREND_RUN_LIMIT = 20;
const TREND_FETCH_CONCURRENCY = 4;
const TREND_BOX: PlotBox = { left: 58, right: 18, top: 16, bottom: 44 };

type TrendRun = {
  runId: string;
  position: number;
  planName: string;
  completedAt: string | null;
  ttft: { value: number; unit: string } | null;
  throughput: { value: number; unit: string } | null;
  metricsError: boolean;
};

type TrendState = { status: "loading" } | { status: "ready"; runs: TrendRun[] };

/** Run `task` over `items` with at most `limit` requests in flight; failures stay per item. */
async function settleWithLimit<T, R>(
  items: T[],
  limit: number,
  task: (item: T) => Promise<R>,
): Promise<Array<PromiseSettledResult<R>>> {
  const results: Array<PromiseSettledResult<R>> = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      try {
        results[index] = { status: "fulfilled", value: await task(items[index]!) };
      } catch (reason) {
        results[index] = { status: "rejected", reason };
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

function completedTime(run: BenchmarkRun): number {
  const time = new Date(run.completed_at ?? run.updated_at).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function measuredValue(
  record: { value: number | null; unit: string } | null,
  fallbackUnit: string,
): { value: number; unit: string } | null {
  if (!record || record.value === null || !Number.isFinite(record.value)) return null;
  return { value: record.value, unit: record.unit || fallbackUnit };
}

/** The last completed runs (oldest first) with their TTFT p50 and generation throughput. */
function useRunTrend(runs: BenchmarkRun[] | null): TrendState {
  const recent = useMemo(
    () =>
      (runs ?? [])
        .filter((run) => run.status === "completed")
        .sort((left, right) => completedTime(right) - completedTime(left))
        .slice(0, TREND_RUN_LIMIT)
        .reverse(),
    [runs],
  );
  const [loaded, setLoaded] = useState<{ source: BenchmarkRun[]; runs: TrendRun[] } | null>(null);
  useEffect(() => {
    if (recent.length === 0) return;
    let active = true;
    void (async () => {
      const [plans, metrics] = await Promise.all([
        listBenchmarkPlans().catch(() => []),
        settleWithLimit(recent, TREND_FETCH_CONCURRENCY, (run) => listRunMetrics(run.id)),
      ]);
      if (!active) return;
      const planNames = new Map(plans.map((plan) => [plan.id, plan.name]));
      setLoaded({
        source: recent,
        runs: recent.map((run, index) => {
          const result = metrics[index];
          const records = result?.status === "fulfilled" ? result.value.metrics : [];
          return {
            runId: run.id,
            position: index + 1,
            planName: planNames.get(run.plan_id) ?? `Plan ${run.plan_id.slice(0, 8)}`,
            completedAt: run.completed_at,
            ttft: measuredValue(findRunMetric(records, "ttft_ms", "p50"), "ms"),
            throughput: measuredValue(
              findRunMetric(records, "generation_throughput_tokens_per_second", "run_rate"),
              "tokens/s",
            ),
            metricsError: result?.status !== "fulfilled",
          };
        }),
      });
    })();
    return () => {
      active = false;
    };
  }, [recent]);
  if (!runs) return { status: "loading" };
  if (recent.length === 0) return { status: "ready", runs: [] };
  return loaded?.source === recent ? { status: "ready", runs: loaded.runs } : { status: "loading" };
}

function runHref(runId: string): string {
  return `#runs?run=${encodeURIComponent(runId)}`;
}

/**
 * One metric across recent completed runs. X is the run's position in completion order
 * (shared by both trend panels so they line up); runs without the metric leave a gap.
 */
function RunTrendPlot({
  runs,
  pick,
  label,
  yLabel,
  height = 170,
}: {
  runs: TrendRun[];
  pick: (run: TrendRun) => { value: number; unit: string } | null;
  label: string;
  yLabel: string;
  height?: number;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(420);
  const width = Math.max(280, measured);
  const total = runs.length;
  const points = runs.flatMap((run) => {
    const value = pick(run);
    return value ? [{ run, value: value.value, unit: value.unit }] : [];
  });
  const plotLeft = TREND_BOX.left;
  const plotRight = width - TREND_BOX.right;
  const xMap = (position: number) =>
    total <= 1
      ? (plotLeft + plotRight) / 2
      : plotLeft + ((position - 1) / (total - 1)) * (plotRight - plotLeft);
  const y = linearScale(
    points.map((point) => point.value),
    [height - TREND_BOX.bottom, TREND_BOX.top],
    { zeroBased: true, tickCount: 3 },
  );
  const xs = points.map((point) => xMap(point.run.position));
  const { active, svgProps } = useChartCursor(xs, "x", width);
  const activePoint = active === null ? null : points[active];
  if (points.length === 0) {
    return (
      <p className="chart-no-data" role="status">
        None of these runs recorded {label}.
      </p>
    );
  }
  const readout = activePoint
    ? [
        activePoint.run.planName,
        `${label}: ${formatValue(activePoint.value, activePoint.unit)}`,
        `Run ${activePoint.run.runId.slice(0, 8)} · ${formatDate(activePoint.run.completedAt)}`,
      ]
    : null;
  const linePath = points
    .map(
      (point, index) =>
        `${index === 0 ? "M" : "L"}${xMap(point.run.position)},${y.map(point.value)}`,
    )
    .join(" ");
  const openActive = () => {
    if (activePoint) window.location.assign(runHref(activePoint.run.runId));
  };
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${label} for ${points.length} recent completed runs. Use arrow keys to read values and Enter to open a run.`}
        className="lf-chart__svg overview-trend__svg"
        height={height}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        {...svgProps}
        onClick={openActive}
        onKeyDown={(event) => {
          if (event.key === "Enter" && activePoint) {
            event.preventDefault();
            openActive();
            return;
          }
          svgProps.onKeyDown(event);
        }}
      >
        <ValueGrid ticks={y.ticks} map={y.map} box={TREND_BOX} width={width} />
        <BottomAxis
          box={TREND_BOX}
          height={height}
          label="Completed runs, oldest to newest"
          map={xMap}
          ticks={integerTicks(1, total, 6)}
          width={width}
          format={(value) => `#${value}`}
        />
        <LeftAxisLabel box={TREND_BOX} height={height} label={yLabel} />
        {points.length > 1 && <path className="lf-chart__line" d={linePath} />}
        {activePoint && (
          <line
            className="lf-chart__crosshair"
            x1={xMap(activePoint.run.position)}
            x2={xMap(activePoint.run.position)}
            y1={TREND_BOX.top}
            y2={height - TREND_BOX.bottom}
          />
        )}
        {points.map((point, index) => (
          <circle
            className={index === active ? "lf-chart__dot is-active" : "lf-chart__dot"}
            cx={xMap(point.run.position)}
            cy={y.map(point.value)}
            key={point.run.runId}
            r={index === active ? 5 : 3.5}
          />
        ))}
        {activePoint && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={xMap(activePoint.run.position)}
            y={y.map(activePoint.value)}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}

function skippedSummary(
  runs: TrendRun[],
  label: string,
  pick: (run: TrendRun) => unknown,
): string | null {
  const skipped = runs.filter((run) => !run.metricsError && !pick(run));
  if (skipped.length === 0) return null;
  const names = skipped.map((run) => run.planName).join(", ");
  return `${skipped.length} ${skipped.length === 1 ? "run" : "runs"} skipped for ${label} (not recorded): ${names}.`;
}

const pickTtft = (run: TrendRun) => run.ttft;
const pickThroughput = (run: TrendRun) => run.throughput;

function trendCell(run: TrendRun, value: { value: number; unit: string } | null): string {
  if (run.metricsError) return "Could not load";
  return value ? formatValue(value.value, value.unit) : "Not recorded";
}

function RunTrend({ state }: { state: TrendState }) {
  if (state.status === "loading") {
    return (
      <p role="status" aria-busy="true">
        Loading recent run measurements…
      </p>
    );
  }
  const runs = state.runs;
  const failed = runs.filter((run) => run.metricsError).length;
  const notes = [
    skippedSummary(runs, "TTFT p50", pickTtft),
    skippedSummary(runs, "generation throughput", pickThroughput),
    failed
      ? `Measurements for ${failed} ${failed === 1 ? "run" : "runs"} could not be loaded and are not shown.`
      : null,
  ].filter((note): note is string => Boolean(note));
  return (
    <ChartFigure
      id="overview-trend"
      title="Throughput and latency trend"
      headingLevel={3}
      subtitle={`Last ${runs.length} completed ${runs.length === 1 ? "run" : "runs"}. Hover or use arrow keys to see each plan; click or press Enter to open the run.`}
      empty={
        runs.length === 0 ? "No completed runs yet. Completed runs will be charted here." : null
      }
      caption={
        <>
          TTFT p50 is the median time to first token in each run; generation throughput is the
          run-rate decode throughput at the lowest recorded load level.
          {notes.map((note) => (
            <span className="overview-trend__note" key={note}>
              {" "}
              {note}
            </span>
          ))}
        </>
      }
      table={{
        caption: "Recent completed runs, oldest first",
        columns: ["Run", "Completed", "TTFT p50", "Generation throughput"],
        rows: runs.map((run) => ({
          key: run.runId,
          cells: [
            <a href={runHref(run.runId)} key="link">
              {run.planName} · {run.runId.slice(0, 8)}
            </a>,
            formatDate(run.completedAt),
            trendCell(run, run.ttft),
            trendCell(run, run.throughput),
          ],
        })),
      }}
    >
      <div className="lf-chart-grid lf-chart-grid--compact overview-trend">
        <div>
          <h4 className="lf-chart-group-title">Time to first token (p50)</h4>
          <RunTrendPlot label="TTFT p50" pick={pickTtft} runs={runs} yLabel="ms" />
        </div>
        <div>
          <h4 className="lf-chart-group-title">Generation throughput</h4>
          <RunTrendPlot
            label="generation throughput"
            pick={pickThroughput}
            runs={runs}
            yLabel="tokens/s"
          />
        </div>
      </div>
    </ChartFigure>
  );
}

export function OverviewScreen() {
  const machines = useResource(fetchSystemProfile);
  const runs = useResource(listRuns);
  const engines = useResource(listEngineProfiles);
  const machine = machines.resource.status === "ready" ? machines.resource.value : null;
  const latestRun = runs.resource.status === "ready" ? runs.resource.value[0] : null;
  const trend = useRunTrend(runs.resource.status === "ready" ? runs.resource.value : null);
  // Recommendations lead the page until the first engine profile exists. The placement is fixed
  // once profiles load, so adding an engine from the list does not move the list under the user.
  const [recommendationsPlacement, setRecommendationsPlacement] = useState<"top" | "lower" | null>(
    null,
  );
  if (recommendationsPlacement === null && engines.resource.status !== "loading") {
    setRecommendationsPlacement(
      engines.resource.status === "ready" && engines.resource.value.length === 0 ? "top" : "lower",
    );
  }
  const reloadEngines = engines.reload;
  return (
    <div className="screen-stack">
      {recommendationsPlacement === "top" && (
        <EngineRecommendations onAdded={() => void reloadEngines()} />
      )}
      <section className="welcome-panel overview-intro" aria-labelledby="welcome-title">
        <div className="welcome-heading">
          <div className="welcome-copy">
            <p className="eyebrow">LOCAL INFERENCE WORKSPACE</p>
            <h2 id="welcome-title">Benchmark the model running on your machine.</h2>
            <p>
              Connect an inference engine, choose a workload, and inspect measured results. Hardware
              and engine capabilities remain clearly labeled when unknown.
            </p>
          </div>
          <a className="button button--primary overview-action" href="#engines">
            Add an engine
          </a>
        </div>
        <ol className="overview-workflow" aria-label="Benchmark workflow">
          <li className="overview-workflow__step overview-workflow__step--current">
            <span className="overview-workflow__number" aria-hidden="true">
              01
            </span>
            <div>
              <a href="#engines">Connect an engine</a>
              <p>Choose the inference endpoint to evaluate.</p>
            </div>
          </li>
          <li className="overview-workflow__step">
            <span className="overview-workflow__number" aria-hidden="true">
              02
            </span>
            <div>
              <a href="#workloads">Prepare a workload</a>
              <p>Set up a repeatable request pattern.</p>
            </div>
          </li>
          <li className="overview-workflow__step">
            <span className="overview-workflow__number" aria-hidden="true">
              03
            </span>
            <div>
              <a href="#runs">Review measured results</a>
              <p>Compare latency and run outcomes.</p>
            </div>
          </li>
        </ol>
        <div className="overview-summary-grid">
          <section className="summary-card" aria-labelledby="machine-summary-title">
            <div className="section-title-row">
              <h3 id="machine-summary-title">This machine</h3>
              <a href="#system">View system</a>
            </div>
            <ResourcePanel
              resource={machines.resource}
              onRetry={() => void machines.reload()}
              loadingLabel="Reading machine details…"
            >
              {(value) => (
                <>
                  <p className="summary-primary">{value.host_name || value.label}</p>
                  <p className="summary-secondary">
                    {value.hardware?.cpu_model || "Processor unknown"}
                  </p>
                  <span className={`status-pill status-pill--${value.status}`}>
                    {statusLabel(value.status)}
                  </span>
                </>
              )}
            </ResourcePanel>
          </section>
          <section className="summary-card" aria-labelledby="engine-summary-title">
            <div className="section-title-row">
              <h3 id="engine-summary-title">Engine profiles</h3>
              <a href="#engines">Manage engines</a>
            </div>
            <ResourcePanel
              resource={engines.resource}
              onRetry={() => void engines.reload()}
              loadingLabel="Loading engine profiles…"
            >
              {(value) => (
                <>
                  <p className="summary-primary">{value.length}</p>
                  <p className="summary-secondary">
                    {value.filter((engine) => engine.status === "enabled").length} enabled ·{" "}
                    {value.filter((engine) => engine.status === "needs_attention").length} need
                    attention
                  </p>
                </>
              )}
            </ResourcePanel>
          </section>
          <section className="summary-card" aria-labelledby="latest-run-title">
            <div className="section-title-row">
              <h3 id="latest-run-title">Latest run</h3>
              <a href="#runs">Run history</a>
            </div>
            <ResourcePanel
              resource={runs.resource}
              onRetry={() => void runs.reload()}
              loadingLabel="Loading recent runs…"
            >
              {(value) =>
                value.length === 0 ? (
                  <>
                    <p className="summary-primary">No runs recorded</p>
                    <p className="summary-secondary">
                      Measured results will appear here after a run.
                    </p>
                  </>
                ) : (
                  <>
                    <p className="summary-primary">{statusLabel(latestRun?.status ?? "unknown")}</p>
                    <p className="summary-secondary">
                      Run {latestRun?.id.slice(0, 8)} · updated {formatDate(latestRun?.updated_at)}
                    </p>
                    {latestRun?.error_code && (
                      <p className="inline-warning">Last error: {latestRun.error_code}</p>
                    )}
                  </>
                )
              }
            </ResourcePanel>
          </section>
        </div>
      </section>
      {runs.resource.status === "ready" && runs.resource.value.length > 0 && (
        <section className="panel overview-trend-panel" aria-label="Recent run trend">
          <div className="section-title-row">
            <p className="eyebrow">RECENT RESULTS</p>
            <a className="text-link" href="#runs">
              Run history
            </a>
          </div>
          <RunTrend state={trend} />
        </section>
      )}
      {recommendationsPlacement === "lower" && (
        <EngineRecommendations compact onAdded={() => void reloadEngines()} />
      )}
      {machine && (
        <section className="panel" aria-labelledby="overview-machine-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">SYSTEM SNAPSHOT</p>
              <h2 id="overview-machine-title">Machine details</h2>
            </div>
            <a className="text-link" href="#system">
              All system details
            </a>
          </div>
          <MachineFacts machine={machine} />
        </section>
      )}
      {runs.resource.status === "ready" && runs.resource.value.length === 0 && (
        <section className="empty-state panel" aria-labelledby="empty-runs-title">
          <div>
            <h2 id="empty-runs-title">No benchmark runs yet</h2>
            <p>
              Connect and probe an engine, then configure a workload to create your first measured
              run.
            </p>
          </div>
          <a className="button button--primary" href="#engines">
            Set up an engine
          </a>
        </section>
      )}
    </div>
  );
}

export function SystemScreen() {
  const system = useResource(fetchSystemProfile);
  const profiles = useResource(listMachineProfiles);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const saveSnapshot = async () => {
    setSaveState("saving");
    setSaveError("");
    try {
      await saveCurrentMachineProfile();
      setSaveState("saved");
      await profiles.reload();
    } catch (error) {
      setSaveState("error");
      setSaveError(
        error instanceof Error ? error.message : "Could not save this machine snapshot.",
      );
    }
  };
  return (
    <div className="screen-stack">
      <section className="panel" aria-labelledby="system-details-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">LOCAL HOST · BEST EFFORT DETECTION</p>
            <h2 id="system-details-title">Current machine</h2>
          </div>
          <button
            className="button button--quiet"
            onClick={() => void system.reload()}
            type="button"
          >
            Refresh detection
          </button>
        </div>
        <ResourcePanel
          resource={system.resource}
          onRetry={() => void system.reload()}
          loadingLabel="Inspecting this machine…"
        >
          {(value) => (
            <>
              <div className="machine-status-row">
                <div>
                  <strong>{value.host_name || value.label}</strong>
                  <span className={`status-pill status-pill--${value.status}`}>
                    {statusLabel(value.status)}
                  </span>
                </div>
                <button
                  className="button button--primary"
                  disabled={saveState === "saving"}
                  onClick={() => void saveSnapshot()}
                  type="button"
                >
                  {saveState === "saving"
                    ? "Saving…"
                    : saveState === "saved"
                      ? "Snapshot saved"
                      : "Save machine snapshot"}
                </button>
              </div>
              <p className="helper-copy">
                Detection is best effort. Missing values are not inferred. Every detected value
                includes its source and observation time.
              </p>
              <MachineFacts machine={value} />
            </>
          )}
        </ResourcePanel>
        {saveState === "error" && (
          <p className="form-error" role="alert">
            {saveError}
          </p>
        )}
        {saveState === "saved" && (
          <p className="success-message" role="status">
            This detected snapshot is saved to the local machine profile list.
          </p>
        )}
      </section>
      <section className="panel" aria-labelledby="machine-profiles-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">KNOWN TARGETS</p>
            <h2 id="machine-profiles-title">Machine profiles</h2>
          </div>
          <button
            className="button button--quiet"
            onClick={() => void profiles.reload()}
            type="button"
          >
            Refresh list
          </button>
        </div>
        <ResourcePanel
          resource={profiles.resource}
          onRetry={() => void profiles.reload()}
          loadingLabel="Loading saved machine profiles…"
        >
          {(items) =>
            items.length === 0 ? (
              <p className="empty-inline">No machine profiles have been saved yet.</p>
            ) : (
              <div className="profile-list">
                {items.map((profile) => (
                  <article className="profile-row" key={profile.id}>
                    <div>
                      <h3>{profile.label}</h3>
                      <p>
                        {profile.host_name || "Host name unknown"} ·{" "}
                        {profile.hardware?.cpu_model || "Processor unknown"}
                      </p>
                    </div>
                    <span className={`status-pill status-pill--${profile.status}`}>
                      {profile.status === "manual_unverified"
                        ? "User supplied · unverified"
                        : statusLabel(profile.status)}
                    </span>
                  </article>
                ))}
              </div>
            )
          }
        </ResourcePanel>
      </section>
    </div>
  );
}

type EngineFormValues = {
  label: string;
  engine_display_name: string;
  integration_mode: EngineMode | "";
  endpoint_url: string;
  health_route: string;
  models_route: string;
  model_id: string;
  timeout_seconds: string;
  startup_timeout_seconds: string;
  executable_path: string;
  model_path: string;
  working_directory: string;
  context_size: string;
  parallelism: string;
  batch_size: string;
  ubatch_size: string;
  gpu_layers: string;
  max_retries: string;
  retry_backoff_seconds: string;
  streaming_mode: "auto" | "required" | "disabled";
  include_usage: boolean;
  metrics_route: string;
  credential_ref: string;
  clear_credential: boolean;
  user_notes: string;
};

const blankEngineForm: EngineFormValues = {
  label: "",
  engine_display_name: "",
  integration_mode: "",
  endpoint_url: "",
  health_route: "",
  models_route: "",
  model_id: "",
  timeout_seconds: "30",
  startup_timeout_seconds: "120",
  executable_path: "",
  model_path: "",
  working_directory: "",
  context_size: "",
  parallelism: "",
  batch_size: "",
  ubatch_size: "",
  gpu_layers: "",
  max_retries: "0",
  retry_backoff_seconds: "0.25",
  streaming_mode: "auto",
  include_usage: false,
  metrics_route: "",
  credential_ref: "",
  clear_credential: false,
  user_notes: "",
};

/** Where each engine listens when started with its documented defaults on this machine. */
const DEFAULT_LOCAL_ENDPOINTS: Record<string, string> = {
  "localforge.llama_cpp": "http://127.0.0.1:8080",
  "localforge.ollama": "http://127.0.0.1:11434",
  "localforge.openai_compatible": "http://127.0.0.1:8000/v1",
  "localforge.vllm": "http://127.0.0.1:8000",
  "localforge.sglang": "http://127.0.0.1:30000",
  "localforge.lmdeploy": "http://127.0.0.1:23333",
  "localforge.lightllm": "http://127.0.0.1:8000",
  "localforge.tensorrt_llm": "http://127.0.0.1:8000",
  "localforge.tgi": "http://127.0.0.1:8080",
  "localforge.localai": "http://127.0.0.1:8080",
  "localforge.koboldcpp": "http://127.0.0.1:5001",
  "localforge.mistral_rs": "http://127.0.0.1:1234",
  "localforge.mlc_llm": "http://127.0.0.1:8000",
  "localforge.mlx_lm": "http://127.0.0.1:8080",
  "localforge.nvidia_dynamo": "http://127.0.0.1:8000",
};

/** Port LOCALFORGE binds when it launches llama-server itself, clear of a running instance. */
const MANAGED_LLAMA_CPP_ENDPOINT = "http://127.0.0.1:9931";

function defaultEndpointFor(adapterId: string, mode: string): string {
  if (adapterId === "localforge.llama_cpp" && mode === "managed_process") {
    return MANAGED_LLAMA_CPP_ENDPOINT;
  }
  return DEFAULT_LOCAL_ENDPOINTS[adapterId] ?? "";
}

function EngineProfileDialog({
  manifest,
  initial,
  initialMode,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  manifest: AdapterManifest;
  initial?: EngineProfile;
  /** Connection mode preselected for a new profile, when the adapter supports it. */
  initialMode?: EngineMode | null;
  busy: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (input: EngineProfileInput) => void;
}) {
  const isOpenAiCompatible = manifest.adapter_id === "localforge.openai_compatible";
  const isLlamaCpp = manifest.adapter_id === "localforge.llama_cpp";
  const isOllama = manifest.adapter_id === "localforge.ollama";
  const isLocalAI = manifest.adapter_id === "localforge.localai";
  const isLibraryPipeline = manifest.category === "library_pipeline";
  const configFields = adapterConfigFields(manifest);
  const isSchemaDrivenAdapter =
    !isOpenAiCompatible && !isLlamaCpp && !isOllama && !isLocalAI && configFields.length > 0;
  const extensionNamespace = isLlamaCpp
    ? "llama_cpp"
    : isOllama
      ? "ollama"
      : isLocalAI
        ? "localai"
        : isOpenAiCompatible
          ? "openai_compatible"
          : (configFields[0]?.namespace ?? adapterExtensionNamespace(manifest));
  const savedExtension = initial?.engine_extensions?.[extensionNamespace] ?? {};
  const savedString = (key: string, fallback: string) => {
    const value = savedExtension[key];
    return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
  };
  const newProfileMode =
    initialMode && manifest.integration_modes.includes(initialMode)
      ? initialMode
      : (manifest.integration_modes[0] ?? "");
  const [values, setValues] = useState<EngineFormValues>(() =>
    initial
      ? {
          label: initial.label,
          engine_display_name: initial.engine_display_name ?? "",
          integration_mode: initial.integration_mode,
          endpoint_url: initial.endpoint_url ?? "",
          health_route: "",
          models_route: "",
          model_id: savedString("model_id", ""),
          timeout_seconds: savedString("timeout_seconds", "30"),
          startup_timeout_seconds: savedString("startup_timeout_seconds", "120"),
          executable_path: savedString("executable_path", ""),
          model_path: savedString("model_path", ""),
          working_directory: savedString("working_directory", ""),
          context_size: savedString("context_size", ""),
          parallelism: savedString("parallelism", ""),
          batch_size: savedString("batch_size", ""),
          ubatch_size: savedString("ubatch_size", ""),
          gpu_layers: savedString("gpu_layers", ""),
          max_retries: savedString("max_retries", "0"),
          retry_backoff_seconds: savedString("retry_backoff_seconds", "0.25"),
          streaming_mode:
            savedExtension.streaming_mode === "required" ||
            savedExtension.streaming_mode === "disabled"
              ? savedExtension.streaming_mode
              : "auto",
          include_usage: savedExtension.include_usage === true,
          metrics_route: savedString("metrics_route", ""),
          credential_ref: "",
          clear_credential: false,
          user_notes: initial.user_notes ?? "",
        }
      : {
          ...blankEngineForm,
          label: `${manifest.display_name} on this machine`.slice(0, 120),
          integration_mode: newProfileMode,
          endpoint_url: defaultEndpointFor(manifest.adapter_id, newProfileMode),
        },
  );
  const [adapterSettings, setAdapterSettings] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      configFields.map(({ namespace, name, property }) => {
        const saved = initial?.engine_extensions?.[namespace]?.[name];
        const value = saved ?? property.default;
        return [
          `${namespace}.${name}`,
          typeof value === "string" || typeof value === "number" || typeof value === "boolean"
            ? String(value)
            : property.type === "boolean"
              ? "false"
              : "",
        ];
      }),
    ),
  );
  const [formError, setFormError] = useState("");
  const setField = <K extends keyof EngineFormValues>(key: K, value: EngineFormValues[K]) =>
    setValues((current) => ({ ...current, [key]: value }));
  const remote = isRemoteEndpoint(values.endpoint_url);
  const showEndpointHint =
    values.endpoint_url !== "" &&
    values.endpoint_url === defaultEndpointFor(manifest.adapter_id, values.integration_mode);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!values.integration_mode) {
      setFormError("Choose an available connection mode.");
      return;
    }
    try {
      if (values.endpoint_url.trim()) {
        const endpoint = new URL(values.endpoint_url.trim());
        if (!["http:", "https:"].includes(endpoint.protocol) || !endpoint.hostname)
          throw new Error();
      }
    } catch {
      setFormError("Enter a valid HTTP or HTTPS endpoint URL.");
      return;
    }
    if (isOpenAiCompatible) {
      const timeout = Number(values.timeout_seconds);
      const retries = Number(values.max_retries);
      const backoff = Number(values.retry_backoff_seconds);
      if (!values.endpoint_url.trim()) {
        setFormError("Enter the OpenAI-compatible API base URL.");
        return;
      }
      if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
        setFormError("Timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (!Number.isInteger(retries) || retries < 0 || retries > 3) {
        setFormError("Connection retries must be an integer from 0 to 3.");
        return;
      }
      if (!Number.isFinite(backoff) || backoff < 0 || backoff > 2) {
        setFormError("Retry delay must be between 0 and 2 seconds.");
        return;
      }
      if (
        values.credential_ref.trim() &&
        !/^(keyring:\/\/|localforge-credential:\/\/)[^\s]+$/.test(values.credential_ref.trim())
      ) {
        setFormError(
          "Enter a keyring:// or localforge-credential:// reference, not a secret value.",
        );
        return;
      }
    }
    if (isLlamaCpp) {
      const timeout = Number(values.timeout_seconds);
      const startupTimeout = Number(values.startup_timeout_seconds);
      if (!values.endpoint_url.trim()) {
        setFormError("Enter the local llama-server endpoint URL.");
        return;
      }
      if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
        setFormError("Request timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (
        values.integration_mode === "managed_process" &&
        (!Number.isFinite(startupTimeout) || startupTimeout < 0.1 || startupTimeout > 300)
      ) {
        setFormError("Startup timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (values.integration_mode === "managed_process") {
        if (
          !values.executable_path.trim() ||
          !values.model_path.trim() ||
          !values.working_directory.trim()
        ) {
          setFormError(
            "Managed mode needs an executable, a GGUF model file, and a working directory.",
          );
          return;
        }
        if (!values.model_path.trim().toLowerCase().endsWith(".gguf")) {
          setFormError("Choose a local model file with the .gguf extension.");
          return;
        }
        for (const [label, value] of [
          ["Context size", values.context_size],
          ["Parallelism", values.parallelism],
          ["Batch size", values.batch_size],
          ["Physical batch size", values.ubatch_size],
        ] as const) {
          if (value.trim() && (!Number.isInteger(Number(value)) || Number(value) < 1)) {
            setFormError(`${label} must be a positive whole number.`);
            return;
          }
        }
        if (values.gpu_layers.trim()) {
          const normalizedGpuLayers = values.gpu_layers.trim().toLowerCase();
          const numericGpuLayers = Number(normalizedGpuLayers);
          if (
            normalizedGpuLayers !== "auto" &&
            normalizedGpuLayers !== "all" &&
            (!Number.isInteger(numericGpuLayers) ||
              numericGpuLayers < 0 ||
              numericGpuLayers > 100_000)
          ) {
            setFormError("GPU layers must be a whole number from 0 to 100000, auto, or all.");
            return;
          }
        }
        if (
          values.batch_size.trim() &&
          values.ubatch_size.trim() &&
          Number(values.ubatch_size) > Number(values.batch_size)
        ) {
          setFormError("Physical batch size cannot exceed logical batch size.");
          return;
        }
        try {
          const managedEndpoint = new URL(values.endpoint_url.trim());
          if (
            managedEndpoint.protocol !== "http:" ||
            managedEndpoint.hostname !== "127.0.0.1" ||
            managedEndpoint.search !== "" ||
            managedEndpoint.hash !== "" ||
            !["", "/", "/v1"].includes(managedEndpoint.pathname.replace(/\/$/, ""))
          ) {
            setFormError("Managed mode must use an HTTP 127.0.0.1 URL at the root.");
            return;
          }
        } catch {
          setFormError("Enter a valid local HTTP endpoint URL for managed mode.");
          return;
        }
      }
    }
    if (isOllama) {
      const timeout = Number(values.timeout_seconds);
      if (!values.endpoint_url.trim()) {
        setFormError("Enter the local Ollama server URL.");
        return;
      }
      if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
        setFormError("Request timeout must be between 0.1 and 300 seconds.");
        return;
      }
      try {
        const endpoint = new URL(values.endpoint_url.trim());
        const host = endpoint.hostname.replace(/^\[|\]$/g, "").toLowerCase();
        const isLocalHost =
          host === "localhost" ||
          host.endsWith(".localhost") ||
          host === "::1" ||
          /^127(?:\.\d{1,3}){3}$/.test(host);
        if (
          !isLocalHost ||
          !["", "/"].includes(endpoint.pathname) ||
          endpoint.search ||
          endpoint.hash
        ) {
          setFormError("Ollama must use a local server URL at the root, such as 127.0.0.1:11434.");
          return;
        }
      } catch {
        setFormError("Enter a valid local Ollama server URL.");
        return;
      }
    }
    const parsedAdapterSettings: Record<string, Record<string, unknown>> = {};
    if (isSchemaDrivenAdapter) {
      for (const { namespace, name, property, required } of configFields) {
        const raw = adapterSettings[`${namespace}.${name}`]?.trim() ?? "";
        if (!raw && required) {
          setFormError(`${property.title || titleCase(name)} is required.`);
          return;
        }
        if (!raw) continue;
        const propertyType = Array.isArray(property.type)
          ? property.type.find((item) => item !== "null")
          : property.type;
        let parsedValue: unknown = raw;
        if (propertyType === "boolean") {
          parsedValue = raw === "true";
        }
        if (propertyType === "number" || propertyType === "integer") {
          const numericValue = Number(raw);
          if (
            !Number.isFinite(numericValue) ||
            (propertyType === "integer" && !Number.isInteger(numericValue)) ||
            (property.minimum !== undefined && numericValue < property.minimum) ||
            (property.maximum !== undefined && numericValue > property.maximum)
          ) {
            setFormError(
              `${property.title || titleCase(name)} must be a ${
                propertyType === "integer" ? "whole number" : "number"
              }${property.minimum !== undefined ? ` from ${property.minimum}` : ""}${
                property.maximum !== undefined ? ` to ${property.maximum}` : ""
              }.`,
            );
            return;
          }
          parsedValue = numericValue;
        }
        if (property.enum?.length) {
          const enumValue = property.enum.find((option) => String(option) === raw);
          if (enumValue === undefined) {
            setFormError(`${property.title || titleCase(name)} has an unsupported value.`);
            return;
          }
          parsedValue = enumValue;
        }
        (parsedAdapterSettings[namespace] ??= {})[name] = parsedValue;
      }
    }
    setFormError("");
    const input: EngineProfileInput = {
      label: values.label.trim(),
      adapter_id: manifest.adapter_id,
      engine_display_name: values.engine_display_name.trim() || null,
      integration_mode: values.integration_mode,
      endpoint_url: values.endpoint_url.trim() || null,
      user_notes: values.user_notes.trim() || null,
    };
    if (!isLlamaCpp && !isOllama && !isLibraryPipeline && (!initial || values.health_route.trim()))
      input.health_route = values.health_route.trim() || null;
    if (!isLlamaCpp && !isOllama && !isLibraryPipeline && (!initial || values.models_route.trim()))
      input.models_route = values.models_route.trim() || null;
    if (isOpenAiCompatible) {
      const extension: Record<string, unknown> = { ...savedExtension };
      if (values.model_id.trim()) extension.model_id = values.model_id.trim();
      else delete extension.model_id;
      extension.timeout_seconds = Number(values.timeout_seconds);
      extension.max_retries = Number(values.max_retries);
      extension.retry_backoff_seconds = Number(values.retry_backoff_seconds);
      extension.streaming_mode = values.streaming_mode;
      extension.include_usage = values.include_usage;
      if (values.metrics_route.trim()) extension.metrics_route = values.metrics_route.trim();
      else delete extension.metrics_route;
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        openai_compatible: extension,
      };
      if (values.credential_ref.trim()) {
        input.secret_refs = { api_key: values.credential_ref.trim() };
      } else if (values.clear_credential) {
        input.secret_refs = {};
      }
    }
    if (isLlamaCpp) {
      const extension: Record<string, unknown> = {
        timeout_seconds: Number(values.timeout_seconds),
      };
      if (values.model_id.trim()) extension.model_id = values.model_id.trim();
      if (values.integration_mode === "managed_process") {
        extension.executable_path = values.executable_path.trim();
        extension.model_path = values.model_path.trim();
        extension.working_directory = values.working_directory.trim();
        extension.startup_timeout_seconds = Number(values.startup_timeout_seconds);
        for (const key of ["context_size", "parallelism", "batch_size", "ubatch_size"] as const) {
          const value = values[key].trim();
          if (value) extension[key] = Number(value);
        }
        const gpuLayers = values.gpu_layers.trim();
        if (gpuLayers) {
          const normalizedGpuLayers = gpuLayers.toLowerCase();
          extension.gpu_layers = ["auto", "all"].includes(normalizedGpuLayers)
            ? normalizedGpuLayers
            : Number(normalizedGpuLayers);
        }
      }
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        llama_cpp: extension,
      };
    }
    if (isOllama) {
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        ollama: {
          timeout_seconds: Number(values.timeout_seconds),
          streaming_mode: values.streaming_mode,
        },
      };
    }
    if (isLocalAI) {
      const extension: Record<string, unknown> = { ...savedExtension, discover_backend: true };
      if (values.model_id.trim()) extension.model_id = values.model_id.trim();
      else delete extension.model_id;
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        localai: extension,
      };
    }
    if (isSchemaDrivenAdapter) {
      const extensions: Record<string, Record<string, unknown>> = {
        ...(initial?.engine_extensions ?? {}),
      };
      for (const { namespace, name } of configFields) {
        const namespaceSettings = { ...(extensions[namespace] ?? {}) };
        const parsedSettings = parsedAdapterSettings[namespace];
        if (parsedSettings && Object.hasOwn(parsedSettings, name)) {
          namespaceSettings[name] = parsedSettings[name];
        } else {
          delete namespaceSettings[name];
        }
        extensions[namespace] = namespaceSettings;
      }
      input.engine_extensions = {
        ...extensions,
      };
    }
    onSubmit(input);
  };

  return (
    <div className="dialog-scrim">
      <AccessibleDialog className="dialog-card" titleId="engine-dialog-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">{initial ? "EDIT PROFILE" : "NEW PROFILE"}</p>
            <h2 id="engine-dialog-title">{initial ? "Edit engine profile" : "Add an engine"}</h2>
          </div>
          <button aria-label="Close dialog" className="icon-button" onClick={onClose} type="button">
            ×
          </button>
        </div>
        <p className="helper-copy">
          {manifest.display_name}: {manifest.description}
        </p>
        {manifest.category === "test_fixture" && (
          <p className="notice notice--warning">
            This adapter is a deterministic test fixture. Its probe and timings do not measure
            language-model inference performance.
          </p>
        )}
        <form className="engine-form" onSubmit={submit}>
          <label>
            Profile name
            <input
              autoFocus
              data-initial-focus
              maxLength={120}
              onChange={(event) => setField("label", event.target.value)}
              required
              value={values.label}
            />
          </label>
          <label>
            Adapter
            <select disabled value={manifest.adapter_id}>
              <option value={manifest.adapter_id}>{manifest.display_name}</option>
            </select>
          </label>
          <label>
            Connection mode
            <select
              onChange={(event) => {
                const mode = event.target.value as EngineMode;
                setValues((current) => ({
                  ...current,
                  integration_mode: mode,
                  // Follow the mode's usual address unless the user typed their own.
                  endpoint_url:
                    current.endpoint_url.trim() === "" ||
                    current.endpoint_url ===
                      defaultEndpointFor(manifest.adapter_id, current.integration_mode)
                      ? defaultEndpointFor(manifest.adapter_id, mode)
                      : current.endpoint_url,
                }));
              }}
              required
              value={values.integration_mode}
            >
              <option value="">Choose a mode</option>
              {manifest.integration_modes.map((mode) => (
                <option key={mode} value={mode}>
                  {integrationModeLabel(mode)}
                </option>
              ))}
            </select>
          </label>
          {!isLibraryPipeline && (
            <>
              <label>
                {isOpenAiCompatible
                  ? "API base URL"
                  : isOllama
                    ? "Ollama local server URL"
                    : isLlamaCpp && values.integration_mode === "managed_process"
                      ? "Local bind URL"
                      : isLlamaCpp
                        ? "llama-server endpoint URL"
                        : "Endpoint URL"}{" "}
                {!isOpenAiCompatible && !isLlamaCpp && !isOllama && (
                  <span className="optional-label">Optional</span>
                )}
                <input
                  aria-describedby={showEndpointHint ? "engine-endpoint-hint" : undefined}
                  autoComplete="url"
                  maxLength={1000}
                  onChange={(event) => setField("endpoint_url", event.target.value)}
                  placeholder="http://127.0.0.1:8080"
                  required={isOpenAiCompatible || isLlamaCpp || isOllama}
                  type="url"
                  value={values.endpoint_url}
                />
              </label>
              {showEndpointHint && (
                <small className="table-subtext" id="engine-endpoint-hint">
                  {isLlamaCpp && values.integration_mode === "managed_process"
                    ? "LOCALFORGE starts llama-server on this address. Change the port if it is in use."
                    : `The usual address when ${manifest.display_name} runs on this machine. Change it if you started the server on another port or host.`}
                </small>
              )}
            </>
          )}
          <label>
            Engine display name <span className="optional-label">Optional</span>
            <input
              maxLength={160}
              onChange={(event) => setField("engine_display_name", event.target.value)}
              value={values.engine_display_name}
            />
          </label>
          {isLlamaCpp && (
            <>
              <p className="notice">
                GGUF model files stay on this machine. LocalForge never downloads or uploads them.
                GPU acceleration depends on the backend compiled into your selected llama-server
                binary.
              </p>
              <label>
                Model ID{" "}
                <span className="optional-label">Optional if the server lists a model</span>
                <input
                  maxLength={500}
                  onChange={(event) => setField("model_id", event.target.value)}
                  placeholder="Model ID reported by llama-server"
                  value={values.model_id}
                />
              </label>
              <label>
                Request timeout (seconds)
                <input
                  max={300}
                  min={0.1}
                  onChange={(event) => setField("timeout_seconds", event.target.value)}
                  step="any"
                  type="number"
                  value={values.timeout_seconds}
                />
              </label>
              {values.integration_mode === "managed_process" && (
                <details className="advanced-fields" open>
                  <summary>Local launch settings</summary>
                  <p className="helper-copy">
                    Use absolute paths. LocalForge starts only this selected executable and stops
                    only the process it launched. GGUF files must already be present.
                  </p>
                  <label>
                    llama-server executable path
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("executable_path", event.target.value)}
                      placeholder="C:\\llama.cpp\\llama-server.exe"
                      required
                      value={values.executable_path}
                    />
                  </label>
                  <label>
                    Existing GGUF model path
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("model_path", event.target.value)}
                      placeholder="C:\\Models\\model.gguf"
                      required
                      value={values.model_path}
                    />
                  </label>
                  <label>
                    Working directory
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("working_directory", event.target.value)}
                      placeholder="C:\\llama.cpp\\build\\bin"
                      required
                      value={values.working_directory}
                    />
                  </label>
                  <label>
                    Startup timeout (seconds)
                    <input
                      max={300}
                      min={0.1}
                      onChange={(event) => setField("startup_timeout_seconds", event.target.value)}
                      step="any"
                      type="number"
                      value={values.startup_timeout_seconds}
                    />
                  </label>
                  <details className="advanced-fields">
                    <summary>Optional server tuning</summary>
                    <p className="helper-copy">
                      Settings are passed to llama-server as documented command-line arguments.
                      Hardware compatibility is determined by your binary and model.
                    </p>
                    {(
                      [
                        ["context_size", "Context size", "4096"],
                        ["parallelism", "Parallel requests", "1"],
                        ["batch_size", "Batch size", "2048"],
                        ["ubatch_size", "Physical batch size", "512"],
                      ] as const
                    ).map(([key, label, placeholder]) => (
                      <label key={key}>
                        {label} <span className="optional-label">Optional</span>
                        <input
                          min={1}
                          onChange={(event) => setField(key, event.target.value)}
                          placeholder={placeholder}
                          step={1}
                          type="number"
                          value={values[key]}
                        />
                      </label>
                    ))}
                    <label>
                      GPU layers <span className="optional-label">Optional</span>
                      <input
                        maxLength={16}
                        onChange={(event) => setField("gpu_layers", event.target.value)}
                        placeholder="0, auto, or all"
                        value={values.gpu_layers}
                      />
                    </label>
                  </details>
                </details>
              )}
            </>
          )}
          {isOllama && (
            <>
              <p className="notice">
                LOCALFORGE attaches to Ollama already running on this machine. Select a model
                already installed in Ollama; LOCALFORGE never pulls or deletes models and never
                starts or stops the Ollama service. The underlying runner stays unknown unless
                Ollama reports verified details.
              </p>
              <label>
                Request timeout (seconds)
                <input
                  max={300}
                  min={0.1}
                  onChange={(event) => setField("timeout_seconds", event.target.value)}
                  step="any"
                  type="number"
                  value={values.timeout_seconds}
                />
              </label>
              <label>
                Streaming mode
                <select
                  onChange={(event) =>
                    setField(
                      "streaming_mode",
                      event.target.value as EngineFormValues["streaming_mode"],
                    )
                  }
                  value={values.streaming_mode}
                >
                  <option value="auto">Use streaming when supported</option>
                  <option value="required">Require streaming</option>
                  <option value="disabled">Use non-streaming responses</option>
                </select>
              </label>
            </>
          )}
          {isOpenAiCompatible && (
            <>
              <label>
                Model ID{" "}
                <span className="optional-label">Optional if the endpoint lists models</span>
                <input
                  maxLength={500}
                  onChange={(event) => setField("model_id", event.target.value)}
                  placeholder="model ID reported by your endpoint"
                  value={values.model_id}
                />
              </label>
              <label>
                Request timeout (seconds)
                <input
                  max={300}
                  min={0.1}
                  onChange={(event) => setField("timeout_seconds", event.target.value)}
                  step="any"
                  type="number"
                  value={values.timeout_seconds}
                />
              </label>
              <label>
                Streaming mode
                <select
                  onChange={(event) =>
                    setField(
                      "streaming_mode",
                      event.target.value as EngineFormValues["streaming_mode"],
                    )
                  }
                  value={values.streaming_mode}
                >
                  <option value="auto">Use streaming when supported</option>
                  <option value="required">Require streaming</option>
                  <option value="disabled">Use non-streaming responses</option>
                </select>
              </label>
              <details className="advanced-fields">
                <summary>Endpoint options</summary>
                <p className="helper-copy">
                  Retries apply only when the connection fails before an HTTP response. The generic
                  adapter never replays a request after output begins.
                </p>
                <label>
                  Connection retries <span className="optional-label">0–3, default 0</span>
                  <input
                    max={3}
                    min={0}
                    onChange={(event) => setField("max_retries", event.target.value)}
                    step={1}
                    type="number"
                    value={values.max_retries}
                  />
                </label>
                <label>
                  Retry delay (seconds)
                  <input
                    max={2}
                    min={0}
                    onChange={(event) => setField("retry_backoff_seconds", event.target.value)}
                    step="any"
                    type="number"
                    value={values.retry_backoff_seconds}
                  />
                </label>
                <label>
                  Metrics route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("metrics_route", event.target.value)}
                    placeholder="/metrics"
                    value={values.metrics_route}
                  />
                </label>
                <label className="workload-check">
                  <input
                    checked={values.include_usage}
                    onChange={(event) => setField("include_usage", event.target.checked)}
                    type="checkbox"
                  />
                  Request optional streaming usage counts when supported
                </label>
                <label>
                  API key credential reference <span className="optional-label">Optional</span>
                  <input
                    autoComplete="off"
                    maxLength={256}
                    onChange={(event) => setField("credential_ref", event.target.value)}
                    placeholder="keyring://LocalForge/account-name"
                    value={values.credential_ref}
                  />
                  <span className="helper-copy">
                    Enter a reference to an existing OS keyring entry. Never paste the key itself.
                  </span>
                </label>
                {initial?.credential_configured && (
                  <label className="workload-check">
                    <input
                      checked={values.clear_credential}
                      onChange={(event) => setField("clear_credential", event.target.checked)}
                      type="checkbox"
                    />
                    Remove the saved credential reference
                  </label>
                )}
              </details>
            </>
          )}
          {isLocalAI && (
            <label>
              Model ID for backend discovery <span className="optional-label">Optional</span>
              <input
                maxLength={500}
                onChange={(event) => setField("model_id", event.target.value)}
                placeholder="Use the selected model if left blank"
                value={values.model_id}
              />
              <span className="helper-copy">
                The saved model configuration may require LocalAI administrator credentials. If it
                cannot be read, the backend remains unknown.
              </span>
            </label>
          )}
          {isSchemaDrivenAdapter && (
            <section className="adapter-config-fields" aria-labelledby="adapter-settings-title">
              <h3 id="adapter-settings-title">
                {isLibraryPipeline ? "Local pipeline settings" : "Adapter settings"}
              </h3>
              {isLibraryPipeline && (
                <p className="helper-copy">
                  Uses an existing model and installed runtime on this machine. LOCALFORGE does not
                  install packages or download model files.
                </p>
              )}
              {configFields.map(({ namespace, name, property, required }) => {
                const fieldId = `adapter-setting-${namespace}-${name}`;
                const value = adapterSettings[`${namespace}.${name}`] ?? "";
                const propertyType = Array.isArray(property.type)
                  ? property.type.find((item) => item !== "null")
                  : property.type;
                const title = property.title || titleCase(name);
                const enumValues = property.enum ?? [];
                const helper = property.description;
                if (propertyType === "boolean") {
                  return (
                    <label className="workload-check" htmlFor={fieldId} key={fieldId}>
                      <input
                        checked={value === "true"}
                        id={fieldId}
                        onChange={(event) =>
                          setAdapterSettings((current) => ({
                            ...current,
                            [`${namespace}.${name}`]: String(event.target.checked),
                          }))
                        }
                        type="checkbox"
                      />
                      {title}
                      {helper && <span className="helper-copy">{helper}</span>}
                    </label>
                  );
                }
                return (
                  <label htmlFor={fieldId} key={fieldId}>
                    {title} {!required && <span className="optional-label">Optional</span>}
                    {enumValues.length > 0 ? (
                      <select
                        id={fieldId}
                        onChange={(event) =>
                          setAdapterSettings((current) => ({
                            ...current,
                            [`${namespace}.${name}`]: event.target.value,
                          }))
                        }
                        required={required}
                        value={value}
                      >
                        {!required && <option value="">Use adapter default</option>}
                        {enumValues.map((option) => (
                          <option key={String(option)} value={String(option)}>
                            {String(option)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        autoComplete="off"
                        id={fieldId}
                        max={property.maximum}
                        maxLength={
                          propertyType === "number" || propertyType === "integer" ? undefined : 4096
                        }
                        min={property.minimum}
                        onChange={(event) =>
                          setAdapterSettings((current) => ({
                            ...current,
                            [`${namespace}.${name}`]: event.target.value,
                          }))
                        }
                        required={required}
                        step={
                          propertyType === "integer"
                            ? 1
                            : propertyType === "number"
                              ? "any"
                              : undefined
                        }
                        type={
                          propertyType === "number" || propertyType === "integer"
                            ? "number"
                            : "text"
                        }
                        value={value}
                      />
                    )}
                    {helper && <span className="helper-copy">{helper}</span>}
                  </label>
                );
              })}
            </section>
          )}
          <details className="advanced-fields">
            <summary>Connection details</summary>
            {!isLlamaCpp && !isOllama && !isLibraryPipeline && (
              <>
                <label>
                  Health route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("health_route", event.target.value)}
                    placeholder="/health"
                    value={values.health_route}
                  />
                </label>
                <label>
                  Models route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("models_route", event.target.value)}
                    placeholder="/v1/models"
                    value={values.models_route}
                  />
                </label>
              </>
            )}
            <label>
              Notes <span className="optional-label">Optional</span>
              <textarea
                maxLength={2000}
                onChange={(event) => setField("user_notes", event.target.value)}
                rows={3}
                value={values.user_notes}
              />
            </label>
          </details>
          {remote && manifest.category === "test_fixture" ? (
            <p className="notice notice--warning">
              This fixture never contacts its configured endpoint. It cannot verify this remote host
              or send it model requests. Use a network-capable adapter to probe a remote engine.
            </p>
          ) : remote ? (
            <p className="notice notice--warning">
              This URL points to a remote host. Before probing, LOCALFORGE will explain which
              requests leave this machine and ask you to confirm.
            </p>
          ) : null}
          {initial?.credential_configured && (
            <p className="notice">
              A credential is configured for this profile. Its value is stored outside this profile
              and is never displayed here.
            </p>
          )}
          {formError && (
            <p className="form-error" role="alert">
              {formError}
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button className="button button--quiet" onClick={onClose} type="button">
              Cancel
            </button>
            <button className="button button--primary" disabled={busy} type="submit">
              {busy ? "Saving…" : initial ? "Save changes" : "Add engine"}
            </button>
          </div>
        </form>
      </AccessibleDialog>
    </div>
  );
}

function isRemoteEndpoint(rawUrl: string): boolean {
  if (!rawUrl.trim()) return false;
  try {
    const host = new URL(rawUrl).hostname.toLowerCase();
    return !(
      ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host) || host.endsWith(".localhost")
    );
  } catch {
    return false;
  }
}

function CapabilityTable({ items }: { items: Record<string, CapabilityValue> }) {
  const rows = Object.entries(items);
  if (rows.length === 0)
    return (
      <p className="empty-inline">
        No capability values were reported. Each unreported capability remains unknown.
      </p>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">Result</th>
            <th scope="col">Source and details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, value]) => (
            <tr key={name}>
              <th scope="row">{titleCase(name)}</th>
              <td>
                <span className={`capability-state capability-state--${value.state}`}>
                  {titleCase(value.state)}
                </span>
              </td>
              <td>
                {[value.source, value.details].filter(Boolean).join(" · ") || "No details reported"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProbeDetails({
  result,
  models,
  modelsLoading,
  modelsError,
}: {
  result: ProbeResult;
  models?: EngineModel[];
  modelsLoading?: boolean;
  modelsError?: string;
}) {
  const metrics = Object.fromEntries(
    Object.entries(result.capabilities.capabilities).filter(
      ([key]) => key.includes("metric") || key.startsWith("server_"),
    ),
  );
  return (
    <div className="probe-details">
      <div className="probe-summary-grid">
        <div>
          <span>Probe status</span>
          <strong>{statusLabel(result.status)}</strong>
        </div>
        <div>
          <span>Health</span>
          <strong>{capabilityLabel(result.health)}</strong>
        </div>
        <div>
          <span>Engine version</span>
          <strong>{result.engine_version || "Unknown"}</strong>
        </div>
        <div>
          <span>Streaming</span>
          <strong>{capabilityLabel(result.checks.streaming)}</strong>
        </div>
        <div>
          <span>Model inventory</span>
          <strong>
            {capabilityLabel(
              result.checks.model_inventory ?? result.capabilities.capabilities.model_inventory,
            )}
          </strong>
        </div>
        <div>
          <span>Native metrics</span>
          <strong>
            {capabilityLabel(
              result.checks.native_metrics ?? result.capabilities.capabilities.native_metrics,
            )}
          </strong>
        </div>
      </div>
      <section aria-labelledby="probe-models-title">
        <h4 id="probe-models-title">Models reported by this probe</h4>
        {result.models.length === 0 ? (
          <p className="empty-inline">
            No models were reported. Check the model inventory state before treating this as an
            empty catalog.
          </p>
        ) : (
          <ModelTable models={result.models} />
        )}
      </section>
      <section aria-labelledby="probe-capabilities-title">
        <h4 id="probe-capabilities-title">Capabilities</h4>
        <CapabilityTable items={result.capabilities.capabilities} />
      </section>
      {Object.keys(metrics).length > 0 && (
        <section aria-labelledby="probe-metrics-title">
          <h4 id="probe-metrics-title">Metrics capability details</h4>
          <CapabilityTable items={metrics} />
        </section>
      )}
      <section aria-labelledby="probe-diagnostics-title">
        <h4 id="probe-diagnostics-title">Diagnostics</h4>
        {result.diagnostics.length === 0 ? (
          <p className="empty-inline">No diagnostics were returned by the adapter.</p>
        ) : (
          <ul className="diagnostic-list">
            {result.diagnostics.map((diagnostic, index) => (
              <li
                className={`diagnostic diagnostic--${diagnostic.severity}`}
                key={`${diagnostic.code}-${index}`}
              >
                <span>{titleCase(diagnostic.severity)}</span>
                <strong>{diagnostic.code}</strong>
                <p>{diagnostic.message}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="source-note">
        Probe recorded {formatDate(result.probed_at)} · adapter {result.adapter_id}{" "}
        {result.adapter_version}
      </p>
      {models && (
        <section aria-labelledby="saved-models-title">
          <h4 id="saved-models-title">Saved model inventory</h4>
          {models.length === 0 ? (
            <p className="empty-inline">The API has no saved model records for this profile.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col">Reference</th>
                    <th scope="col">State</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <tr key={model.id}>
                      <th scope="row">{model.display_name}</th>
                      <td>{model.model_ref || "Unknown"}</td>
                      <td>{titleCase(model.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {modelsLoading && <p role="status">Refreshing saved model inventory…</p>}
      {modelsError && (
        <p className="form-error" role="alert">
          {modelsError}
        </p>
      )}
    </div>
  );
}

function ModelTable({ models }: { models: AdapterModel[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Reference</th>
            <th scope="col">Revision</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => (
            <tr key={model.model_ref}>
              <th scope="row">{model.display_name}</th>
              <td>{model.model_ref}</td>
              <td>{model.revision || "Unknown"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EnginesScreen() {
  const catalog = useResource(listEngineCatalog);
  const profiles = useResource(listEngineProfiles);
  const [editing, setEditing] = useState<EngineProfile | null | "new">(null);
  const [selectedAdapterId, setSelectedAdapterId] = useState("");
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyProfile, setBusyProfile] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<"start" | "stop" | "probe" | "enable" | null>(null);
  const [probeResults, setProbeResults] = useState<Record<string, ProbeResult>>({});
  // Why the latest probe of a profile failed, shown on its card until a probe succeeds.
  const [probeProblems, setProbeProblems] = useState<Record<string, string>>({});
  const [setupMessage, setSetupMessage] = useState("");
  const discovery = useEngineDiscovery();
  const [addedByEndpoint, setAddedByEndpoint] = useState<Record<string, string>>({});
  const [candidateAddStates, setCandidateAddStates] = useState<Record<string, CandidateAddState>>(
    {},
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [modelData, setModelData] = useState<
    Record<
      string,
      { loading: boolean; models?: Awaited<ReturnType<typeof listEngineModels>>; error?: string }
    >
  >({});
  const [remotePrompt, setRemotePrompt] = useState<{
    id: string;
    host: string;
    message: string;
  } | null>(null);
  const [removePrompt, setRemovePrompt] = useState<EngineProfile | null>(null);
  // Mode preselected when the Add dialog opens from a recommendation ("Add as managed").
  const [newProfileMode, setNewProfileMode] = useState<EngineMode | null>(null);
  useEffect(() => {
    if (!editing) setNewProfileMode(null);
  }, [editing]);

  const manifestById = useMemo(
    () =>
      new Map(
        (catalog.resource.status === "ready" ? catalog.resource.value : []).map((manifest) => [
          manifest.adapter_id,
          manifest,
        ]),
      ),
    [catalog.resource],
  );

  const save = async (input: EngineProfileInput) => {
    setSaving(true);
    setDialogError("");
    try {
      if (editing && editing !== "new") {
        const patch: Partial<EngineProfileInput> = {
          label: input.label,
          engine_display_name: input.engine_display_name,
          user_notes: input.user_notes,
        };
        if (input.integration_mode !== editing.integration_mode) {
          patch.integration_mode = input.integration_mode;
        }
        if (input.endpoint_url !== editing.endpoint_url) {
          patch.endpoint_url = input.endpoint_url;
        }
        if (input.health_route !== undefined) patch.health_route = input.health_route;
        if (input.models_route !== undefined) patch.models_route = input.models_route;
        if (input.engine_extensions !== undefined)
          patch.engine_extensions = input.engine_extensions;
        if (input.secret_refs !== undefined) patch.secret_refs = input.secret_refs;
        await updateEngineProfile(editing.id, patch);
        setEditing(null);
        await profiles.reload();
      } else {
        const created = await createEngineProfile(input);
        setEditing(null);
        await profiles.reload();
        await setUpNewProfile(created);
      }
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : "Could not save the engine profile.");
    } finally {
      setSaving(false);
    }
  };

  /**
   * After a new profile is saved: an attached local endpoint is probed and, if the probe
   * succeeds, enabled. Managed servers are never started automatically; remote endpoints wait
   * for the user's explicit probe because probing them sends data off this machine.
   */
  const setUpNewProfile = async (created: EngineProfile): Promise<boolean> => {
    if (created.integration_mode === "managed_process") {
      setSetupMessage(`Added ${created.label}. Next step: start its server.`);
      return false;
    }
    if (created.integration_mode !== "attached_endpoint" || created.is_remote) {
      setSetupMessage(`Added ${created.label}. Next step: probe it.`);
      return false;
    }
    setSetupMessage(`Added ${created.label}. Probing it now…`);
    const result = await probe(created.id, false, created);
    if (result?.status !== "succeeded") {
      setSetupMessage(`Added ${created.label}, but its probe found a problem. It stays disabled.`);
      return false;
    }
    setSetupMessage(`Probe succeeded. Enabling ${created.label}…`);
    if (await enable(created.id)) {
      setSetupMessage(`${created.label} is ready. Try it in the Playground or benchmark it.`);
      return true;
    }
    setSetupMessage(`${created.label} was probed but could not be enabled.`);
    return false;
  };

  /** One-click add from the recommendations list, through the same create → probe → enable path. */
  const addRecommended = async (item: EngineRecommendation): Promise<AddOutcome> => {
    if (!item.endpoint_url) throw new Error(`No local endpoint is known for ${item.display_name}.`);
    const created = await createEngineProfile({
      label: recommendationProfileLabel(item),
      adapter_id: item.adapter_id,
      engine_display_name: null,
      integration_mode: "attached_endpoint",
      endpoint_url: item.endpoint_url,
      user_notes: null,
    });
    setAddedByEndpoint((current) => ({ ...current, [item.endpoint_url!]: created.id }));
    await profiles.reload();
    const ready = await setUpNewProfile(created);
    return {
      profileId: created.id,
      ready,
      message: ready
        ? `${created.label} is ready. Benchmark it to measure it on this machine.`
        : `Added ${created.label}. Finish its setup in the engine profiles below.`,
    };
  };

  const openManagedDialog = (item: EngineRecommendation) => {
    setDialogError("");
    setSelectedAdapterId(item.adapter_id);
    setNewProfileMode("managed_process");
    setEditing("new");
  };

  const addCandidate = async (candidate: DiscoveredEngine) => {
    const key = candidate.endpoint_url;
    setCandidateAddStates((current) => ({ ...current, [key]: { status: "adding" } }));
    try {
      const created = await createEngineProfile({
        label: `${candidate.display_name} on this machine`,
        adapter_id: candidate.adapter_id,
        engine_display_name: null,
        integration_mode: "attached_endpoint",
        endpoint_url: candidate.endpoint_url,
        user_notes: null,
      });
      setAddedByEndpoint((current) => ({ ...current, [key]: created.id }));
      setCandidateAddStates((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      await profiles.reload();
      await setUpNewProfile(created);
    } catch (error) {
      setCandidateAddStates((current) => ({
        ...current,
        [key]: {
          status: "error",
          message: error instanceof Error ? error.message : "Could not add this engine.",
        },
      }));
    }
  };

  const showProfile = (id: string) => {
    const card = document.getElementById(`engine-profile-${id}`);
    card?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    card?.querySelector<HTMLElement>("h3")?.focus();
  };

  const probe = async (
    id: string,
    confirmRemote = false,
    known?: EngineProfile,
  ): Promise<ProbeResult | null> => {
    const profile =
      known ??
      (profiles.resource.status === "ready"
        ? profiles.resource.value.find((item) => item.id === id)
        : undefined);
    const manifest = profile ? manifestById.get(profile.adapter_id) : undefined;
    if (profile?.is_remote && manifest?.category === "test_fixture") {
      setActionError(
        `${manifest.display_name} is a local test fixture and does not contact ${profile.endpoint_url}. It cannot verify a remote endpoint; use a network-capable adapter to probe that host.`,
      );
      return null;
    }
    setBusyProfile(id);
    setBusyAction("probe");
    setActionError("");
    try {
      const response = await probeEngineProfile(id, confirmRemote);
      setProbeResults((current) => ({ ...current, [id]: response.result }));
      setProbeProblems((current) => {
        const next = { ...current };
        if (response.result.status === "failed") next[id] = probeProblem(response.result);
        else delete next[id];
        return next;
      });
      setRemotePrompt(null);
      setExpanded((current) => ({ ...current, [id]: true }));
      setModelData((current) => ({ ...current, [id]: { loading: true } }));
      await profiles.reload();
      try {
        const models = await listEngineModels(id);
        setModelData((current) => ({ ...current, [id]: { loading: false, models } }));
      } catch (error) {
        setModelData((current) => ({
          ...current,
          [id]: {
            loading: false,
            error: error instanceof Error ? error.message : "Could not load saved models.",
          },
        }));
      }
      return response.result;
    } catch (error) {
      if (error instanceof ApiError && error.code === "remote_confirmation_required") {
        setRemotePrompt({
          id,
          host: error.host || "configured remote host",
          message: error.message,
        });
      } else {
        const message =
          error instanceof Error ? error.message : "The probe could not be completed.";
        setProbeProblems((current) => ({ ...current, [id]: message }));
      }
      return null;
    } finally {
      setBusyProfile(null);
      setBusyAction(null);
    }
  };

  const enable = async (id: string): Promise<boolean> => {
    setBusyProfile(id);
    setBusyAction("enable");
    setActionError("");
    try {
      await enableEngineProfile(id);
      await profiles.reload();
      return true;
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not enable this engine.");
      return false;
    } finally {
      setBusyProfile(null);
      setBusyAction(null);
    }
  };

  const toggleManagedProcess = async (profile: EngineProfile) => {
    setBusyProfile(profile.id);
    setBusyAction(profile.managed_process_running ? "stop" : "start");
    setActionError("");
    try {
      if (profile.managed_process_running) await stopEngineProfile(profile.id);
      else await startEngineProfile(profile.id);
      await profiles.reload();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Could not change this engine process state.",
      );
    } finally {
      setBusyProfile(null);
      setBusyAction(null);
    }
  };

  const inspect = async (id: string) => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
    if (modelData[id] && !modelData[id].error) return;
    setModelData((current) => ({ ...current, [id]: { loading: true } }));
    try {
      const models = await listEngineModels(id);
      setModelData((current) => ({ ...current, [id]: { loading: false, models } }));
    } catch (error) {
      setModelData((current) => ({
        ...current,
        [id]: {
          loading: false,
          error: error instanceof Error ? error.message : "Could not load saved models.",
        },
      }));
    }
  };

  const remove = async () => {
    if (!removePrompt) return;
    setBusyProfile(removePrompt.id);
    setActionError("");
    try {
      await deleteEngineProfile(removePrompt.id);
      setRemovePrompt(null);
      await profiles.reload();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not remove this profile.");
    } finally {
      setBusyProfile(null);
    }
  };

  const availableManifests = catalog.resource.status === "ready" ? catalog.resource.value : [];
  // The catalog is sorted by id, so its first entry can be a planned (unconfigurable) connector.
  const defaultNewManifest =
    availableManifests.find(
      (manifest) =>
        manifest.implementation_status === "implemented" && manifest.category !== "test_fixture",
    ) ?? availableManifests.find((manifest) => manifest.implementation_status === "implemented");
  const openAddDialog = () => {
    setDialogError("");
    setSelectedAdapterId(defaultNewManifest?.adapter_id ?? "");
    setEditing("new");
  };
  // The empty state leads with its own scan button, so the header one is hidden there.
  const showHeaderScan = !(
    profiles.resource.status === "ready" && profiles.resource.value.length === 0
  );
  return (
    <div className="screen-stack">
      <EngineRecommendations
        canAddManaged={(adapterId) =>
          manifestById.get(adapterId)?.integration_modes.includes("managed_process") ?? false
        }
        collapseStorageKey="localforge.engines.recommendationsCollapsed"
        onAdd={addRecommended}
        onAddManaged={openManagedDialog}
      />
      <div className="engine-workbench">
        <section className="panel engine-catalog-panel" aria-labelledby="engine-catalog-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">01 / ADAPTER CATALOG</p>
              <h2 id="engine-catalog-title">Engine catalog</h2>
            </div>
            <button
              className="button button--primary"
              disabled={!defaultNewManifest}
              onClick={() => {
                setDialogError("");
                setSelectedAdapterId(defaultNewManifest?.adapter_id ?? "");
                setEditing("new");
              }}
              type="button"
            >
              Add engine
            </button>
          </div>
          <p className="helper-copy">
            Implemented entries are available to configure. Planned entries are catalog references
            only. Fixture checks do not claim that an upstream engine release was live-tested.
          </p>
          <ResourcePanel
            resource={catalog.resource}
            onRetry={() => void catalog.reload()}
            loadingLabel="Loading installed adapters…"
          >
            {(items) =>
              items.length === 0 ? (
                <p className="empty-inline">No adapters are registered in this installation.</p>
              ) : (
                <div className="catalog-grid">
                  {items.map((manifest) => (
                    <article
                      className={`catalog-card ${
                        manifest.category === "test_fixture"
                          ? "catalog-card--fixture"
                          : "catalog-card--endpoint"
                      }`}
                      key={manifest.adapter_id}
                    >
                      <div className="catalog-card-intro">
                        <span className="catalog-card-icon" aria-hidden="true">
                          {manifest.category === "test_fixture" ? (
                            <IconTerminal2 size={23} stroke={1.8} />
                          ) : (
                            <IconApi size={23} stroke={1.8} />
                          )}
                        </span>
                        <div className="catalog-card-copy">
                          <div className="catalog-card-header">
                            <span
                              className={`status-pill status-pill--${
                                manifest.implementation_status === "implemented"
                                  ? "enabled"
                                  : "needs_attention"
                              }`}
                            >
                              {manifest.implementation_status === "implemented"
                                ? "Implemented"
                                : titleCase(manifest.implementation_status)}
                            </span>
                            {manifest.implementation_status === "implemented" && (
                              <span className="status-pill status-pill--enabled">
                                Fixture contract tested
                              </span>
                            )}
                            {manifest.maintenance_mode && (
                              <span className="status-pill status-pill--needs_attention">
                                Maintenance mode
                              </span>
                            )}
                            <span className="catalog-category">{titleCase(manifest.category)}</span>
                          </div>
                          <h3>{manifest.display_name}</h3>
                          <p>{manifest.description}</p>
                        </div>
                      </div>
                      <div className="catalog-card-specs">
                        {manifest.status_details && (
                          <p className="helper-copy">{manifest.status_details}</p>
                        )}
                        <div className="catalog-meta">
                          <span>Connection modes</span>
                          <strong>
                            {manifest.integration_modes.length
                              ? manifest.integration_modes.map(integrationModeLabel).join(" · ")
                              : "No callable integration mode"}
                          </strong>
                        </div>
                        <div className="catalog-meta">
                          <span>LOCALFORGE host OS</span>
                          <strong>
                            {manifest.operating_systems.length
                              ? manifest.operating_systems.map(operatingSystemLabel).join(" · ")
                              : "Not specified"}
                          </strong>
                        </div>
                        <div className="catalog-meta">
                          <span>Version test evidence</span>
                          <strong>{versionCoverageLabel(manifest)}</strong>
                        </div>
                        <details className="catalog-details catalog-capabilities">
                          <summary>
                            Declared capabilities
                            <span>{manifest.declared_capabilities.length}</span>
                          </summary>
                          {manifest.declared_capabilities.length > 0 ? (
                            <ul>
                              {manifest.declared_capabilities.map((capability) => (
                                <li key={capability}>{titleCase(capability)}</li>
                              ))}
                            </ul>
                          ) : (
                            <p>Capabilities are not declared for this adapter.</p>
                          )}
                        </details>
                        {manifest.known_limitations.length > 0 && (
                          <details className="catalog-details catalog-limitations">
                            <summary>Known limitations</summary>
                            <ul>
                              {manifest.known_limitations.map((limitation) => (
                                <li key={limitation}>{limitation}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                        {manifest.documentation_url && (
                          <a
                            className="catalog-doc-link"
                            href={manifest.documentation_url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            Adapter documentation
                          </a>
                        )}
                      </div>
                      {manifest.implementation_status === "implemented" ? (
                        <button
                          className="button button--secondary catalog-card-action"
                          onClick={() => {
                            setDialogError("");
                            setSelectedAdapterId(manifest.adapter_id);
                            setEditing("new");
                          }}
                          type="button"
                        >
                          Add this adapter
                        </button>
                      ) : (
                        <button
                          className="button button--secondary catalog-card-action"
                          disabled
                          type="button"
                        >
                          Not available yet
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              )
            }
          </ResourcePanel>
        </section>
        <section className="panel engine-profiles-panel" aria-labelledby="engine-profiles-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">02 / LOCAL CONNECTIONS</p>
              <h2 id="engine-profiles-title">Engine profiles</h2>
              <p className="engine-profiles-description">
                Local servers and user-selected endpoints, in one place.
              </p>
            </div>
            <div className="engine-profiles-actions">
              {showHeaderScan && (
                <button
                  className="button button--secondary"
                  disabled={discovery.state.status === "scanning"}
                  onClick={() => void discovery.scan()}
                  type="button"
                >
                  {discovery.state.status === "scanning" ? "Scanning…" : "Scan this machine"}
                </button>
              )}
              <button
                className="button button--quiet"
                onClick={() => void profiles.reload()}
                type="button"
              >
                Refresh
              </button>
            </div>
          </div>
          <p className="visually-hidden" role="status" aria-live="polite">
            {scanStatusMessage(discovery.state)}
          </p>
          <p className="engine-setup-message" role="status" aria-live="polite">
            {setupMessage}
          </p>
          {actionError && (
            <p className="form-error" role="alert">
              {actionError}
            </p>
          )}
          <EngineDiscoveryPanel
            state={discovery.state}
            manifests={manifestById}
            profiles={profiles.resource.status === "ready" ? profiles.resource.value : []}
            addedByEndpoint={addedByEndpoint}
            addStates={candidateAddStates}
            onAdd={(candidate) => void addCandidate(candidate)}
            onAddManually={openAddDialog}
            onDismiss={discovery.dismiss}
            onScan={() => void discovery.scan()}
            onShowProfile={showProfile}
          />
          <ResourcePanel
            resource={profiles.resource}
            onRetry={() => void profiles.reload()}
            loadingLabel="Loading engine profiles…"
          >
            {(items) =>
              items.length === 0 ? (
                <div className="empty-inline empty-inline--action">
                  <figure className="engine-profile-empty-visual" aria-hidden="true">
                    <img src="/images/localforge-engine-detail.jpg" alt="" />
                    <figcaption>LOCAL WORKSPACE / READY TO CONNECT</figcaption>
                  </figure>
                  <div className="engine-profile-empty-copy">
                    <p>
                      <strong>No engine profiles are configured.</strong>
                    </p>
                    <p>
                      Already running Ollama, llama.cpp or another engine server? Scan this computer
                      (127.0.0.1 only) to find it and add it in one click.
                    </p>
                    <div className="engine-profile-empty-actions">
                      <button
                        className="button button--primary"
                        disabled={discovery.state.status === "scanning"}
                        onClick={() => void discovery.scan()}
                        type="button"
                      >
                        {discovery.state.status === "scanning" ? "Scanning…" : "Scan this machine"}
                      </button>
                      <button
                        className="button button--quiet"
                        disabled={!defaultNewManifest}
                        onClick={openAddDialog}
                        type="button"
                      >
                        Add manually
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="engine-profile-list">
                  {items.map((profile) => {
                    const manifest = manifestById.get(profile.adapter_id);
                    const probeResult = probeResults[profile.id];
                    const savedCapabilities = profile.capabilities;
                    const modelsState = modelData[profile.id];
                    const managed = profile.integration_mode === "managed_process";
                    const serverStopped = managed && !profile.managed_process_running;
                    const probeSucceeded = probeOutcome(profile, probeResult) === "succeeded";
                    const probeBlockedReason = manifest
                      ? serverStopped
                        ? "Start the local server before probing it."
                        : undefined
                      : "This adapter is not installed. Reinstall it to probe this profile.";
                    const isBusy = busyProfile === profile.id;
                    const problem =
                      probeProblems[profile.id] ??
                      (profile.status === "needs_attention" && !probeResult
                        ? "The latest probe found a problem. Check that the engine is running, then probe again."
                        : undefined);
                    return (
                      <article
                        className="engine-profile-card"
                        id={`engine-profile-${profile.id}`}
                        key={profile.id}
                      >
                        <div className="engine-profile-header">
                          <div>
                            <div className="profile-name-row">
                              <h3 tabIndex={-1}>{profile.label}</h3>
                              <span className={`status-pill status-pill--${profile.status}`}>
                                {statusLabel(profile.status)}
                              </span>
                            </div>
                            <p>
                              {profile.engine_display_name ||
                                manifest?.display_name ||
                                profile.adapter_id}{" "}
                              · {profileConnectionLabel(profile)}
                            </p>
                          </div>
                          <div className="engine-actions">
                            {/* Incomplete setup steps (start, first probe, enable) are actions in
                                the checklist below; the header keeps the repeat actions. */}
                            {managed && profile.managed_process_running && (
                              <button
                                className="button button--danger-quiet"
                                disabled={!manifest || isBusy}
                                onClick={() => void toggleManagedProcess(profile)}
                                type="button"
                              >
                                {isBusy && busyAction === "stop" ? "Stopping…" : "Stop server"}
                              </button>
                            )}
                            {probeSucceeded && (
                              <button
                                className="button button--quiet"
                                disabled={!manifest || isBusy || serverStopped}
                                title={probeBlockedReason}
                                onClick={() => void probe(profile.id)}
                                type="button"
                              >
                                {isBusy && busyAction === "probe" ? "Probing…" : "Probe"}
                              </button>
                            )}
                            <button
                              className="button button--quiet"
                              disabled={serverStopped}
                              onClick={() => void inspect(profile.id)}
                              type="button"
                            >
                              {expanded[profile.id] ? "Hide details" : "Inspect"}
                            </button>
                            <button
                              className="button button--quiet"
                              disabled={!manifest || isBusy || profile.managed_process_running}
                              title={
                                manifest
                                  ? undefined
                                  : "This adapter is not installed. Remove the profile or reinstall its adapter to edit it."
                              }
                              onClick={() => {
                                setDialogError("");
                                setEditing(profile);
                              }}
                              type="button"
                            >
                              Edit
                            </button>
                            <button
                              className="button button--danger-quiet"
                              disabled={isBusy || profile.managed_process_running}
                              onClick={() => setRemovePrompt(profile)}
                              type="button"
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                        {!manifest && (
                          <p className="inline-warning" role="status">
                            Adapter {profile.adapter_id} is not installed. Probing and editing are
                            unavailable; this profile can still be removed.
                          </p>
                        )}
                        <EngineSetupChecklist
                          profile={profile}
                          probeResult={probeResult}
                          busy={isBusy || !manifest}
                          busyAction={
                            isBusy && busyAction && busyAction !== "stop" ? busyAction : null
                          }
                          canProbe={Boolean(manifest) && !serverStopped}
                          probeBlockedReason={probeBlockedReason}
                          onStart={() => void toggleManagedProcess(profile)}
                          onProbe={() => void probe(profile.id)}
                          onEnable={() => void enable(profile.id)}
                        />
                        {managed && profile.adapter_id === "localforge.llama_cpp" && (
                          <EngineTuner profile={profile} onApplied={() => void profiles.reload()} />
                        )}
                        {problem && (
                          <p className="inline-warning engine-probe-problem" role="alert">
                            <strong>Probe problem:</strong> {problem} This engine stays disabled
                            until a probe succeeds.
                          </p>
                        )}
                        <dl className="profile-attributes">
                          <div>
                            <dt>Adapter</dt>
                            <dd>{manifest?.display_name || profile.adapter_id}</dd>
                          </div>
                          <div>
                            <dt>Endpoint</dt>
                            <dd>
                              {profile.endpoint_url || "Not configured"}
                              {profile.is_remote && <span className="remote-chip">Remote</span>}
                            </dd>
                          </div>
                          <div>
                            <dt>Version</dt>
                            <dd>{profile.engine_version || "Unknown / not probed"}</dd>
                          </div>
                          <div>
                            <dt>Last probe</dt>
                            <dd>{formatDate(profile.last_probed_at)}</dd>
                          </div>
                          <div>
                            <dt>Credentials</dt>
                            <dd>
                              {profile.credential_configured
                                ? "Configured · value hidden"
                                : "Not configured"}
                            </dd>
                          </div>
                        </dl>
                        {expanded[profile.id] && (
                          <div className="profile-inspection">
                            {probeResult ? (
                              <ProbeDetails
                                result={probeResult}
                                models={modelsState?.models}
                                modelsLoading={modelsState?.loading}
                                modelsError={modelsState?.error}
                              />
                            ) : (
                              <>
                                <div className="inspection-heading">
                                  <div>
                                    <h4>Saved capability snapshot</h4>
                                    <p>
                                      {profile.last_probed_at
                                        ? `Last updated ${formatDate(profile.last_probed_at)}`
                                        : "No probe snapshot exists yet."}
                                    </p>
                                  </div>
                                </div>
                                {savedCapabilities ? (
                                  <CapabilityTable items={savedCapabilities} />
                                ) : (
                                  <p className="empty-inline">
                                    Capabilities unknown until this profile has a probe result.
                                  </p>
                                )}
                                {modelsState?.loading && (
                                  <p role="status">Loading saved model inventory…</p>
                                )}
                                {modelsState?.error && (
                                  <p className="form-error" role="alert">
                                    {modelsState.error}
                                  </p>
                                )}
                                {modelsState?.models && (
                                  <section>
                                    <h4>Saved model inventory</h4>
                                    {modelsState.models.length ? (
                                      <div className="table-scroll">
                                        <table>
                                          <thead>
                                            <tr>
                                              <th scope="col">Model</th>
                                              <th scope="col">Reference</th>
                                              <th scope="col">State</th>
                                            </tr>
                                          </thead>
                                          <tbody>
                                            {modelsState.models.map((model) => (
                                              <tr key={model.id}>
                                                <th scope="row">{model.display_name}</th>
                                                <td>{model.model_ref || "Unknown"}</td>
                                                <td>{titleCase(model.status)}</td>
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    ) : (
                                      <p className="empty-inline">
                                        No saved model records. Inventory may be empty or
                                        unsupported; check the capability value above.
                                      </p>
                                    )}
                                  </section>
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              )
            }
          </ResourcePanel>
        </section>
      </div>
      {editing && manifestById.get(editing === "new" ? selectedAdapterId : editing.adapter_id) && (
        <EngineProfileDialog
          busy={saving}
          error={dialogError}
          initial={editing === "new" ? undefined : editing}
          initialMode={editing === "new" ? newProfileMode : null}
          manifest={manifestById.get(editing === "new" ? selectedAdapterId : editing.adapter_id)!}
          onClose={() => setEditing(null)}
          onSubmit={(input) => void save(input)}
        />
      )}
      {remotePrompt && (
        <div className="dialog-scrim">
          <AccessibleDialog
            className="dialog-card dialog-card--compact"
            titleId="remote-dialog-title"
          >
            <div className="section-title-row">
              <div>
                <p className="eyebrow">REMOTE DATA EGRESS</p>
                <h2 id="remote-dialog-title">Confirm remote probe</h2>
              </div>
              <button
                aria-label="Close dialog"
                className="icon-button"
                onClick={() => setRemotePrompt(null)}
                type="button"
              >
                ×
              </button>
            </div>
            <p className="notice notice--warning">
              Target host: <strong>{remotePrompt.host}</strong>
            </p>
            <p>{remotePrompt.message}</p>
            <p>
              The probe will contact this host. If you later run a benchmark against it, prompts and
              model requests will also leave this machine. Do not continue unless you control or
              trust this endpoint.
            </p>
            <div className="dialog-actions">
              <button
                className="button button--quiet"
                onClick={() => setRemotePrompt(null)}
                autoFocus
                data-initial-focus
                type="button"
              >
                Cancel
              </button>
              <button
                className="button button--primary"
                onClick={() => void probe(remotePrompt.id, true)}
                type="button"
              >
                I understand — probe remote engine
              </button>
            </div>
          </AccessibleDialog>
        </div>
      )}
      {removePrompt && (
        <div className="dialog-scrim">
          <AccessibleDialog
            className="dialog-card dialog-card--compact"
            titleId="remove-dialog-title"
          >
            <div className="section-title-row">
              <h2 id="remove-dialog-title">Remove engine profile?</h2>
              <button
                aria-label="Close dialog"
                className="icon-button"
                onClick={() => setRemovePrompt(null)}
                type="button"
              >
                ×
              </button>
            </div>
            <p>
              This removes “{removePrompt.label}” from this workspace. It does not remove the engine
              or model from your machine.
            </p>
            <div className="dialog-actions">
              <button
                className="button button--quiet"
                onClick={() => setRemovePrompt(null)}
                autoFocus
                data-initial-focus
                type="button"
              >
                Keep profile
              </button>
              <button
                className="button button--danger"
                disabled={busyProfile === removePrompt.id}
                onClick={() => void remove()}
                type="button"
              >
                {busyProfile === removePrompt.id ? "Removing…" : "Remove profile"}
              </button>
            </div>
          </AccessibleDialog>
        </div>
      )}
    </div>
  );
}
