import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  fetchWorkspaceSettings,
  saveWorkspaceSettings,
  type StorageStatus,
  type WorkspaceSettings,
  type WorkspaceSettingsResponse,
} from "../lib/api";

type SettingsForm = {
  showTestFixtureEngines: boolean;
  defaultRetainPromptOutput: boolean;
  defaultRequestTimeoutSeconds: string;
  defaultWarmupCount: string;
};

type FieldName = keyof WorkspaceSettings;

const TIMEOUT_LIMITS = { min: 1, max: 3600 };
const WARMUP_LIMITS = { min: 0, max: 1000 };

function toForm(settings: WorkspaceSettings): SettingsForm {
  return {
    showTestFixtureEngines: settings.show_test_fixture_engines,
    defaultRetainPromptOutput: settings.default_retain_prompt_output,
    defaultRequestTimeoutSeconds: String(settings.default_request_timeout_seconds),
    defaultWarmupCount: String(settings.default_warmup_count),
  };
}

function sameForm(left: SettingsForm, right: SettingsForm) {
  return (
    left.showTestFixtureEngines === right.showTestFixtureEngines &&
    left.defaultRetainPromptOutput === right.defaultRetainPromptOutput &&
    left.defaultRequestTimeoutSeconds.trim() === right.defaultRequestTimeoutSeconds.trim() &&
    left.defaultWarmupCount.trim() === right.defaultWarmupCount.trim()
  );
}

/** Mirror the API limits so most mistakes are caught before a request is sent. */
function validateSettingsForm(form: SettingsForm): {
  settings: WorkspaceSettings | null;
  errors: Partial<Record<FieldName, string>>;
} {
  const errors: Partial<Record<FieldName, string>> = {};
  const timeoutText = form.defaultRequestTimeoutSeconds.trim();
  const timeout = Number(timeoutText);
  if (
    !timeoutText ||
    !Number.isFinite(timeout) ||
    timeout < TIMEOUT_LIMITS.min ||
    timeout > TIMEOUT_LIMITS.max
  ) {
    errors.default_request_timeout_seconds = `Enter a number of seconds from ${TIMEOUT_LIMITS.min} to ${TIMEOUT_LIMITS.max}.`;
  }
  const warmupText = form.defaultWarmupCount.trim();
  const warmup = Number(warmupText);
  if (
    !warmupText ||
    !Number.isInteger(warmup) ||
    warmup < WARMUP_LIMITS.min ||
    warmup > WARMUP_LIMITS.max
  ) {
    errors.default_warmup_count = `Enter a whole number from ${WARMUP_LIMITS.min} to ${WARMUP_LIMITS.max}.`;
  }
  if (Object.keys(errors).length) return { settings: null, errors };
  return {
    settings: {
      show_test_fixture_engines: form.showTestFixtureEngines,
      default_retain_prompt_output: form.defaultRetainPromptOutput,
      default_request_timeout_seconds: timeout,
      default_warmup_count: warmup,
    },
    errors,
  };
}

function apiFieldMessage(errorType: string) {
  if (errorType.includes("greater") || errorType.includes("less")) {
    return "The local API rejected this value as out of range.";
  }
  if (errorType.includes("int") || errorType.includes("float") || errorType.includes("bool")) {
    return "The local API rejected this value's type.";
  }
  return "The local API rejected this value.";
}

