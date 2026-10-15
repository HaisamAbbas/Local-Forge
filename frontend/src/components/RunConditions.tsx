import type {
  BenchmarkRun,
  RepeatSpreadMetric,
  RunComparison,
  RunComparisonWithConditions,
} from "../lib/api";
import { conditionConcept } from "../lib/learning";
import {
  RUNTIME_SETTING_LABELS,
  conditionDimensions,
  conditionExplanation,
  conditionStateLabel,
  describeIdentity,
  describePower,
  formatCv,
  formatSettingSource,
  formatSettingValue,
  runConditions,
  shortHash,
} from "../lib/run-conditions";

/** Run detail card: the engine settings, weights, and power state this run measured under. */
export function RunConditionsCard({ run }: { run: BenchmarkRun }) {
  const conditions = runConditions(run);
  const settings = conditions.runtime_settings;
  const identity = conditions.model_identity;
  const power = conditions.power_state;
  const cache = conditions.prompt_cache;
  const captured = Boolean(settings || identity || power);

  return (
    <section className="run-subsection run-conditions" aria-labelledby="run-conditions-title">
      <div className="section-title-row">
        <div>
          <h3 id="run-conditions-title">Run conditions</h3>
          <p className="helper-copy">
            What the engine and machine were actually set to. Values no source reported are shown as
            not reported; LOCALFORGE never fills in engine defaults.
          </p>
        </div>
      </div>
      {!captured ? (
        <p className="empty-inline">This run was recorded before run conditions were captured.</p>
      ) : (
        <>
          {cache?.code === "prompt_cache_likely" && (
            <p className="notice notice--warning run-conditions-cache" role="status">
              Prompt cache likely: {cache.message ?? "some requests reused a cached prompt."}
            </p>
          )}
          <div className="run-conditions-grid">
            <div className="run-conditions-block">
              <h4 title={conditionConcept("runtime_settings")?.summary}>Engine runtime settings</h4>
              {settings?.status === "unavailable" || !settings ? (
                <p className="empty-inline">Runtime settings could not be read.</p>
              ) : (
                <table className="run-conditions-table">
                  <tbody>
                    {RUNTIME_SETTING_LABELS.map(([field, label]) => {
                      const entry = settings.fields[field];
                      const known = entry && entry.value !== null && entry.value !== undefined;
                      return (
                        <tr key={field} className={known ? undefined : "run-conditions-unknown"}>
                          <th scope="row">{label}</th>
                          <td>{formatSettingValue(field, entry?.value)}</td>
                          <td>
                            <small>{formatSettingSource(entry)}</small>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {settings?.notes?.map((note) => (
                <p className="helper-copy" key={note}>
                  {note}
                </p>
              ))}
            </div>
            <div className="run-conditions-block">
              <h4 title={conditionConcept("model_weights")?.summary}>Model weights</h4>
              <dl className="run-provenance-grid">
                <div>
                  <dt>SHA-256</dt>
                  <dd title={identity?.sha256 ?? undefined}>
                    <code>{shortHash(identity?.sha256)}</code>
                  </dd>
                </div>
                <div>
                  <dt>Basis</dt>
                  <dd>{describeIdentity(identity)}</dd>
                </div>
                {identity?.file_name && (
                  <div>
                    <dt>File</dt>
                    <dd>{identity.file_name}</dd>
                  </div>
                )}
                {identity?.size_bytes ? (
                  <div>
                    <dt>Size</dt>
                    <dd>{(identity.size_bytes / 1024 ** 2).toFixed(1)} MiB</dd>
                  </div>
                ) : null}
              </dl>
              <h4 title={conditionConcept("power_state")?.summary}>Power state</h4>
              <dl className="run-provenance-grid">
                <div>
                  <dt>At start</dt>
                  <dd>{describePower(power?.start)}</dd>
                </div>
                <div>
                  <dt>At end</dt>
                  <dd>{power?.end ? describePower(power.end) : "Not captured"}</dd>
                </div>
              </dl>
              {power?.changed_during_run && (
                <p className="notice notice--warning" role="status">
                  Power changed during this run ({power.changed_fields.join(", ")}); its speed
                  numbers mix two power states.
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}

/** Comparison summary for runtime settings, model weights, and power, in plain language. */
export function ComparisonConditions({ comparison }: { comparison: RunComparison }) {
  const dimensions = conditionDimensions(comparison);
  const confounders = (comparison as RunComparisonWithConditions).confounders ?? [];
  if (!dimensions.length) return null;
  return (
    <section className="comparison-conditions" aria-label="Run conditions compared">
      {confounders.length > 0 && (
        <ul className="comparison-confounders">
          {confounders.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      <div className="comparison-conditions-grid">
        {dimensions.map((dimension) => (
          <div
            className={`comparison-condition comparison-condition--${dimension.state}`}
            key={dimension.key}
          >
            <span title={conditionConcept(dimension.key)?.summary}>{dimension.label}</span>
            <strong>{conditionStateLabel(dimension)}</strong>
            <small>{conditionExplanation(dimension)}</small>
            {dimension.key === "model_weights" && dimension.digests && (
              <small>{dimension.digests.map((digest) => shortHash(digest)).join(" · ")}</small>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** CV and an "unstable" flag for a repeated metric (CV above 10%). */
export function SpreadBadge({ metric }: { metric: RepeatSpreadMetric | undefined }) {
  if (!metric || metric.repeat_count < 2) return null;
  return (
    <small
      className={metric.unstable ? "spread-badge spread-badge--unstable" : "spread-badge"}
      title={conditionConcept("repeat_spread")?.summary}
    >
      {formatCv(metric.cv)}
      {metric.unstable ? " · unstable" : ""}
    </small>
  );
}
