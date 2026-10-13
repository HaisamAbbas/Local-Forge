import { formatTick, formatValue, linearScale, roundedBarPath } from "../../lib/chart-scale";
import type { HistogramBin } from "../../lib/chart-data";
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

/** Count histogram over contiguous bins; adjacent columns are separated by a 2px gap. */
export function HistogramChart({
  bins,
  unit,
  xLabel,
  ariaLabel,
  height = 220,
}: {
  bins: HistogramBin[];
  unit: string;
  xLabel: string;
  ariaLabel: string;
  height?: number;
}) {
  const { ref, width: measured } = useMeasuredWidth<HTMLDivElement>(640);
  const width = Math.max(300, measured);
  const first = bins[0];
  const last = bins[bins.length - 1];
  const start = first?.start ?? 0;
  const end = last && last.end > start ? last.end : start + 1;
  const plotWidth = width - BOX.left - BOX.right;
  const xFor = (value: number) => BOX.left + ((value - start) / (end - start)) * plotWidth;
  const y = linearScale(
    bins.map((bin) => bin.count),
    [height - BOX.bottom, BOX.top],
  );
  const centers = bins.map((bin) => xFor((bin.start + bin.end) / 2));
  const { active, svgProps } = useChartCursor(centers, "x", width);
  const activeBin = active === null ? null : bins[active];
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  const tickEvery = Math.max(1, Math.ceil((bins.length + 1) / 7));
  const edges = [...bins.map((bin) => bin.start), end].filter(
    (_, index, all) => index % tickEvery === 0 || index === all.length - 1,
  );
  const readout = activeBin
    ? [
        `${formatValue(activeBin.start)}–${formatValue(activeBin.end, unit)}`,
        `${activeBin.count.toLocaleString()} gaps (${((activeBin.count / total) * 100).toFixed(1)}%)`,
      ]
    : null;
  return (
    <div className="lf-chart__plot" ref={ref}>
      <svg
        aria-label={`${ariaLabel}. ${total} values in ${bins.length} bins. Use arrow keys to read bins.`}
        className="lf-chart__svg"
        height={height}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        {...svgProps}
      >
        <ValueGrid ticks={y.ticks} map={y.map} box={BOX} width={width} />
        {bins.map((bin, index) => {
          const x0 = xFor(bin.start);
          const x1 = xFor(bin.end);
          const columnWidth = Math.max(1, x1 - x0 - 2);
          const top = y.map(bin.count);
          const barHeight = height - BOX.bottom - top;
          if (bin.count === 0) return null;
          return (
            <path
              className={index === active ? "lf-chart__bar is-active" : "lf-chart__bar"}
              d={roundedBarPath(x0 + 1, top, columnWidth, barHeight, 4, "up")}
              key={`${bin.start}-${bin.end}`}
            />
          );
        })}
        <BottomAxis
          box={BOX}
          format={formatTick}
          height={height}
          label={xLabel}
          map={xFor}
          ticks={edges}
          width={width}
        />
        <LeftAxisLabel box={BOX} height={height} label="Count" />
        {activeBin && readout && (
          <ChartTooltip
            height={height}
            lines={readout}
            width={width}
            x={xFor((activeBin.start + activeBin.end) / 2)}
            y={y.map(activeBin.count)}
          />
        )}
      </svg>
      <CursorAnnouncement text={readout ? readout.join(", ") : null} />
    </div>
  );
}