function formatStorageBytes(value: number | null) {
  if (value === null) return "Unknown";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function StoragePanel({ storage }: { storage: StorageStatus }) {
  const usedShare =
    storage.free_bytes !== null && storage.total_bytes
      ? Math.round(((storage.total_bytes - storage.free_bytes) / storage.total_bytes) * 100)
      : null;
  return (
    <section className="panel" aria-labelledby="settings-storage-title">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">LOCAL DATA · READ ONLY</p>
          <h2 id="settings-storage-title">Storage</h2>
        </div>
      </div>
      {storage.low_space === true && (
        <div className="notice notice--warning settings-storage-warning" role="alert">
          Less than {formatStorageBytes(storage.low_space_threshold_bytes)} is free on the volume
          that holds LOCALFORGE data. Runs, dataset imports, and settings changes can fail until
          space is freed.
        </div>
      )}
      <div className="facts-grid">
        <div className="fact-card fact-card--wide">
          <span className="fact-label">Data directory</span>
          <strong className="settings-path">{storage.data_directory}</strong>
          <small>Set LOCALFORGE_DATA_DIR before starting the API to change it.</small>
        </div>
        <div className="fact-card fact-card--wide">
          <span className="fact-label">Database file</span>
          <strong className="settings-path">{storage.database_path}</strong>
        </div>
        <div className="fact-card">
          <span className="fact-label">Free space</span>
          <strong>{formatStorageBytes(storage.free_bytes)}</strong>
          <small>
            {storage.low_space === null
              ? "The volume could not be read."
              : storage.low_space
                ? "Low disk space"
                : "Above the low-space threshold"}
          </small>
        </div>
        <div className="fact-card">
          <span className="fact-label">Volume size</span>
          <strong>{formatStorageBytes(storage.total_bytes)}</strong>
          <small>{usedShare === null ? "Usage unknown" : `${usedShare}% used`}</small>
        </div>
      </div>
    </section>
  );
}

export function SettingsScreen() {
  const [response, setResponse] = useState<WorkspaceSettingsResponse | null>(null);
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const applyResponse = useCallback((value: WorkspaceSettingsResponse) => {
    setResponse(value);
    setForm(toForm(value.settings));
    setLoadError(null);
  }, []);

  const load = useCallback(async () => {
    try {
      applyResponse(await fetchWorkspaceSettings());
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Could not load settings.");
    }
  }, [applyResponse]);

  useEffect(() => {
    let active = true;
    fetchWorkspaceSettings().then(
      (value) => {
        if (active) applyResponse(value);
      },
      (error: unknown) => {
        if (active)
          setLoadError(error instanceof Error ? error.message : "Could not load settings.");
      },
    );
    return () => {
      active = false;
    };
  }, [applyResponse]);

  const update = <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
    setForm((current) => (current ? { ...current, [key]: value } : current));
    setSaved(false);
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form) return;
    setSaveError(null);
    setSaved(false);
    const { settings, errors } = validateSettingsForm(form);
    setFieldErrors(errors);
    if (!settings) return;
    setSaving(true);
    try {
      applyResponse(await saveWorkspaceSettings(settings));
      setSaved(true);
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length) {
        setFieldErrors(
          Object.fromEntries(
            Object.entries(error.fieldErrors).map(([field, type]) => [
              field,
              apiFieldMessage(type),
            ]),
          ),
        );
      }
      setSaveError(error instanceof Error ? error.message : "Could not save settings.");
    } finally {
      setSaving(false);
    }
  };

  if (!response || !form) {
    return (
      <div className="screen-stack">
        <section className="panel" aria-live="polite">
          {loadError ? (
            <>
              <p className="form-error" role="alert">
                {loadError}
              </p>
              <button className="button button--quiet" onClick={() => void load()} type="button">
                Retry
              </button>
            </>
          ) : (
            <p className="helper-copy">Loading settings…</p>
          )}
        </section>
      </div>
    );
  }

  const savedForm = toForm(response.settings);
  const defaultForm = toForm(response.defaults);
  const dirty = !sameForm(form, savedForm);

  return (
    <div className="screen-stack settings-screen">
      <section className="panel" aria-labelledby="settings-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">WORKSPACE PREFERENCES</p>
            <h2 id="settings-title">Settings</h2>
          </div>
          <span className="workload-step">
            {response.saved ? "Saved in the local database" : "Using defaults"}
          </span>
        </div>
        <form className="workload-form" noValidate onSubmit={(event) => void onSubmit(event)}>
          <fieldset className="workload-fieldset">
            <legend>New benchmark defaults</legend>
            <div className="workload-grid">
              <label className="workload-field">
                <span>Request timeout (seconds)</span>
                <input
                  aria-describedby={
                    fieldErrors.default_request_timeout_seconds
                      ? "settings-error-timeout"
                      : undefined
                  }
                  aria-invalid={Boolean(fieldErrors.default_request_timeout_seconds)}
                  inputMode="decimal"
                  max={TIMEOUT_LIMITS.max}
                  min={TIMEOUT_LIMITS.min}
                  onChange={(event) => update("defaultRequestTimeoutSeconds", event.target.value)}
                  step="any"
                  type="number"
                  value={form.defaultRequestTimeoutSeconds}
                />
                <small>Applied to each request of a benchmark plan when the plan is saved.</small>
                {fieldErrors.default_request_timeout_seconds && (
                  <span className="field-error" id="settings-error-timeout">
                    {fieldErrors.default_request_timeout_seconds}
                  </span>
                )}
              </label>
              <label className="workload-field">
                <span>Warmup requests</span>
                <input
                  aria-describedby={
                    fieldErrors.default_warmup_count ? "settings-error-warmup" : undefined
                  }
                  aria-invalid={Boolean(fieldErrors.default_warmup_count)}
                  inputMode="numeric"
                  max={WARMUP_LIMITS.max}
                  min={WARMUP_LIMITS.min}
                  onChange={(event) => update("defaultWarmupCount", event.target.value)}
                  step="1"
                  type="number"
                  value={form.defaultWarmupCount}
                />
                <small>Prefills the workload builder; each workload can still change it.</small>
                {fieldErrors.default_warmup_count && (
                  <span className="field-error" id="settings-error-warmup">
                    {fieldErrors.default_warmup_count}
                  </span>
                )}
              </label>
              <label className="workload-check workload-field--wide">
                <input
                  checked={form.defaultRetainPromptOutput}
                  onChange={(event) => update("defaultRetainPromptOutput", event.target.checked)}
                  type="checkbox"
                />
                <span>
                  Start new plans with prompt and output retention turned on. Retained content is
                  stored in the local database; each plan can still turn it off.
                </span>
              </label>
            </div>
          </fieldset>
          <fieldset className="workload-fieldset">
            <legend>Engines</legend>
            <div className="workload-grid">
              <label className="workload-check workload-field--wide">
                <input
                  checked={form.showTestFixtureEngines}
                  onChange={(event) => update("showTestFixtureEngines", event.target.checked)}
                  type="checkbox"
                />
                <span>
                  Show test fixture engines. The deterministic fake engine does not load a model and
                  its timings are not model-performance measurements. When off, it is hidden from
                  the catalog and plan builder, and plans that use it are blocked.
                </span>
              </label>
            </div>
          </fieldset>
          {saveError && (
            <p className="form-error" role="alert">
              {saveError}
            </p>
          )}
          {saved && !dirty && (
            <p className="success-message" role="status">
              Settings saved.
            </p>
          )}
          <div className="workload-actions settings-actions">
            <button className="button button--primary" disabled={saving || !dirty} type="submit">
              {saving ? "Saving…" : "Save settings"}
            </button>
            <button
              className="button button--quiet"
              disabled={saving || !dirty}
              onClick={() => {
                setForm(savedForm);
                setFieldErrors({});
                setSaveError(null);
              }}
              type="button"
            >
              Discard changes
            </button>
            <button
              className="button button--quiet"
              disabled={saving || sameForm(form, defaultForm)}
              onClick={() => {
                setForm(defaultForm);
                setFieldErrors({});
                setSaveError(null);
                setSaved(false);
              }}
              type="button"
            >
              Reset to defaults
            </button>
          </div>
        </form>
      </section>
      <StoragePanel storage={response.storage} />
    </div>
  );
}
