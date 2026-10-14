import type { ReactNode } from "react";

import { glossaryEntry, glossaryHref } from "../lib/learning";

type MetricHelpProps = {
  /** Persisted metric_name, for example `ttft_ms`. */
  metric: string;
  /** Visible link text; defaults to the glossary label. */
  children?: ReactNode;
  className?: string;
};

/**
 * Link from a metric label to its glossary entry on the Learning screen. Unknown metric names
 * render their children (or the raw name) as plain text instead of a dead link.
 */
export function MetricHelp({ metric, children, className }: MetricHelpProps) {
  const entry = glossaryEntry(metric);
  if (!entry) return <span className={className}>{children ?? metric}</span>;
  return (
    <a
      className={className ? `metric-help ${className}` : "metric-help"}
      href={glossaryHref(metric)}
      title={entry.summary}
    >
      {children ?? entry.label}
    </a>
  );
}
