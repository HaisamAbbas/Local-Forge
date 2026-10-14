import { useEffect, useId, useState, type FormEvent } from "react";

import {
  ApiError,
  cleanupRuns,
  cloneBenchmarkPlan,
  fetchWorkspaceSettings,
  listEngineModels,
  listEngineProfiles,
  startBenchmarkRun,
  type BenchmarkPlan,
  type EngineModel,
  type EngineProfile,
  type PlanCloneChanges,
  type PlanCloneResponse,
  type PreflightIssue,
  type Workload,
} from "../lib/api";
import { visibleEngines } from "../lib/test-fixtures";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The local API request failed.";
}

// ---------------------------------------------------------------------------
// Clean up finished runs
// ---------------------------------------------------------------------------

const AGE_OPTIONS = [
  { value: "", label: "Any age" },
  { value: "7", label: "Older than 7 days" },
  { value: "30", label: "Older than 30 days" },
  { value: "90", label: "Older than 90 days" },
];

type CleanupFilter = { before: string | null; keepLatest: number | null };

/**
 * History-panel control that deletes finished runs by age and/or keeps the newest N.
 * Always previews with a dry run first; the confirm step deletes the same filter.
 */
export function RunCleanupControl({ onCleaned }: { onCleaned: (deletedRunIds: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [ageDays, setAgeDays] = useState("30");
  const [keepLatest, setKeepLatest] = useState("");
  const [preview, setPreview] = useState<{ filter: CleanupFilter; count: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const ids = useId();

  const keepValue = keepLatest.trim() === "" ? null : Number(keepLatest);
  const keepInvalid = keepValue !== null && (!Number.isInteger(keepValue) || keepValue < 0);
  const noFilter = ageDays === "" && keepValue === null;

  function resetPreview() {
    setPreview(null);
    setError(null);
    setResult(null);
  }

  async function runPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (keepInvalid || noFilter) return;
    const filter: CleanupFilter = {
      before: ageDays ? new Date(Date.now() - Number(ageDays) * 86_400_000).toISOString() : null,
      keepLatest: keepValue,
    };
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await cleanupRuns({
        before: filter.before,
        statuses: null,
        keep_latest: filter.keepLatest,
        dry_run: true,
      });
      setPreview({ filter, count: response.count });
    } catch (previewError) {
      setError(errorMessage(previewError));
    } finally {
      setBusy(false);
    }
  }

  async function confirmCleanup() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const response = await cleanupRuns({
        before: preview.filter.before,
        statuses: null,
        keep_latest: preview.filter.keepLatest,
        dry_run: false,
      });
      setPreview(null);
      setResult(
        response.count === 1
          ? "Deleted 1 finished run."
          : `Deleted ${response.count} finished runs.`,
      );
      onCleaned(response.run_ids);
    } catch (cleanupError) {
      setError(errorMessage(cleanupError));
    } finally {
      setBusy(false);
    }
  }

  if (!open)
    return (
      <div className="run-cleanup">
        <button
          aria-expanded="false"
          className="button button--quiet"
          onClick={() => setOpen(true)}
          type="button"
        >
          Clean up
        </button>
        {result && (
          <p className="success-message" role="status">
            {result}
          </p>
        )}
      </div>
    );

  return (
    <form
      aria-label="Clean up finished runs"
      className="run-cleanup run-cleanup--open"
      onSubmit={(event) => void runPreview(event)}
    >
      <p className="helper-copy">
        Deletes finished runs (completed, failed, cancelled, interrupted) with their samples and
        events. Active runs are never touched. Saved reports are kept.
      </p>
      <div className="run-cleanup__fields">
        <label htmlFor={`${ids}-age`}>
          Age
          <select
            id={`${ids}-age`}
            onChange={(event) => {
              setAgeDays(event.target.value);
              resetPreview();
            }}
            value={ageDays}
          >
            {AGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <div className="run-cleanup__field">
          <label htmlFor={`${ids}-keep`}>Keep latest</label>
          <input
            aria-describedby={`${ids}-keep-help`}
            id={`${ids}-keep`}
            inputMode="numeric"
            min={0}
            onChange={(event) => {
              setKeepLatest(event.target.value);
              resetPreview();
            }}
            placeholder="No limit"
            step={1}
            type="number"
            value={keepLatest}
          />
          <small id={`${ids}-keep-help`}>The newest finished runs to always keep.</small>
        </div>
      </div>
      {keepInvalid && (
        <p className="field-error" role="alert">
          Keep latest must be a whole number of 0 or more.
        </p>
      )}
      {noFilter && (
        <p className="helper-copy" role="note">
          Choose an age or a number of runs to keep.
        </p>
      )}
      {error && (
        <p className="notice notice--warning" role="alert">
          {error}
        </p>
      )}
      {result && (
        <p className="success-message" role="status">
          {result}
        </p>
      )}
      {preview ? (
        <div className="run-cancel-confirm" role="alert">
          <p>
            {preview.count === 0
              ? "No finished runs match. Nothing would be deleted."
              : `${preview.count} finished ${preview.count === 1 ? "run" : "runs"} will be deleted permanently.`}
          </p>
          <div className="run-detail-actions">
            {preview.count > 0 && (
              <button
                className="button button--danger"
                disabled={busy}
                onClick={() => void confirmCleanup()}
                type="button"
              >
                {busy
                  ? "Deleting…"
                  : `Delete ${preview.count} ${preview.count === 1 ? "run" : "runs"}`}
              </button>
            )}
            <button
              className="button button--quiet"
              disabled={busy}
              onClick={() => setPreview(null)}
              type="button"
            >
              {preview.count > 0 ? "Keep runs" : "Change filter"}
            </button>
          </div>
        </div>
      ) : (
        <div className="run-detail-actions">
          <button
            className="button button--secondary"
            disabled={busy || keepInvalid || noFilter}
            type="submit"
          >
            {busy ? "Checking…" : "Preview clean up"}
          </button>
          <button
            className="button button--quiet"
            disabled={busy}
            onClick={() => {
              resetPreview();
              setOpen(false);
            }}
            type="button"
          >
            Close
          </button>
        </div>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Repeat with one change (clone a plan, preflight, start)
// ---------------------------------------------------------------------------

export type CloneField = keyof PlanCloneChanges;

const CLONE_FIELDS: Array<{ field: CloneField; label: string; unit?: string }> = [
  { field: "model_artifact_id", label: "Model" },
  { field: "engine_profile_id", label: "Engine profile" },
  { field: "concurrency", label: "Concurrency" },
  { field: "max_output_tokens", label: "Maximum output tokens", unit: "tokens" },
  { field: "temperature", label: "Temperature" },
  { field: "warmup_count", label: "Warmup requests", unit: "requests" },
  { field: "request_count", label: "Measured requests", unit: "requests" },
  { field: "request_timeout_seconds", label: "Request timeout", unit: "s" },
];

const INTEGER_FIELDS = new Set<CloneField>([
  "concurrency",
  "max_output_tokens",
  "warmup_count",
  "request_count",
]);
const MINIMUMS: Partial<Record<CloneField, number>> = {
  concurrency: 1,
  max_output_tokens: 1,
  warmup_count: 0,
  request_count: 1,
  request_timeout_seconds: 1,
  temperature: 0,
};

/** The plan's current value for a cloneable field (plan first, then its workload). */
function currentCloneValue(
  field: CloneField,
  plan: BenchmarkPlan,
  workload: Workload | null,
): string | number | null {
  switch (field) {
    case "model_artifact_id":
      return plan.model_artifact_id;
    case "engine_profile_id":
      return plan.engine_profile_id;
    case "concurrency":
      return plan.concurrency;
    case "max_output_tokens":
      return workload?.max_output_tokens ?? null;
    case "temperature": {
      const value = workload?.sampling_config?.temperature;
      return typeof value === "number" ? value : null;
    }
    case "warmup_count":
      return plan.warmup_requests;
    case "request_count":
      return plan.measured_requests;
    case "request_timeout_seconds":
      return plan.timeout_seconds;
  }
}

function IssueGroup({
  title,
  issues,
  tone,
}: {
  title: string;
  issues: PreflightIssue[];
  tone: "error" | "warning";
}) {
  if (issues.length === 0) return null;
  return (
    <section className={`preflight-card preflight-issues preflight-issues--${tone}`}>
      <h4>
        {title} <span>{issues.length}</span>
      </h4>
      <ul>
        {issues.map((issue) => (
          <li key={issue.code}>
            <strong>{issue.code.replaceAll("_", " ")}:</strong> {issue.message}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Inline form: pick one plan setting, enter its new value, save a copy of the plan, review
 * its preflight and start a run from it. Spec workflow step 8.
 */
export function RepeatWithChangePanel({
  plan,
  workload,
  onClose,
  onStarted,
}: {
  plan: BenchmarkPlan;
  workload: Workload | null;
  onClose: () => void;
  onStarted: (runId: string) => void;
}) {
  const ids = useId();
  const [field, setField] = useState<CloneField>("concurrency");
  const [value, setValue] = useState("");
  const [name, setName] = useState("");
  const [engines, setEngines] = useState<EngineProfile[] | null>(null);
  const [models, setModels] = useState<{ engineId: string; items: EngineModel[] } | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [clone, setClone] = useState<PlanCloneResponse | null>(null);
  const [egressAcknowledged, setEgressAcknowledged] = useState(false);
  const [contentionAcknowledged, setContentionAcknowledged] = useState(false);
  const [contentionOverrideNeeded, setContentionOverrideNeeded] = useState(false);

  const current = currentCloneValue(field, plan, workload);
  const fieldInfo = CLONE_FIELDS.find((item) => item.field === field)!;

  useEffect(() => {
    let active = true;
    void Promise.all([listEngineProfiles(), fetchWorkspaceSettings().catch(() => null)]).then(
      ([items, settings]) => {
        if (!active) return;
        const visible = visibleEngines(items, settings);
        const own = items.find((engine) => engine.id === plan.engine_profile_id);
        setEngines(own && !visible.includes(own) ? [own, ...visible] : visible);
      },
      (loadError: unknown) => {
        if (active) setLookupError(errorMessage(loadError));
      },
    );
    return () => {
      active = false;
    };
  }, [plan.engine_profile_id]);

  useEffect(() => {
    if (field !== "model_artifact_id") return;
    let active = true;
    listEngineModels(plan.engine_profile_id).then(
      (items) => {
        if (active) setModels({ engineId: plan.engine_profile_id, items });
      },
      (loadError: unknown) => {
        if (active) setLookupError(errorMessage(loadError));
      },
    );
    return () => {
      active = false;
    };
  }, [field, plan.engine_profile_id]);

  const engineName = (id: string | null) =>
    id ? (engines?.find((engine) => engine.id === id)?.label ?? `Engine ${id.slice(0, 8)}`) : "";
  const modelName = (id: string | null) =>
    id
      ? (models?.items.find((model) => model.id === id)?.display_name ?? `Model ${id.slice(0, 8)}`)
      : "None selected";

  const currentText =
    field === "model_artifact_id"
      ? modelName(current as string | null)
      : field === "engine_profile_id"
        ? engineName(current as string | null)
        : field === "temperature" && current === null
          ? "Engine default"
          : field === "request_count" && current === null
            ? "Duration-based plan"
            : current === null
              ? "Not recorded"
              : `${current}${fieldInfo.unit ? ` ${fieldInfo.unit}` : ""}`;

  function chooseField(next: CloneField) {
    setField(next);
    setValue("");
    setFieldError(null);
    setError(null);
  }

  function parseChange(): PlanCloneChanges | null {
    const raw = value.trim();
    if (field === "model_artifact_id" || field === "engine_profile_id") {
      if (!raw) {
        setFieldError(`Choose a ${fieldInfo.label.toLowerCase()}.`);
        return null;
      }
      if (raw === current) {
        setFieldError("Choose a different value from the current one.");
        return null;
      }
      return { [field]: raw };
    }
    if (field === "temperature" && raw === "") return { temperature: null };
    const number = Number(raw);
    const minimum = MINIMUMS[field] ?? 0;
    if (raw === "" || !Number.isFinite(number)) {
      setFieldError("Enter a number.");
      return null;
    }
    if (INTEGER_FIELDS.has(field) && !Number.isInteger(number)) {
      setFieldError("Enter a whole number.");
      return null;
    }
    if (number < minimum) {
      setFieldError(`Enter ${minimum} or more.`);
      return null;
    }
    if (number === current) {
      setFieldError("Enter a different value from the current one.");
      return null;
    }
    return { [field]: number };
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFieldError(null);
    setError(null);
    const changes = parseChange();
    if (!changes) return;
    setBusy(true);
    try {
      const response = await cloneBenchmarkPlan(plan.id, {
        name: name.trim() || null,
        changes,
      });
      setClone(response);
      setEgressAcknowledged(false);
      setContentionAcknowledged(false);
      setContentionOverrideNeeded(false);
    } catch (saveError) {
      if (saveError instanceof ApiError && Object.keys(saveError.fieldErrors).length > 0) {
        setFieldError(Object.values(saveError.fieldErrors)[0] ?? saveError.message);
      } else setError(errorMessage(saveError));
    } finally {
      setBusy(false);
    }
  }

  const preflight = clone?.preflight ?? null;
  const contention = Boolean(preflight?.resource_contention.length || contentionOverrideNeeded);
  const ready = Boolean(
    clone && clone.plan.status === "ready" && preflight?.valid && preflight.blockers.length === 0,
  );
  const canStart =
    ready &&
    (!preflight?.remote_data_egress || egressAcknowledged) &&
    (!contention || contentionAcknowledged);

  async function start() {
    if (!clone || !canStart) return;
    setBusy(true);
    setError(null);
    try {
      const result = await startBenchmarkRun(clone.plan.id, contention);
      onStarted(result.run.id);
    } catch (startError) {
      if (startError instanceof ApiError && startError.code === "resource_lock_conflict") {
        setContentionOverrideNeeded(true);
        setContentionAcknowledged(false);
        setError(
          "This target is already running another benchmark. Acknowledge the contention to start anyway.",
        );
      } else setError(errorMessage(startError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="run-subsection run-repeat" aria-labelledby={`${ids}-title`}>
      <div className="section-title-row">
        <div>
          <h3 id={`${ids}-title`}>Repeat with one change</h3>
          <p className="helper-copy">
            Saves a copy of “{plan.name}” with one setting changed, so the two runs differ in
            exactly one way.
          </p>
        </div>
        <button className="button button--quiet" disabled={busy} onClick={onClose} type="button">
          Close
        </button>
      </div>
      {!clone ? (
        <form className="run-repeat__form" onSubmit={(event) => void save(event)}>
          <label htmlFor={`${ids}-field`}>
            Setting to change
            <select
              id={`${ids}-field`}
              onChange={(event) => chooseField(event.target.value as CloneField)}
              value={field}
            >
              {CLONE_FIELDS.map((item) => (
                <option key={item.field} value={item.field}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <p className="run-repeat__current">
            Current value: <strong>{currentText}</strong>
          </p>
          {field === "model_artifact_id" ? (
            <div className="run-repeat__field">
              <label htmlFor={`${ids}-value`}>New model</label>
              <select
                aria-describedby={fieldError ? `${ids}-error` : undefined}
                aria-invalid={Boolean(fieldError)}
                id={`${ids}-value`}
                onChange={(event) => setValue(event.target.value)}
                value={value}
              >
                <option value="">
                  {models ? "Choose a model" : "Loading models from this engine…"}
                </option>
                {models?.items.map((model) => (
                  <option disabled={model.id === current} key={model.id} value={model.id}>
                    {model.display_name}
                    {model.revision ? ` · ${model.revision}` : ""}
                    {model.id === current ? " (current)" : ""}
                  </option>
                ))}
              </select>
              {models && models.items.length === 0 && (
                <small>This engine reports no saved models. Probe it in Engines first.</small>
              )}
            </div>
          ) : field === "engine_profile_id" ? (
            <div className="run-repeat__field">
              <label htmlFor={`${ids}-value`}>New engine profile</label>
              <select
                aria-describedby={fieldError ? `${ids}-error` : undefined}
                aria-invalid={Boolean(fieldError)}
                id={`${ids}-value`}
                onChange={(event) => setValue(event.target.value)}
                value={value}
              >
                <option value="">{engines ? "Choose an engine" : "Loading engines…"}</option>
                {engines?.map((engine) => (
                  <option disabled={engine.id === current} key={engine.id} value={engine.id}>
                    {engine.label}
                    {engine.id === current ? " (current)" : ""}
                  </option>
                ))}
              </select>
              <small>
                The model setting is kept; preflight reports if this engine cannot serve it.
              </small>
            </div>
          ) : (
            <label htmlFor={`${ids}-value`}>
              New {fieldInfo.label.toLowerCase()}
              {fieldInfo.unit ? ` (${fieldInfo.unit})` : ""}
              <input
                aria-describedby={fieldError ? `${ids}-error` : undefined}
                aria-invalid={Boolean(fieldError)}
                id={`${ids}-value`}
                inputMode="decimal"
                min={MINIMUMS[field]}
                onChange={(event) => setValue(event.target.value)}
                placeholder={field === "temperature" ? "Blank = engine default" : undefined}
                step={INTEGER_FIELDS.has(field) ? 1 : "any"}
                type="number"
                value={value}
              />
            </label>
          )}
          {fieldError && (
            <p className="field-error" id={`${ids}-error`} role="alert">
              {fieldError}
            </p>
          )}
          <label htmlFor={`${ids}-name`}>
            Name for the new plan (optional)
            <input
              id={`${ids}-name`}
              maxLength={160}
              onChange={(event) => setName(event.target.value)}
              placeholder="Generated from the change when blank"
              value={name}
            />
          </label>
          {lookupError && (
            <p className="notice notice--warning" role="alert">
              {lookupError}
            </p>
          )}
          {error && (
            <p className="notice notice--warning" role="alert">
              {error}
            </p>
          )}
          <div className="run-detail-actions">
            <button className="button button--primary" disabled={busy} type="submit">
              {busy ? "Saving plan…" : "Save changed plan"}
            </button>
          </div>
        </form>
      ) : (
        <div className="run-repeat__result" role="status">
          <p>
            Saved <strong>{clone.plan.name}</strong>
            {clone.changed_fields.length > 0
              ? ` · changed ${clone.changed_fields.join(", ")}`
              : " · no setting differs from the original"}
            . Plan status: {clone.plan.status}.
          </p>
          {preflight && (
            <>
              <IssueGroup issues={preflight.blockers} title="Blockers" tone="error" />
              <IssueGroup issues={preflight.warnings} title="Warnings" tone="warning" />
              {preflight.blockers.length === 0 && preflight.warnings.length === 0 && (
                <p className="helper-copy">Preflight has no blockers or warnings.</p>
              )}
              {preflight.remote_data_egress && (
                <label className="workload-check">
                  <input
                    checked={egressAcknowledged}
                    onChange={(event) => setEgressAcknowledged(event.target.checked)}
                    type="checkbox"
                  />
                  <span>
                    {preflight.remote_data_egress.message} I approve sending this workload to{" "}
                    {preflight.remote_data_egress.host}.
                  </span>
                </label>
              )}
              {contention && (
                <label className="workload-check">
                  <input
                    checked={contentionAcknowledged}
                    onChange={(event) => setContentionAcknowledged(event.target.checked)}
                    type="checkbox"
                  />
                  <span>
                    I accept that another run may contend for this target and want a potentially
                    contended run.
                  </span>
                </label>
              )}
            </>
          )}
          {error && (
            <p className="notice notice--warning" role="alert">
              {error}
            </p>
          )}
          <div className="run-detail-actions">
            {ready && (
              <button
                className="button button--primary"
                disabled={busy || !canStart}
                onClick={() => void start()}
                type="button"
              >
                {busy ? "Starting run…" : "Start run"}
              </button>
            )}
            <button
              className="button button--quiet"
              disabled={busy}
              onClick={() => {
                setClone(null);
                setError(null);
              }}
              type="button"
            >
              Make another change
            </button>
          </div>
          {!ready && (
            <p className="helper-copy">
              Resolve the blockers above (for example in Workloads or Engines) before starting a run
              from this plan.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
