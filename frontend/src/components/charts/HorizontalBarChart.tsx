import { formatValue, linearScale, roundedBarPath } from "../../lib/chart-scale";
import { useChartCursor, useMeasuredWidth } from "./chart-hooks";
import { BottomAxis, ChartTooltip, CursorAnnouncement, type PlotBox } from "./ChartParts";

export type HorizontalBarRow = {
  key: string;
  label: string;
  /** null renders an explicit "Unavailable" row, never a zero-length bar. */
  value: number | null;
  /** Why the value is missing (shown in place of the bar). */
  reason?: string | null;
  /** Highlighted mark (e.g. the comparison baseline). */
  emphasis?: boolean;
  /** Observed min–max spread drawn as a whisker when the API provides it. */
  range?: [number, number] | null;
  note?: string;
};

const ROW_HEIGHT = 30;
const BAR_THICKNESS = 16;

/**
 * Category rows with bars growing from a shared zero baseline, value labels at the bar
 * tip, optional min–max whiskers, and explicit unavailable rows.
 */
export function HorizontalBarChart({
  rows,
  unit,
  xLabel,
  ariaLabel,
}: {
  rows: HorizontalBarRow[];
  unit: string;
  xLabel: string;
  ariaLabel: string;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(560);
  const width = Math.max(300, measured);
  const longestLabel = Math.max(...rows.map((row) => row.label.length), 4);
  const box: PlotBox = {
    left: Math.min(200, Math.max(64, longestLabel * 7 + 16)),
    right: rows.some((row) => row.emphasis) ? 136 : 84,
    top: 8,
    bottom: 44,
  };
  const height = box.top + rows.length * ROW_HEIGHT + box.bottom;
  const extents = rows.flatMap((row) => [
    ...(row.value === null ? [] : [row.value]),
    ...(row.range ?? []),
  ]);
  const x = linearScale(extents, [box.left, width - box.right], { tickCount: 4 });
  const zero = x.map(0);
  const centers = rows.map((_, index) => box.top + index * ROW_HEIGHT + ROW_HEIGHT / 2);
  const { active, svgProps } = useChartCursor(centers, "y", height);
  const activeRow = active === null ? null : rows[active];
  const readout = activeRow
    ? [
        `${activeRow.label}${activeRow.emphasis ? " (baseline)" : ""}`,
        activeRow.value === null
          ? `Unavailable${activeRow.reason ? `: ${activeRow.reason}` : ""}`
          : formatValue(activeRow.value, unit),
        ...(activeRow.range
          ? [`Range ${formatValue(activeRow.range[0])}–${formatValue(activeRow.range[1], unit)}`]
          : []),
        ...(activeRow.note ? [activeRow.note] : []),
      ]
    : null;
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${ariaLabel}. ${rows.length} rows. Use arrow keys to read values.`}
        className="lf-chart__svg"
        height={height}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        {...svgProps}
      >
        <g className="lf-chart__grid" aria-hidden="true">
          {x.ticks.map((tick) => (
            <line
              key={tick}
              x1={x.map(tick)}
              x2={x.map(tick)}
              y1={box.top}
              y2={height - box.bottom}
            />
          ))}
        </g>
        <BottomAxis
          box={box}
          height={height}
          label={xLabel}
          map={x.map}
          ticks={x.ticks}
          width={width}
        />
        {rows.map((row, index) => {
          const center = centers[index] ?? 0;
          const isActive = index === active;
          const value = row.value;
          const tip = value === null ? zero : x.map(value);
          const barX = Math.min(zero, tip);
          const barWidth = Math.abs(tip - zero);
          const labelX = Math.max(tip, row.range ? x.map(row.range[1]) : tip) + 6;
          return (
            <g key={row.key}>
              {isActive && (
                <rect
                  className="lf-chart__row-highlight"
                  height={ROW_HEIGHT}
                  width={width - 4}
                  x={2}
                  y={center - ROW_HEIGHT / 2}
                />
              )}
              <text className="lf-chart__category" x={box.left - 8} y={center + 4} textAnchor="end">
                {row.label}
              </text>
              {value === null ? (
                <text className="lf-chart__unavailable" x={zero + 6} y={center + 4}>
                  {fitText(`Unavailable${row.reason ? ` · ${row.reason}` : ""}`, width - zero - 12)}
                </text>
              ) : (
                <>
                  {barWidth > 0 ? (
                    <path
                      className={`lf-chart__bar${row.emphasis ? " is-emphasis" : ""}${rows.some((item) => item.emphasis) && !row.emphasis ? " is-muted" : ""}`}
                      d={
                        value >= 0
                          ? roundedBarPath(
                              barX,
                              center - BAR_THICKNESS / 2,
                              barWidth,
                              BAR_THICKNESS,
                              4,
                              "right",
                            )
                          : `M${barX},${center - BAR_THICKNESS / 2} h${barWidth} v${BAR_THICKNESS} h${-barWidth} Z`
                      }
                    />
                  ) : (
                    <line
                      className="lf-chart__zero-mark"
                      x1={zero}
                      x2={zero}
                      y1={center - BAR_THICKNESS / 2}
                      y2={center + BAR_THICKNESS / 2}
                    />
                  )}
                  {row.range && (
                    <g className="lf-chart__whisker" aria-hidden="true">
                      <line
                        x1={x.map(row.range[0])}
                        x2={x.map(row.range[1])}
                        y1={center}
                        y2={center}
                      />
                      <line
                        x1={x.map(row.range[0])}
                        x2={x.map(row.range[0])}
                        y1={center - 5}
                        y2={center + 5}
                      />
                      <line
                        x1={x.map(row.range[1])}
                        x2={x.map(row.range[1])}
                        y1={center - 5}
                        y2={center + 5}
                      />
                    </g>
                  )}
                  <text className="lf-chart__value" x={labelX} y={center + 4}>
                    {formatValue(value)}
                    {row.emphasis ? " · baseline" : ""}
                  </text>
                </>
              )}
            </g>
          );
        })}
        {activeRow && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={activeRow.value === null ? zero : x.map(activeRow.value)}
            y={centers[active ?? 0] ?? 0}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}

/** Truncate a label to the pixels available (full text stays in the tooltip and table). */
function fitText(text: string, pixels: number): string {
  const maxChars = Math.max(11, Math.floor(pixels / 6.4));
  return text.length <= maxChars ? text : `${text.slice(0, maxChars - 1).trimEnd()}…`;
}
