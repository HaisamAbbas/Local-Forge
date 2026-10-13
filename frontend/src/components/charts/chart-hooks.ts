import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

import { nearestIndex } from "../../lib/chart-scale";

/**
 * Tracks the rendered width of a chart container so the SVG is drawn at real pixel size
 * (text stays legible instead of being scaled with a fixed viewBox). Falls back to
 * `fallback` where ResizeObserver is unavailable (tests, very old browsers).
 */
export function useMeasuredWidth<T extends HTMLElement>(fallback: number) {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = Math.round(entries[0]?.contentRect.width ?? 0);
      if (next > 0) setWidth((current) => (Math.abs(current - next) >= 1 ? next : current));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

/**
 * Shared hover / keyboard cursor for a chart. One tab stop per chart: arrow keys move
 * between marks, Home/End jump, Escape clears. Pointer movement snaps to the nearest
 * mark along `axis` so hit targets are wider than the marks themselves.
 */
export function useChartCursor(positions: number[], axis: "x" | "y", viewSize: number) {
  const [rawActive, setActive] = useState<number | null>(null);
  const count = positions.length;
  const active = rawActive !== null && rawActive < count ? rawActive : null;

  const onKeyDown = useCallback(
    (event: KeyboardEvent<SVGSVGElement>) => {
      if (count === 0) return;
      const forward = axis === "x" ? "ArrowRight" : "ArrowDown";
      const backward = axis === "x" ? "ArrowLeft" : "ArrowUp";
      let next: number | null | undefined;
      if (event.key === forward) next = active === null ? 0 : Math.min(count - 1, active + 1);
      else if (event.key === backward) next = active === null ? count - 1 : Math.max(0, active - 1);
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = count - 1;
      else if (event.key === "Escape") next = null;
      if (next === undefined) return;
      event.preventDefault();
      setActive(next);
    },
    [active, axis, count],
  );

  const onPointerMove = useCallback(
    (event: PointerEvent<SVGSVGElement>) => {
      const rect = event.currentTarget.getBoundingClientRect();
      const extent = axis === "x" ? rect.width : rect.height;
      if (extent <= 0) return;
      const offset = axis === "x" ? event.clientX - rect.left : event.clientY - rect.top;
      setActive(nearestIndex(positions, (offset / extent) * viewSize));
    },
    [axis, positions, viewSize],
  );

  const clear = useCallback(() => setActive(null), []);

  return {
    active,
    svgProps: {
      tabIndex: count > 0 ? 0 : -1,
      onKeyDown,
      onPointerMove,
      onPointerLeave: clear,
      onBlur: clear,
    },
  };
}
