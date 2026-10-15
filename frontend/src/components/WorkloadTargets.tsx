import type { PrefillEstimate, RunTargets, UseCasePreset } from "../lib/api";
import { glossaryHref } from "../lib/learning";
import {
  TARGET_FIELDS,
  targetsToForm,
  type QualityForm,
  type TargetsForm,
} from "../lib/targets-form";

function contextLabel(useCase: UseCasePreset): string {
  if (useCase.uses_builtin_dataset) return "20 built-in questions";
  if (useCase.input_tokens === null) return "Prompt size varies";
  return useCase.input_tokens >= 1000
    ? `~${Math.round(useCase.input_tokens / 1024)}k-token context`
    : `~${useCase.input_tokens}-token prompts`;
}

export function UseCaseCards({
  useCases,
  selected,
  onSelect,
}: {
  useCases: UseCasePreset[];
  selected: string;
  onSelect: (useCase: UseCasePreset) => void;
}) {
  if (useCases.length === 0) return null;
  return (
    <fieldset className="workload-fieldset use-case-picker">
      <legend>What will this setup be used for?</legend>
      <p className="helper-copy">
        Pick a use case to start from realistic request sizes and pass/fail targets. Everything
        stays editable below.
      </p>
      <div className="use-case-cards" role="radiogroup" aria-label="Use case">
        {useCases.map((useCase) => {
          const active = selected === useCase.preset;
          return (
            <button
              aria-checked={active}
              className={`use-case-card${active ? " use-case-card--active" : ""}`}
              key={useCase.preset}
              onClick={() => onSelect(useCase)}
              role="radio"
              type="button"
            >
              <strong>{useCase.title}</strong>
              <span className="use-case-card__shape">
                {contextLabel(useCase)} · {useCase.output_tokens} output tokens
                {useCase.concurrency > 1 ? ` · ${useCase.concurrency} at once` : ""}
              </span>
              <span className="use-case-card__description">{useCase.description}</span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

export function TargetsEditor({
  form,
  errors,
  onChange,
  onReset,
  onClear,
  canReset,
}: {
  form: TargetsForm;
  errors: Partial<Record<keyof TargetsForm, string>>;
  onChange: (key: keyof TargetsForm, value: string) => void;
  onReset: () => void;
  onClear: () => void;
  canReset: boolean;
}) {
  return (
    <fieldset className="workload-fieldset">
      <legend>Targets · is this setup good enough?</legend>
      <p className="helper-copy">
        Each finished run is judged against these limits: pass, fail, or not enough data. Leave a
        box empty for no target. They are saved with the plan.
      </p>
      <div className="workload-grid">
        {TARGET_FIELDS.map((field) => (
          <label className="workload-field" key={field.key}>
            <span>
              {field.label} <em>{field.unit}</em>
            </span>
            <input
              aria-describedby={errors[field.key] ? `error-target-${field.key}` : undefined}
              aria-invalid={Boolean(errors[field.key])}
              aria-label={`${field.label} (${field.unit})`}
              inputMode="decimal"
              min="0"
              max={field.percent ? "100" : undefined}
              onChange={(event) => onChange(field.key, event.target.value)}
              step="any"
              type="number"
              value={form[field.key]}
            />
            <small>{field.help}</small>
            {errors[field.key] && (
              <span className="field-error" id={`error-target-${field.key}`}>
                {errors[field.key]}
              </span>
            )}
          </label>
        ))}
      </div>
      <div className="workload-inline-actions">
        <button
          className="button button--quiet"
          disabled={!canReset}
          onClick={onReset}
          type="button"
        >
          Use the use-case defaults
        </button>
        <button className="button button--quiet" onClick={onClear} type="button">
          Clear all targets
        </button>
      </div>
    </fieldset>
  );
}

export function QualityChecksEditor({
  form,
  onChange,
  exactMatchAvailable,
  exactMatchNote,
}: {
  form: QualityForm;
  onChange: (next: QualityForm) => void;
  exactMatchAvailable: boolean;
  exactMatchNote: string | null;
}) {
  const set = <K extends keyof QualityForm>(key: K, value: QualityForm[K]) =>
    onChange({ ...form, [key]: value });
  return (
    <fieldset className="workload-fieldset">
      <legend>Quality checks · optional</legend>
      <p className="helper-copy">
        Faster can mean worse answers. These checks run on each output on this machine; only pass or
        fail and the reason are stored, never the output, unless you retain content.
      </p>
      <div className="workload-grid">
        <label className="workload-check workload-field--wide">
          <input
            checked={form.exactMatch && exactMatchAvailable}
            disabled={!exactMatchAvailable}
            onChange={(event) => set("exactMatch", event.target.checked)}
            type="checkbox"
          />
          <span>
            Exact match: the answer must equal the dataset&apos;s expected answer (ignoring case,
            spacing, and trailing punctuation).
            {exactMatchNote && <small className="quality-check-note"> {exactMatchNote}</small>}
          </span>
        </label>
        <label className="workload-check">
          <input
            checked={form.validJson}
            onChange={(event) => set("validJson", event.target.checked)}
            type="checkbox"
          />
          <span>Valid JSON output</span>
        </label>
        {form.validJson && (
          <label className="workload-field">
            <span>
              Required JSON keys <em>optional, comma-separated</em>
            </span>
            <input
              aria-label="Required JSON keys"
              onChange={(event) => set("requiredKeys", event.target.value)}
              value={form.requiredKeys}
            />
          </label>
        )}
        <label className="workload-check workload-field--wide">
          <input
            checked={form.stoppedProperly}
            onChange={(event) => set("stoppedProperly", event.target.checked)}
            type="checkbox"
          />
          <span>Stopped properly: the answer finished before hitting the output token limit</span>
        </label>
        <label className="workload-field">
          <span>
            Maximum output length <em>characters, optional</em>
          </span>
          <input
            aria-label="Maximum output length in characters"
            min="1"
            onChange={(event) => set("maxCharacters", event.target.value)}
            type="number"
            value={form.maxCharacters}
          />
        </label>
        <label className="workload-field">
          <span>
            Must contain <em>comma-separated, optional</em>
          </span>
          <input
            aria-label="Output must contain"
            onChange={(event) => set("mustContain", event.target.value)}
            value={form.mustContain}
          />
        </label>
        <label className="workload-field">
          <span>
            Must not contain <em>comma-separated, optional</em>
          </span>
          <input
            aria-label="Output must not contain"
            onChange={(event) => set("mustNotContain", event.target.value)}
            value={form.mustNotContain}
          />
        </label>
      </div>
    </fieldset>
  );
}

function seconds(value: number): string {
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 10) return `${value.toFixed(1)} s`;
  if (value < 120) return `${Math.round(value)} s`;
  return `${(value / 60).toFixed(1)} min`;
}

export function PrefillEstimateNote({
  estimate,
  ttftTargetSeconds,
}: {
  estimate: PrefillEstimate | null;
  ttftTargetSeconds: number | null;
}) {
  if (!estimate) return null;
  if (!estimate.available || estimate.expected_first_token_seconds === null) {
    return (
      <p className="workload-note workload-field--wide prefill-estimate" role="note">
        No earlier run of this engine and model measured prompt processing yet, so the first-token
        time for this context can&apos;t be estimated. Long contexts can take minutes on CPU-only
        machines.
      </p>
    );
  }
  const expected = estimate.expected_first_token_seconds;
  const overTarget = ttftTargetSeconds !== null && expected > ttftTargetSeconds;
  return (
    <p
      className={`workload-field--wide prefill-estimate ${overTarget ? "workload-warning" : "workload-note"}`}
      role="note"
    >
      Expected first token ≈ {seconds(expected)} for this context (from your earlier runs: prompt
      processing at ~{Math.round(estimate.prompt_tokens_per_second ?? 0).toLocaleString()} tokens/s
      over {estimate.run_count} run{estimate.run_count === 1 ? "" : "s"}).
      {overTarget &&
        ` That is above your ${seconds(ttftTargetSeconds)} first-token target; try a smaller context, a GPU engine, or a smaller model.`}{" "}
      <a href={glossaryHref("concept.prefill_and_ttft")}>Why context size matters</a>
    </p>
  );
}

export function TargetsSummary({ targets }: { targets: RunTargets | null }) {
  const rows = TARGET_FIELDS.map((field) => {
    const form = targetsToForm(targets);
    return { field, value: form[field.key] };
  }).filter((row) => row.value !== "");
  return (
    <section className="preflight-card" aria-label="Plan targets">
      <h3>Targets</h3>
      {rows.length === 0 ? (
        <p>No targets. Runs will not get a pass/fail verdict.</p>
      ) : (
        <dl>
          {rows.map(({ field, value }) => (
            <div key={field.key}>
              <dt>{field.label}</dt>
              <dd>
                {value} {field.unit}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
