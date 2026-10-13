import type { ReactNode } from "react";

import { formatTick } from "../../lib/chart-scale";

export type ChartTable = {
  caption: string;
  columns: string[];
  rows: Array<{ key: string; cells: ReactNode[] }>;
};

/**
 * Figure wrapper shared by every chart: title, subtitle, an explicit empty state, a
 * caption for formulas and provenance, and a collapsible data table as the accessible
 * fallback (every value in the chart is also readable there).
 */
export function ChartFigure({
  id,
  title,
  subtitle,
  caption,
  empty,
  table,
  headingLevel = 4,
  className,
  children,
}: {
  id: string;
  title: string;
  subtitle?: ReactNode;
  caption?: ReactNode;
  /** When set, the chart is replaced by this message (no fabricated placeholder marks). */
  empty?: string | null;
  table?: ChartTable;
  headingLevel?: 3 | 4;
  className?: string;
  children?: ReactNode;
}) {
  const Heading = headingLevel === 3 ? "h3" : "h4";
  const titleId = `${id}-title`;
  return (
    <figure className={`lf-chart ${className ?? ""}`.trim()} aria-labelledby={titleId}>
      <div className="lf-chart__header">
        <Heading className="lf-chart__title" id={titleId}>
          {title}
        </Heading>
        {subtitle && <p className="lf-chart__subtitle">{subtitle}</p>}
      </div>
      {empty ? (
        <p className="chart-no-data lf-chart__empty" role="status">
          {empty}
        </p>
      ) : (
        children
      )}
      {caption && <figcaption className="lf-chart__caption">{caption}</figcaption>}
      {!empty && table && table.rows.length > 0 && (
        <details className="lf-chart__table">
          <summary>Data table ({table.rows.length} rows)</summary>
          <div className="table-scroll">
            <table>
              <caption>{table.caption}</caption>
              <thead>
                <tr>
                  {table.columns.map((column) => (
                    <th key={column} scope="col">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row) => (
                  <tr key={row.key}>
                    {row.cells.map((cell, index) =>
                      index === 0 ? (
                        <th key={index} scope="row">
                          {cell}
                        </th>
                      ) : (
                        <td key={index}>{cell}</td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </figure>
  );
}

export type PlotBox = { left: number; right: number; top: number; bottom: number };

/** Horizontal hairline gridlines with compact value labels on the left. */
export function ValueGrid({
  ticks,
  map,
  box,
  width,
  format = formatTick,
}: {
  ticks: number[];
  map: (value: number) => number;
  box: PlotBox;
  width: number;
  format?: (value: number) => string;
}) {
  return (
    <g className="lf-chart__grid" aria-hidden="true">
      {ticks.map((tick) => {
        const y = map(tick);
        return (
          <g key={tick}>
            <line x1={box.left} x2={width - box.right} y1={y} y2={y} />
            <text className="lf-chart__tick" x={box.left - 8} y={y + 4} textAnchor="end">
              {format(tick)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

/** Baseline axis with tick labels along the bottom of the plot. */
export function BottomAxis({
  ticks,
  map,
  box,
  width,
  height,
  label,
  format = formatTick,
}: {
  ticks: number[];
  map: (value: number) => number;
  box: PlotBox;
  width: number;
  height: number;
  label: string;
  format?: (value: number) => string;
}) {
  const y = height - box.bottom;
  return (
    <g aria-hidden="true">
      <line className="lf-chart__axis" x1={box.left} x2={width - box.right} y1={y} y2={y} />
      {ticks.map((tick) => (
        <text className="lf-chart__tick" key={tick} x={map(tick)} y={y + 16} textAnchor="middle">
          {format(tick)}
        </text>
      ))}
      <text
        className="lf-chart__axis-label"
        x={box.left + (width - box.left - box.right) / 2}
        y={height - 6}
        textAnchor="middle"
      >
        {label}
      </text>
    </g>
  );
}

export function LeftAxisLabel({
  box,
  height,
  label,
}: {
  box: PlotBox;
  height: number;
  label: string;
}) {
  return (
    <text
      aria-hidden="true"
      className="lf-chart__axis-label"
      textAnchor="middle"
      transform={`translate(13 ${box.top + (height - box.top - box.bottom) / 2}) rotate(-90)`}
    >
      {label}
    </text>
  );
}

/**
 * In-SVG tooltip anchored at (x, y), flipped to stay inside the plot. Text uses ink
 * tokens; the readout is duplicated in a polite live region by the caller.
 */
export function ChartTooltip({
  x,
  y,
  lines,
  width,
  height,
}: {
  x: number;
  y: number;
  lines: string[];
  width: number;
  height: number;
}) {
  const boxWidth = Math.max(...lines.map((line) => line.length)) * 6.8 + 18;
  const boxHeight = lines.length * 17 + 10;
  const left = x + 12 + boxWidth > width ? x - 12 - boxWidth : x + 12;
  const top = Math.min(Math.max(4, y - boxHeight / 2), height - boxHeight - 4);
  return (
    <g className="lf-chart__tooltip" aria-hidden="true" pointerEvents="none">
      <rect x={Math.max(2, left)} y={top} width={boxWidth} height={boxHeight} rx="6" />
      {lines.map((line, index) => (
        <text
          className={index === 0 ? "lf-chart__tooltip-title" : undefined}
          key={`${index}:${line}`}
          x={Math.max(2, left) + 9}
          y={top + 18 + index * 17}
        >
          {line}
        </text>
      ))}
    </g>
  );
}

/** Screen-reader readout of the mark under the keyboard cursor. */
export function CursorAnnouncement({ text }: { text: string | null }) {
  return (
    <p className="visually-hidden" aria-live="polite">
      {text ?? ""}
    </p>
  );
}
