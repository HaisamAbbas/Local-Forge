import { useMemo } from "react";

import type { ExperimentSummary, RunComparison } from "../lib/api";
import {
  comparisonChartGroups,
  experimentChartGroups,
  type ComparisonChartGroup,
} from "../lib/chart-data";
import { formatValue } from "../lib/chart-scale";
import { ChartFigure } from "./charts/ChartParts";
import { HorizontalBarChart } from "./charts/HorizontalBarChart";

function directionText(group: ComparisonChartGroup): string {
  if (group.direction === "lower") return "lower is better";
  if (group.direction === "higher") return "higher is better";
  return "";
}

function GroupChart({
  group,
  idPrefix,
  valueColumn,
}: {
  group: ComparisonChartGroup;
  idPrefix: string;
  valueColumn: string;
}) {
  const hasRange = group.rows.some((row) => row.range);
  return (
    <ChartFigure
      id={`${idPrefix}-${group.id.replace(/[^a-z0-9]+/gi, "-")}`}
      title={group.title}
      subtitle={[group.metricName, group.unit, directionText(group)].filter(Boolean).join(" · ")}
      caption={group.labels ? `Labels: ${group.labels}.` : undefined}
      table={{
        caption: `${group.title} by ${idPrefix === "experiment-chart" ? "variant" : "run"}`,
        columns: [
          idPrefix === "experiment-chart" ? "Variant" : "Run",
          valueColumn,
          ...(hasRange ? ["Min–max"] : []),
          "Sample basis",
        ],
        rows: group.rows.map((row) => ({
          key: row.key,
          cells: [
            `${row.label}${row.isBaseline ? " (baseline)" : ""}`,
            formatValue(row.value, group.unit),
            ...(hasRange
              ? [
                  row.range
                    ? `${formatValue(row.range[0])}–${formatValue(row.range[1], group.unit)}`
                    : "No spread",
                ]
              : []),
            row.note,
          ],
        })),
      }}
    >
      <HorizontalBarChart
        ariaLabel={`${group.title} in ${group.unit}, ${directionText(group)}`}
        rows={group.rows.map((row) => ({
          key: row.key,
          label: row.label,
          value: row.value,
          emphasis: row.isBaseline,
          range: row.range,
          note: row.note,
          reason: row.value === null ? "no value reported for this run" : null,
        }))}
        unit={group.unit}
        xLabel={`${group.title} (${group.unit})`}
      />
    </ChartFigure>
  );
}

/** Bar charts of the headline shared metrics for a run comparison (baseline highlighted). */
export function RunComparisonCharts({
  comparison,
  runLabel,
}: {
  comparison: RunComparison;
  runLabel?: (runId: string) => string;
}) {
  const groups = useMemo(() => comparisonChartGroups(comparison, runLabel), [comparison, runLabel]);
  if (groups.length === 0)
    return (
      <p className="chart-no-data" role="status">
        None of the headline latency or throughput metrics has a reported value shared by these
        runs, so there is nothing to chart. See the table below for every shared metric.
      </p>
    );
  return (
    <div>
      <p className="source-note">
        Highlighted bar = baseline run; grey bars = other runs. One persisted value per run: the
        comparison API reports no per-run uncertainty, so no error bars are drawn. Full values,
        sample counts and deltas are in the table below.
      </p>
      <div className="lf-chart-grid">
        {groups.map((group) => (
          <GroupChart
            group={group}
            idPrefix="comparison-chart"
            key={group.id}
            valueColumn="Value"
          />
        ))}
      </div>
    </div>
  );
}

/** Variant medians with min–max whiskers across repetitions (from the experiment summary). */
export function ExperimentVariantCharts({ summary }: { summary: ExperimentSummary }) {
  const groups = useMemo(() => experimentChartGroups(summary), [summary]);
  if (groups.length === 0)
    return (
      <p className="chart-no-data" role="status">
        No successful repetition reported a headline metric yet, so there is nothing to chart.
      </p>
    );
  return (
    <div>
      <p className="source-note">
        Bar = median across successful repetitions; whisker = observed min–max spread. Highlighted
        bar = Baseline variant.
      </p>
      <div className="lf-chart-grid">
        {groups.map((group) => (
          <GroupChart
            group={group}
            idPrefix="experiment-chart"
            key={group.id}
            valueColumn="Median"
          />
        ))}
      </div>
    </div>
  );
}
