import { formatValue, linearScale } from "../../lib/chart-scale";
import type { TimePoint } from "../../lib/chart-data";
import { useChartCursor, useMeasuredWidth } from "./chart-hooks";
import {
  BottomAxis,
  ChartTooltip,
  CursorAnnouncement,
  LeftAxisLabel,
  ValueGrid,
  type PlotBox,
} from "./ChartParts";

const BOX: PlotBox = { left: 58, right: 18, top: 16, bottom: 44 };

/**
 * Single-series line over elapsed run time. The line only joins consecutive observed
 * samples (it is a reading aid, not interpolated data) and every sample is a dot.
 */
export function TimeSeriesChart({
  points,
  unit,
  yLabel,
  xLabel = "Seconds since run start",
  ariaLabel,
  height = 180,
  zeroBased = true,
}: {
  points: TimePoint[];
  unit: string;
  yLabel: string;
  xLabel?: string;
  ariaLabel: string;
  height?: number;
  zeroBased?: boolean;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(420);
  const width = Math.max(280, measured);
  const x = linearScale(
    points.map((point) => point.t),
    [BOX.left, width - BOX.right],
    { tickCount: 5 },
  );
  const y = linearScale(
    points.map((point) => point.value),
    [height - BOX.bottom, BOX.top],
    { zeroBased, tickCount: 3 },
  );
  const xs = points.map((point) => x.map(point.t));
  const { active, svgProps } = useChartCursor(xs, "x", width);
  const activePoint = active === null ? null : points[active];
  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${x.map(point.t)},${y.map(point.value)}`)
    .join(" ");
  const baseY = height - BOX.bottom;
  const first = points[0];
  const last = points[points.length - 1];
  const areaPath =
    first && last && points.length > 1
      ? `${linePath} L${x.map(last.t)},${baseY} L${x.map(first.t)},${baseY} Z`
      : null;
  const readout = activePoint
    ? [`${formatValue(activePoint.t, "s")} into run`, formatValue(activePoint.value, unit)]
    : null;
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${ariaLabel}. ${points.length} samples. Use arrow keys to read values.`}
        className="lf-chart__svg"
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
        {areaPath && <path className="lf-chart__area" d={areaPath} />}
        {points.length > 1 && <path className="lf-chart__line" d={linePath} />}
        {activePoint && (
          <line
            className="lf-chart__crosshair"
            x1={x.map(activePoint.t)}
            x2={x.map(activePoint.t)}
            y1={BOX.top}
            y2={baseY}
          />
        )}
        {points.map((point, index) =>
          index === active || index === points.length - 1 || points.length <= 40 ? (
            <circle
              className={index === active ? "lf-chart__dot is-active" : "lf-chart__dot"}
              cx={x.map(point.t)}
              cy={y.map(point.value)}
              key={point.key}
              r={index === active ? 5 : points.length <= 40 ? 3 : 4}
            />
          ) : null,
        )}
        {activePoint && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={x.map(activePoint.t)}
            y={y.map(activePoint.value)}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}
