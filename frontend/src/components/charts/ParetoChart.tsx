import { formatValue, linearScale } from "../../lib/chart-scale";
import type { ParetoPoint } from "../../lib/chart-data";
import { useChartCursor, useMeasuredWidth } from "./chart-hooks";
import {
  BottomAxis,
  ChartTooltip,
  CursorAnnouncement,
  LeftAxisLabel,
  ValueGrid,
  type PlotBox,
} from "./ChartParts";

const BOX: PlotBox = { left: 62, right: 24, top: 18, bottom: 44 };

function shortLabel(label: string): string {
  return label.length > 26 ? `${label.slice(0, 25)}…` : label;
}

/**
 * Scatter of one point per run: throughput on x (higher is better), latency on y (lower is
 * better). Pareto-efficient points are filled and joined by the frontier line; dominated
 * points are drawn hollow. Both axes start at zero. Every point carries a text label.
 */
export function ParetoChart({
  points,
  frontier,
  xUnit,
  yUnit,
  xLabel,
  yLabel,
  ariaLabel,
  height = 280,
}: {
  points: ParetoPoint[];
  frontier: string[];
  xUnit: string;
  yUnit: string;
  xLabel: string;
  yLabel: string;
  ariaLabel: string;
  height?: number;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(640);
  const width = Math.max(300, measured);
  const x = linearScale(
    points.map((point) => point.x),
    [BOX.left, width - BOX.right],
  );
  const y = linearScale(
    points.map((point) => point.y),
    [height - BOX.bottom, BOX.top],
  );
  const xs = points.map((point) => x.map(point.x));
  const { active, svgProps } = useChartCursor(xs, "x", width);
  const activePoint = active === null ? null : points[active];
  const byKey = new Map(points.map((point) => [point.key, point]));
  const frontierPath = frontier
    .map((key) => byKey.get(key))
    .filter((point): point is ParetoPoint => Boolean(point))
    .map((point) => `${x.map(point.x)},${y.map(point.y)}`)
    .join(" ");
  const readout = activePoint
    ? [
        activePoint.label + (activePoint.isBaseline ? " (baseline)" : ""),
        `Throughput: ${formatValue(activePoint.x, xUnit)}`,
        `Latency: ${formatValue(activePoint.y, yUnit)}`,
        activePoint.efficient ? "Pareto-efficient" : "Dominated by another run",
      ]
    : null;
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${ariaLabel}. ${points.length} runs, ${frontier.length} Pareto-efficient. Use arrow keys to read values.`}
        className="lf-chart__svg lf-pareto"
        height={height}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        {...svgProps}
      >
        <ValueGrid ticks={y.ticks} map={y.map} box={BOX} width={width} />
        <BottomAxis
          box={BOX}
          height={height}
          label={xLabel}
          map={x.map}
          ticks={x.ticks}
          width={width}
        />
        <LeftAxisLabel box={BOX} height={height} label={yLabel} />
        {frontier.length > 1 && (
          <polyline
            aria-hidden="true"
            className="lf-chart__line lf-pareto__frontier"
            points={frontierPath}
          />
        )}
        {points.map((point, index) => {
          const cx = x.map(point.x);
          const cy = y.map(point.y);
          const anchorEnd = cx > width - BOX.right - 120;
          return (
            <g key={point.key}>
              <circle
                className={[
                  "lf-chart__dot",
                  point.efficient ? "lf-pareto__dot--efficient" : "lf-pareto__dot--dominated",
                  point.isBaseline ? "lf-pareto__dot--baseline" : "",
                  index === active ? "is-active" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                cx={cx}
                cy={cy}
                r={index === active ? 7 : 5}
              />
              <text
                aria-hidden="true"
                className="lf-chart__value lf-pareto__label"
                textAnchor={anchorEnd ? "end" : "start"}
                x={anchorEnd ? cx - 9 : cx + 9}
                y={cy - 8}
              >
                {shortLabel(point.label)}
              </text>
            </g>
          );
        })}
        {activePoint && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={x.map(activePoint.x)}
            y={y.map(activePoint.y)}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}
