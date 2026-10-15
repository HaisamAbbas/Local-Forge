import { useEffect, useState } from "react";

import {
  fetchRunVerdict,
  type RunVerdict as RunVerdictData,
  type TargetEvaluation,
} from "../lib/api";
import { glossaryHref } from "../lib/learning";

const STATUS_LABEL: Record<TargetEvaluation["status"], string> = {
  pass: "Pass",
  fail: "Miss",
  unknown: "Not enough data",
};

function toneFor(overall: RunVerdictData["overall"]): string {
  if (overall === "meets_targets") return "pass";
  if (overall === "misses_targets") return "fail";
  return "unknown";
}

function useVerdict(runId: string, refreshKey: string) {
  const [state, setState] = useState<{ key: string; verdict: RunVerdictData | null }>({
    key: "",
    verdict: null,
  });
  const key = `${runId}:${refreshKey}`;
  useEffect(() => {
    let active = true;
    fetchRunVerdict(runId).then(
      (verdict) => {
        if (active) setState({ key, verdict });
      },
      () => {
        // The verdict is an optional summary; the rest of the run detail stays usable without it.
        if (active) setState({ key, verdict: null });
      },
    );
    return () => {
      active = false;
    };
  }, [runId, key]);
  return state.key === key ? state.verdict : null;
}

/** "Is this setup good enough?" — the run judged against the targets saved on its plan. */
export function RunVerdict({ runId, runStatus }: { runId: string; runStatus: string }) {
  const verdict = useVerdict(runId, runStatus);
  if (!verdict) return null;
  if (verdict.overall === "no_targets") {
    return (
      <section className="run-verdict run-verdict--none" aria-label="Verdict">
        <div className="run-verdict__header">
          <p className="eyebrow">VERDICT</p>
          <h3>No targets set</h3>
        </div>
        <p className="helper-copy">{verdict.summary}</p>
      </section>
    );
  }
  const tone = toneFor(verdict.overall);
  return (
    <section className={`run-verdict run-verdict--${tone}`} aria-label="Verdict">
      <div className="run-verdict__header">
        <p className="eyebrow">VERDICT · YOUR TARGETS</p>
        <h3>{verdict.headline}</h3>
        <p className="helper-copy">
          {verdict.summary}{" "}
          <a href={glossaryHref("concept.targets_and_verdicts")}>How verdicts work</a>
        </p>
      </div>
      <ul className="run-verdict__targets">
        {verdict.evaluations.map((item) => (
          <li
            className={`run-verdict__target run-verdict__target--${item.status}`}
            key={item.target}
          >
            <span className={`verdict-chip verdict-chip--${item.status}`}>
              {STATUS_LABEL[item.status]}
            </span>
            <div>
              <strong>{item.label}</strong>
              <p>{item.explanation}</p>
            </div>
          </li>
        ))}
      </ul>
      {verdict.quality_method_version && (
        <p className="helper-copy run-verdict__method">
          Quality checks: {verdict.quality_method_version}. Verdict method: {verdict.method_version}
          .
        </p>
      )}
    </section>
  );
}

/** Compact verdict for comparison headers; renders nothing when unavailable or untargeted. */
export function RunVerdictBadge({ runId }: { runId: string }) {
  const verdict = useVerdict(runId, "badge");
  if (!verdict || verdict.overall === "no_targets") return null;
  const tone = toneFor(verdict.overall);
  return (
    <small className={`verdict-chip verdict-chip--${tone} verdict-badge`} title={verdict.summary}>
      {verdict.headline}
    </small>
  );
}
