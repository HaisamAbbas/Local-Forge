import { integerTicks, formatValue, linearScale } from "../../lib/chart-scale";
import type { RequestPoint } from "../../lib/chart-data";
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
 * One dot per request on a numeric request-sequence axis. Gaps in the sequence stay gaps
 * (no connecting line, no interpolation); the y axis always starts at zero.
 */
export function RequestPointChart({
  points,
  unit,
  yLabel,
  xLabel = "Request sequence number",
  ariaLabel,
  height = 220,
  valueLabel = "Value",
}: {
  points: RequestPoint[];
  unit: string;
  yLabel: string;
  xLabel?: string;
  ariaLabel: string;
  height?: number;
  valueLabel?: string;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(640);
  const width = Math.max(300, measured);
  const numbers = points.map((point) => point.requestNumber);
  const minX = Math.min(...numbers);
  const maxX = Math.max(...numbers);
  const plotWidth = width - BOX.left - BOX.right;
  const pad = Math.min(24, plotWidth / 8);
  const xFor = (value: number) =>
    minX === maxX
      ? BOX.left + plotWidth / 2
      : BOX.left + pad + ((value - minX) / (maxX - minX)) * (plotWidth - pad * 2);
  const y = linearScale(
    points.map((point) => point.value),
    [height - BOX.bottom, BOX.top],
  );
  const xs = points.map((point) => xFor(point.requestNumber));
  const { active, svgProps } = useChartCursor(xs, "x", width);
  const activePoint = active === null ? null : points[active];
  const readout = activePoint
    ? [
        `Request ${activePoint.requestNumber}`,
        `${valueLabel}: ${formatValue(activePoint.value, unit)}`,
        ...(activePoint.detail ? [activePoint.detail] : []),
      ]
    : null;
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${ariaLabel}. ${points.length} points. Use arrow keys to read values.`}
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
          map={xFor}
          ticks={minX === maxX ? [minX] : integerTicks(minX, maxX)}
          width={width}
        />
        <LeftAxisLabel box={BOX} height={height} label={yLabel} />
        {activePoint && (
          <line
            className="lf-chart__crosshair"
            x1={xFor(activePoint.requestNumber)}
            x2={xFor(activePoint.requestNumber)}
            y1={BOX.top}
            y2={height - BOX.bottom}
          />
        )}
        {points.map((point, index) => (
          <circle
            className={index === active ? "lf-chart__dot is-active" : "lf-chart__dot"}
            cx={xFor(point.requestNumber)}
            cy={y.map(point.value)}
            key={point.key}
            r={index === active ? 6 : 4}
          />
        ))}
        {activePoint && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={xFor(activePoint.requestNumber)}
            y={y.map(activePoint.value)}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}
