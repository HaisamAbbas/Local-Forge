/**
 * Pure scale, tick and number-formatting helpers shared by the hand-rolled SVG charts.
 * Nothing here invents data: helpers only map observed values onto pixels and labels.
 */

export type LinearScale = {
  domain: [number, number];
  range: [number, number];
  map: (value: number) => number;
  ticks: number[];
};

/** Round a raw step to 1, 2, 2.5 or 5 × 10^n so axis ticks read as clean numbers. */
export function niceStep(rawStep: number): number {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawStep));
  const magnitude = 10 ** exponent;
  const fraction = rawStep / magnitude;
  const nice =
    fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return nice * magnitude;
}

/**
 * Ticks from `min` to a rounded-up maximum. When `zeroBased` the scale starts at zero
 * (bars and magnitudes must); otherwise the lower bound is rounded down to a clean step.
 */
export function niceTicks(min: number, max: number, count = 4, zeroBased = true): number[] {
  let low = zeroBased ? Math.min(0, min) : min;
  let high = zeroBased ? Math.max(0, max) : max;
  if (!Number.isFinite(low) || !Number.isFinite(high)) return [0, 1];
  if (high === low) {
    // A flat series still needs a visible axis span; pad around the single observed value.
    const pad = high === 0 ? 1 : Math.abs(high) * 0.1;
    if (zeroBased) high = low + pad;
    else {
      low -= pad;
      high += pad;
    }
  }
  const step = niceStep((high - low) / count);
  const start = Math.floor(low / step) * step;
  const end = Math.ceil(high / step) * step;
  return buildTicks(start, end, step);
}

function buildTicks(start: number, end: number, step: number): number[] {
  const ticks: number[] = [];
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  for (let value = start; value <= end + step / 2; value += step) {
    ticks.push(Number(value.toFixed(decimals)));
    if (ticks.length > 50) break;
  }
  return ticks;
}

export function linearScale(
  values: number[],
  range: [number, number],
  options: { zeroBased?: boolean; tickCount?: number; minimum?: number } = {},
): LinearScale {
  const zeroBased = options.zeroBased ?? true;
  const finite = values.filter((value) => Number.isFinite(value));
  const dataMin = finite.length ? Math.min(...finite) : 0;
  const dataMax = finite.length ? Math.max(...finite) : 1;
  const ticks = niceTicks(
    options.minimum === undefined ? dataMin : Math.min(options.minimum, dataMin),
    dataMax,
    options.tickCount ?? 4,
    zeroBased,
  );
  const low = ticks[0] ?? 0;
  const high = ticks[ticks.length - 1] ?? 1;
  const span = high - low || 1;
  return {
    domain: [low, high],
    range,
    ticks,
    map: (value: number) => range[0] + ((value - low) / span) * (range[1] - range[0]),
  };
}

/** Compact axis label: 0 / 950 / 1.2K / 4.5M with no trailing zeros. */
export function formatTick(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `${trim(value / 1_000_000_000)}B`;
  if (absolute >= 1_000_000) return `${trim(value / 1_000_000)}M`;
  if (absolute >= 1_000) return `${trim(value / 1_000)}K`;
  if (absolute === 0) return "0";
  if (absolute < 1) return trim(value, 3);
  return trim(value);
}

function trim(value: number, digits = 2): string {
  return Number(value.toFixed(digits)).toString();
}

/** Full-precision readout for tooltips and tables (never compacted). */
export function formatValue(value: number | null | undefined, unit = ""): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Unavailable";
  const digits = Math.abs(value) >= 100 ? 1 : Math.abs(value) >= 1 ? 2 : 3;
  const text = value.toLocaleString(undefined, { maximumFractionDigits: digits });
  return unit ? `${text} ${unit}` : text;
}

/** Index of the position closest to `coordinate` (pointer hit-testing); null when empty. */
export function nearestIndex(positions: number[], coordinate: number): number | null {
  let best: number | null = null;
  let bestDistance = Infinity;
  positions.forEach((position, index) => {
    const distance = Math.abs(position - coordinate);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

/** Integer ticks for request-number axes (never fractional request positions). */
export function integerTicks(min: number, max: number, count = 6): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  const low = Math.ceil(min);
  const high = Math.floor(max);
  if (high < low) return [];
  const step = Math.max(1, Math.ceil(niceStep((high - low) / count)));
  const ticks: number[] = [];
  for (let value = Math.ceil(low / step) * step; value <= high; value += step) ticks.push(value);
  if (ticks.length === 0) ticks.push(low);
  return ticks;
}

/**
 * SVG path for a bar with a rounded data end and a square baseline end.
 * `up`: column growing upward from y + height. `right`: bar growing rightward from x.
 */
export function roundedBarPath(
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
  direction: "up" | "right",
): string {
  if (width <= 0 || height <= 0) return "";
  if (direction === "up") {
    const r = Math.max(0, Math.min(radius, width / 2, height));
    return [
      `M${x},${y + height}`,
      `V${y + r}`,
      `Q${x},${y} ${x + r},${y}`,
      `H${x + width - r}`,
      `Q${x + width},${y} ${x + width},${y + r}`,
      `V${y + height}`,
      "Z",
    ].join(" ");
  }
  const r = Math.max(0, Math.min(radius, height / 2, width));
  return [
    `M${x},${y}`,
    `H${x + width - r}`,
    `Q${x + width},${y} ${x + width},${y + r}`,
    `V${y + height - r}`,
    `Q${x + width},${y + height} ${x + width - r},${y + height}`,
    `H${x}`,
    "Z",
  ].join(" ");
}
