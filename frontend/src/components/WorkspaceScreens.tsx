import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { IconApi, IconTerminal2 } from "@tabler/icons-react";

import {
  ApiError,
  createEngineProfile,
  deleteEngineProfile,
  fetchSystemProfile,
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  listRuns,
  probeEngineProfile,
  saveCurrentMachineProfile,
  startEngineProfile,
  stopEngineProfile,
  updateEngineProfile,
  type AdapterManifest,
  type AdapterModel,
  type CapabilityValue,
  type EngineModel,
  type EngineMode,
  type EngineProfile,
  type EngineProfileInput,
  type MachineProfile,
  type ProbeResult,
} from "../lib/api";

type Resource<T> =
  { status: "loading" } | { status: "error"; message: string } | { status: "ready"; value: T };

const DIALOG_FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function AccessibleDialog({
  titleId,
  className,
  children,
}: {
  titleId: string;
  className: string;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    const initialFocus =
      dialog?.querySelector<HTMLElement>("[data-initial-focus]") ??
      dialog?.querySelector<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR);
    initialFocus?.focus();
    return () => {
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const dialog = event.currentTarget;
    if (event.key === "Escape") {
      event.preventDefault();
      dialog.querySelector<HTMLButtonElement>('[aria-label="Close dialog"]')?.click();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE_SELECTOR),
    ).filter(
      (element) =>
        !element.closest("details:not([open])") &&
        !element.closest("[hidden]") &&
        element.getAttribute("aria-hidden") !== "true",
    );
    if (focusable.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    const activeElement = document.activeElement;
    if (event.shiftKey && (activeElement === first || !dialog.contains(activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (activeElement === last || !dialog.contains(activeElement))) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <section
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-modal="true"
      className={className}
      onKeyDown={onKeyDown}
      role="dialog"
    >
      {children}
    </section>
  );
}

function useResource<T>(load: () => Promise<T>) {
  const [resource, setResource] = useState<Resource<T>>({ status: "loading" });
  const reload = useCallback(async () => {
    setResource({ status: "loading" });
    try {
      setResource({ status: "ready", value: await load() });
    } catch (error) {
      setResource({
        status: "error",
        message: error instanceof Error ? error.message : "The local API request failed.",
      });
    }
  }, [load]);
  useEffect(() => {
    let active = true;
    load().then(
      (value) => {
        if (active) setResource({ status: "ready", value });
      },
      (error: unknown) => {
        if (active) {
          setResource({
            status: "error",
            message: error instanceof Error ? error.message : "The local API request failed.",
          });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [load]);
  return { resource, reload };
}

function ResourcePanel<T>({
  resource,
  onRetry,
  loadingLabel,
  children,
}: {
  resource: Resource<T>;
  onRetry: () => void;
  loadingLabel: string;
  children: (value: T) => ReactNode;
}) {
  if (resource.status === "loading") {
    return (
      <div className="resource-skeleton">
        <span className="visually-hidden" role="status" aria-busy="true">
          {loadingLabel}
        </span>
        <span aria-hidden="true" />
        <span aria-hidden="true" />
      </div>
    );
  }
  if (resource.status === "error") {
    return (
      <div className="state-message state-message--error" role="alert">
        <span>{resource.message}</span>
        <button className="button button--quiet" onClick={onRetry} type="button">
          Retry
        </button>
      </div>
    );
  }
  return <>{children(resource.value)}</>;
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "Unknown";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
}

function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusLabel(value: string): string {
  return titleCase(value);
}

function capabilityLabel(value: CapabilityValue | undefined): string {
  return value ? titleCase(value.state) : "Unknown";
}

function MachineFacts({ machine }: { machine: MachineProfile }) {
  const hardware = machine.hardware;
  const facts: Array<[string, string | number | null | undefined]> = [
    ["Operating system", hardware?.os_name],
    ["OS version", hardware?.os_version],
    ["Architecture", hardware?.architecture],
    ["Processor", hardware?.cpu_model],
    ["Logical cores", hardware?.cpu_logical_cores],
    [
      "Memory",
      hardware?.memory_total_bytes == null ? null : formatBytes(hardware.memory_total_bytes),
    ],
  ];
  return (
    <div className="facts-grid">
      {facts.map(([label, rawValue]) => {
        const value =
          rawValue === null || rawValue === undefined || rawValue === ""
            ? "Unknown / not detected"
            : String(rawValue);
        const provenance = machine.provenance[labelToField(label)];
        return (
          <div className="fact-card" key={label}>
            <span className="fact-label">{label}</span>
            <strong>{value}</strong>
            <small>
              {provenance
                ? `${titleCase(provenance.source)}${provenance.observed_at ? ` · ${formatDate(provenance.observed_at)}` : ""}`
                : "Source unknown · unverified"}
            </small>
          </div>
        );
      })}
      <div className="fact-card fact-card--wide">
        <span className="fact-label">Accelerators</span>
        {hardware?.accelerators === null || hardware?.accelerators === undefined ? (
          <strong>Unknown / not detected</strong>
        ) : hardware.accelerators.length === 0 ? (
          <strong>No accelerators reported</strong>
        ) : (
          <ul className="accelerator-list">
            {hardware.accelerators.map((accelerator, index) => (
              <li key={`${accelerator.name}-${index}`}>
                <strong>{accelerator.name}</strong>
                <span>
                  {[
                    accelerator.vendor,
                    accelerator.runtime,
                    accelerator.driver_version,
                    accelerator.memory_bytes === null
                      ? null
                      : formatBytes(accelerator.memory_bytes),
                  ]
                    .filter(Boolean)
                    .join(" · ") || "Details unknown"}
                </span>
              </li>
            ))}
          </ul>
        )}
        <small>
          {machine.provenance.accelerators
            ? `${titleCase(machine.provenance.accelerators.source)} · ${formatDate(machine.provenance.accelerators.observed_at)}`
            : "Source unknown · unverified"}
        </small>
      </div>
    </div>
  );
}

function labelToField(label: string): string {
  return (
    (
      {
        "Operating system": "os_name",
        "OS version": "os_version",
        Architecture: "architecture",
        Processor: "cpu_model",
        "Logical cores": "cpu_logical_cores",
        Memory: "memory_total_bytes",
      } as Record<string, string>
    )[label] ?? ""
  );
}

export function OverviewScreen() {
  const machines = useResource(fetchSystemProfile);
  const runs = useResource(listRuns);
  const engines = useResource(listEngineProfiles);
  const machine = machines.resource.status === "ready" ? machines.resource.value : null;
  const latestRun = runs.resource.status === "ready" ? runs.resource.value[0] : null;
  return (
    <div className="screen-stack">
      <section className="welcome-panel overview-intro" aria-labelledby="welcome-title">
        <div className="welcome-heading">
          <div className="welcome-copy">
            <p className="eyebrow">LOCAL INFERENCE WORKSPACE</p>
            <h2 id="welcome-title">Benchmark the model running on your machine.</h2>
            <p>
              Connect an inference engine, choose a workload, and inspect measured results. Hardware
              and engine capabilities remain clearly labeled when unknown.
            </p>
          </div>
          <a className="button button--primary overview-action" href="#engines">
            Add an engine
          </a>
        </div>
        <ol className="overview-workflow" aria-label="Benchmark workflow">
          <li className="overview-workflow__step overview-workflow__step--current">
            <span className="overview-workflow__number" aria-hidden="true">
              01
            </span>
            <div>
              <a href="#engines">Connect an engine</a>
              <p>Choose the inference endpoint to evaluate.</p>
            </div>
          </li>
          <li className="overview-workflow__step">
            <span className="overview-workflow__number" aria-hidden="true">
              02
            </span>
            <div>
              <a href="#workloads">Prepare a workload</a>
              <p>Set up a repeatable request pattern.</p>
            </div>
          </li>
          <li className="overview-workflow__step">
            <span className="overview-workflow__number" aria-hidden="true">
              03
            </span>
            <div>
              <a href="#runs">Review measured results</a>
              <p>Compare latency and run outcomes.</p>
            </div>
          </li>
        </ol>
        <div className="overview-summary-grid">
          <section className="summary-card" aria-labelledby="machine-summary-title">
            <div className="section-title-row">
              <h3 id="machine-summary-title">This machine</h3>
              <a href="#system">View system</a>
            </div>
            <ResourcePanel
              resource={machines.resource}
              onRetry={() => void machines.reload()}
              loadingLabel="Reading machine details…"
            >
              {(value) => (
                <>
                  <p className="summary-primary">{value.host_name || value.label}</p>
                  <p className="summary-secondary">
                    {value.hardware?.cpu_model || "Processor unknown"}
                  </p>
                  <span className={`status-pill status-pill--${value.status}`}>
                    {statusLabel(value.status)}
                  </span>
                </>
              )}
            </ResourcePanel>
          </section>
          <section className="summary-card" aria-labelledby="engine-summary-title">
            <div className="section-title-row">
              <h3 id="engine-summary-title">Engine profiles</h3>
              <a href="#engines">Manage engines</a>
            </div>
            <ResourcePanel
              resource={engines.resource}
              onRetry={() => void engines.reload()}
              loadingLabel="Loading engine profiles…"
            >
              {(value) => (
                <>
                  <p className="summary-primary">{value.length}</p>
                  <p className="summary-secondary">
                    {value.filter((engine) => engine.status === "enabled").length} enabled ·{" "}
                    {value.filter((engine) => engine.status === "needs_attention").length} need
                    attention
                  </p>
                </>
              )}
            </ResourcePanel>
          </section>
          <section className="summary-card" aria-labelledby="latest-run-title">
            <div className="section-title-row">
              <h3 id="latest-run-title">Latest run</h3>
              <a href="#runs">Run history</a>
            </div>
            <ResourcePanel
              resource={runs.resource}
              onRetry={() => void runs.reload()}
              loadingLabel="Loading recent runs…"
            >
              {(value) =>
                value.length === 0 ? (
                  <>
                    <p className="summary-primary">No runs recorded</p>
                    <p className="summary-secondary">
                      Measured results will appear here after a run.
                    </p>
                  </>
                ) : (
                  <>
                    <p className="summary-primary">{statusLabel(latestRun?.status ?? "unknown")}</p>
                    <p className="summary-secondary">
                      Run {latestRun?.id.slice(0, 8)} · updated {formatDate(latestRun?.updated_at)}
                    </p>
                    {latestRun?.error_code && (
                      <p className="inline-warning">Last error: {latestRun.error_code}</p>
                    )}
                  </>
                )
              }
            </ResourcePanel>
          </section>
        </div>
      </section>
      {machine && (
        <section className="panel" aria-labelledby="overview-machine-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">SYSTEM SNAPSHOT</p>
              <h2 id="overview-machine-title">Machine details</h2>
            </div>
            <a className="text-link" href="#system">
              All system details
            </a>
          </div>
          <MachineFacts machine={machine} />
        </section>
      )}
      {runs.resource.status === "ready" && runs.resource.value.length === 0 && (
        <section className="empty-state panel" aria-labelledby="empty-runs-title">
          <div>
            <h2 id="empty-runs-title">No benchmark runs yet</h2>
            <p>
              Connect and probe an engine, then configure a workload to create your first measured
              run.
            </p>
          </div>
          <a className="button button--primary" href="#engines">
            Set up an engine
          </a>
        </section>
      )}
    </div>
  );
}

export function SystemScreen() {
  const system = useResource(fetchSystemProfile);
  const profiles = useResource(listMachineProfiles);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const saveSnapshot = async () => {
    setSaveState("saving");
    setSaveError("");
    try {
      await saveCurrentMachineProfile();
      setSaveState("saved");
      await profiles.reload();
    } catch (error) {
      setSaveState("error");
      setSaveError(
        error instanceof Error ? error.message : "Could not save this machine snapshot.",
      );
    }
  };
  return (
    <div className="screen-stack">
      <section className="panel" aria-labelledby="system-details-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">LOCAL HOST · BEST EFFORT DETECTION</p>
            <h2 id="system-details-title">Current machine</h2>
          </div>
          <button
            className="button button--quiet"
            onClick={() => void system.reload()}
            type="button"
          >
            Refresh detection
          </button>
        </div>
        <ResourcePanel
          resource={system.resource}
          onRetry={() => void system.reload()}
          loadingLabel="Inspecting this machine…"
        >
          {(value) => (
            <>
              <div className="machine-status-row">
                <div>
                  <strong>{value.host_name || value.label}</strong>
                  <span className={`status-pill status-pill--${value.status}`}>
                    {statusLabel(value.status)}
                  </span>
                </div>
                <button
                  className="button button--primary"
                  disabled={saveState === "saving"}
                  onClick={() => void saveSnapshot()}
                  type="button"
                >
                  {saveState === "saving"
                    ? "Saving…"
                    : saveState === "saved"
                      ? "Snapshot saved"
                      : "Save machine snapshot"}
                </button>
              </div>
              <p className="helper-copy">
                Detection is best effort. Missing values are not inferred. Every detected value
                includes its source and observation time.
              </p>
              <MachineFacts machine={value} />
            </>
          )}
        </ResourcePanel>
        {saveState === "error" && (
          <p className="form-error" role="alert">
            {saveError}
          </p>
        )}
        {saveState === "saved" && (
          <p className="success-message" role="status">
            This detected snapshot is saved to the local machine profile list.
          </p>
        )}
      </section>
      <section className="panel" aria-labelledby="machine-profiles-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">KNOWN TARGETS</p>
            <h2 id="machine-profiles-title">Machine profiles</h2>
          </div>
          <button
            className="button button--quiet"
            onClick={() => void profiles.reload()}
            type="button"
          >
            Refresh list
          </button>
        </div>
        <ResourcePanel
          resource={profiles.resource}
          onRetry={() => void profiles.reload()}
          loadingLabel="Loading saved machine profiles…"
        >
          {(items) =>
            items.length === 0 ? (
              <p className="empty-inline">No machine profiles have been saved yet.</p>
            ) : (
              <div className="profile-list">
                {items.map((profile) => (
                  <article className="profile-row" key={profile.id}>
                    <div>
                      <h3>{profile.label}</h3>
                      <p>
                        {profile.host_name || "Host name unknown"} ·{" "}
                        {profile.hardware?.cpu_model || "Processor unknown"}
                      </p>
                    </div>
                    <span className={`status-pill status-pill--${profile.status}`}>
                      {profile.status === "manual_unverified"
                        ? "User supplied · unverified"
                        : statusLabel(profile.status)}
                    </span>
                  </article>
                ))}
              </div>
            )
          }
        </ResourcePanel>
      </section>
    </div>
  );
}

type EngineFormValues = {
  label: string;
  engine_display_name: string;
  integration_mode: EngineMode | "";
  endpoint_url: string;
  health_route: string;
  models_route: string;
  model_id: string;
  timeout_seconds: string;
  startup_timeout_seconds: string;
  executable_path: string;
  model_path: string;
  working_directory: string;
  context_size: string;
  parallelism: string;
  batch_size: string;
  ubatch_size: string;
  gpu_layers: string;
  max_retries: string;
  retry_backoff_seconds: string;
  streaming_mode: "auto" | "required" | "disabled";
  include_usage: boolean;
  metrics_route: string;
  credential_ref: string;
  clear_credential: boolean;
  user_notes: string;
};

const blankEngineForm: EngineFormValues = {
  label: "",
  engine_display_name: "",
  integration_mode: "",
  endpoint_url: "",
  health_route: "",
  models_route: "",
  model_id: "",
  timeout_seconds: "30",
  startup_timeout_seconds: "120",
  executable_path: "",
  model_path: "",
  working_directory: "",
  context_size: "",
  parallelism: "",
  batch_size: "",
  ubatch_size: "",
  gpu_layers: "",
  max_retries: "0",
  retry_backoff_seconds: "0.25",
  streaming_mode: "auto",
  include_usage: false,
  metrics_route: "",
  credential_ref: "",
  clear_credential: false,
  user_notes: "",
};

function EngineProfileDialog({
  manifest,
  initial,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  manifest: AdapterManifest;
  initial?: EngineProfile;
  busy: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (input: EngineProfileInput) => void;
}) {
  const isOpenAiCompatible = manifest.adapter_id === "localforge.openai_compatible";
  const isLlamaCpp = manifest.adapter_id === "localforge.llama_cpp";
  const extensionNamespace = isLlamaCpp ? "llama_cpp" : "openai_compatible";
  const savedExtension = initial?.engine_extensions?.[extensionNamespace] ?? {};
  const savedString = (key: string, fallback: string) => {
    const value = savedExtension[key];
    return typeof value === "string" || typeof value === "number" ? String(value) : fallback;
  };
  const [values, setValues] = useState<EngineFormValues>(() =>
    initial
      ? {
          label: initial.label,
          engine_display_name: initial.engine_display_name ?? "",
          integration_mode: initial.integration_mode,
          endpoint_url: initial.endpoint_url ?? "",
          health_route: "",
          models_route: "",
          model_id: savedString("model_id", ""),
          timeout_seconds: savedString("timeout_seconds", "30"),
          startup_timeout_seconds: savedString("startup_timeout_seconds", "120"),
          executable_path: savedString("executable_path", ""),
          model_path: savedString("model_path", ""),
          working_directory: savedString("working_directory", ""),
          context_size: savedString("context_size", ""),
          parallelism: savedString("parallelism", ""),
          batch_size: savedString("batch_size", ""),
          ubatch_size: savedString("ubatch_size", ""),
          gpu_layers: savedString("gpu_layers", ""),
          max_retries: savedString("max_retries", "0"),
          retry_backoff_seconds: savedString("retry_backoff_seconds", "0.25"),
          streaming_mode:
            savedExtension.streaming_mode === "required" ||
            savedExtension.streaming_mode === "disabled"
              ? savedExtension.streaming_mode
              : "auto",
          include_usage: savedExtension.include_usage === true,
          metrics_route: savedString("metrics_route", ""),
          credential_ref: "",
          clear_credential: false,
          user_notes: initial.user_notes ?? "",
        }
      : { ...blankEngineForm, integration_mode: manifest.integration_modes[0] ?? "" },
  );
  const [formError, setFormError] = useState("");
  const setField = <K extends keyof EngineFormValues>(key: K, value: EngineFormValues[K]) =>
    setValues((current) => ({ ...current, [key]: value }));
  const remote = isRemoteEndpoint(values.endpoint_url);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!values.integration_mode) {
      setFormError("Choose an available connection mode.");
      return;
    }
    try {
      if (values.endpoint_url.trim()) {
        const endpoint = new URL(values.endpoint_url.trim());
        if (!["http:", "https:"].includes(endpoint.protocol) || !endpoint.hostname)
          throw new Error();
      }
    } catch {
      setFormError("Enter a valid HTTP or HTTPS endpoint URL.");
      return;
    }
    if (isOpenAiCompatible) {
      const timeout = Number(values.timeout_seconds);
      const retries = Number(values.max_retries);
      const backoff = Number(values.retry_backoff_seconds);
      if (!values.endpoint_url.trim()) {
        setFormError("Enter the OpenAI-compatible API base URL.");
        return;
      }
      if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
        setFormError("Timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (!Number.isInteger(retries) || retries < 0 || retries > 3) {
        setFormError("Connection retries must be an integer from 0 to 3.");
        return;
      }
      if (!Number.isFinite(backoff) || backoff < 0 || backoff > 2) {
        setFormError("Retry delay must be between 0 and 2 seconds.");
        return;
      }
      if (
        values.credential_ref.trim() &&
        !/^(keyring:\/\/|localforge-credential:\/\/)[^\s]+$/.test(values.credential_ref.trim())
      ) {
        setFormError(
          "Enter a keyring:// or localforge-credential:// reference, not a secret value.",
        );
        return;
      }
    }
    if (isLlamaCpp) {
      const timeout = Number(values.timeout_seconds);
      const startupTimeout = Number(values.startup_timeout_seconds);
      if (!values.endpoint_url.trim()) {
        setFormError("Enter the local llama-server endpoint URL.");
        return;
      }
      if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 300) {
        setFormError("Request timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (
        values.integration_mode === "managed_process" &&
        (!Number.isFinite(startupTimeout) || startupTimeout < 0.1 || startupTimeout > 300)
      ) {
        setFormError("Startup timeout must be between 0.1 and 300 seconds.");
        return;
      }
      if (values.integration_mode === "managed_process") {
        if (
          !values.executable_path.trim() ||
          !values.model_path.trim() ||
          !values.working_directory.trim()
        ) {
          setFormError(
            "Managed mode needs an executable, a GGUF model file, and a working directory.",
          );
          return;
        }
        if (!values.model_path.trim().toLowerCase().endsWith(".gguf")) {
          setFormError("Choose a local model file with the .gguf extension.");
          return;
        }
        for (const [label, value] of [
          ["Context size", values.context_size],
          ["Parallelism", values.parallelism],
          ["Batch size", values.batch_size],
          ["Physical batch size", values.ubatch_size],
        ] as const) {
          if (value.trim() && (!Number.isInteger(Number(value)) || Number(value) < 1)) {
            setFormError(`${label} must be a positive whole number.`);
            return;
          }
        }
        if (values.gpu_layers.trim()) {
          const normalizedGpuLayers = values.gpu_layers.trim().toLowerCase();
          const numericGpuLayers = Number(normalizedGpuLayers);
          if (
            normalizedGpuLayers !== "auto" &&
            normalizedGpuLayers !== "all" &&
            (!Number.isInteger(numericGpuLayers) ||
              numericGpuLayers < 0 ||
              numericGpuLayers > 100_000)
          ) {
            setFormError("GPU layers must be a whole number from 0 to 100000, auto, or all.");
            return;
          }
        }
        if (
          values.batch_size.trim() &&
          values.ubatch_size.trim() &&
          Number(values.ubatch_size) > Number(values.batch_size)
        ) {
          setFormError("Physical batch size cannot exceed logical batch size.");
          return;
        }
        try {
          const managedEndpoint = new URL(values.endpoint_url.trim());
          if (
            managedEndpoint.protocol !== "http:" ||
            managedEndpoint.hostname !== "127.0.0.1" ||
            managedEndpoint.search !== "" ||
            managedEndpoint.hash !== "" ||
            !["", "/", "/v1"].includes(managedEndpoint.pathname.replace(/\/$/, ""))
          ) {
            setFormError("Managed mode must use an HTTP 127.0.0.1 URL at the root.");
            return;
          }
        } catch {
          setFormError("Enter a valid local HTTP endpoint URL for managed mode.");
          return;
        }
      }
    }
    setFormError("");
    const input: EngineProfileInput = {
      label: values.label.trim(),
      adapter_id: manifest.adapter_id,
      engine_display_name: values.engine_display_name.trim() || null,
      integration_mode: values.integration_mode,
      endpoint_url: values.endpoint_url.trim() || null,
      user_notes: values.user_notes.trim() || null,
    };
    if (!isLlamaCpp && (!initial || values.health_route.trim()))
      input.health_route = values.health_route.trim() || null;
    if (!isLlamaCpp && (!initial || values.models_route.trim()))
      input.models_route = values.models_route.trim() || null;
    if (isOpenAiCompatible) {
      const extension: Record<string, unknown> = { ...savedExtension };
      if (values.model_id.trim()) extension.model_id = values.model_id.trim();
      else delete extension.model_id;
      extension.timeout_seconds = Number(values.timeout_seconds);
      extension.max_retries = Number(values.max_retries);
      extension.retry_backoff_seconds = Number(values.retry_backoff_seconds);
      extension.streaming_mode = values.streaming_mode;
      extension.include_usage = values.include_usage;
      if (values.metrics_route.trim()) extension.metrics_route = values.metrics_route.trim();
      else delete extension.metrics_route;
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        openai_compatible: extension,
      };
      if (values.credential_ref.trim()) {
        input.secret_refs = { api_key: values.credential_ref.trim() };
      } else if (values.clear_credential) {
        input.secret_refs = {};
      }
    }
    if (isLlamaCpp) {
      const extension: Record<string, unknown> = {
        timeout_seconds: Number(values.timeout_seconds),
      };
      if (values.model_id.trim()) extension.model_id = values.model_id.trim();
      if (values.integration_mode === "managed_process") {
        extension.executable_path = values.executable_path.trim();
        extension.model_path = values.model_path.trim();
        extension.working_directory = values.working_directory.trim();
        extension.startup_timeout_seconds = Number(values.startup_timeout_seconds);
        for (const key of ["context_size", "parallelism", "batch_size", "ubatch_size"] as const) {
          const value = values[key].trim();
          if (value) extension[key] = Number(value);
        }
        const gpuLayers = values.gpu_layers.trim();
        if (gpuLayers) {
          const normalizedGpuLayers = gpuLayers.toLowerCase();
          extension.gpu_layers = ["auto", "all"].includes(normalizedGpuLayers)
            ? normalizedGpuLayers
            : Number(normalizedGpuLayers);
        }
      }
      input.engine_extensions = {
        ...(initial?.engine_extensions ?? {}),
        llama_cpp: extension,
      };
    }
    onSubmit(input);
  };

  return (
    <div className="dialog-scrim">
      <AccessibleDialog className="dialog-card" titleId="engine-dialog-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">{initial ? "EDIT PROFILE" : "NEW PROFILE"}</p>
            <h2 id="engine-dialog-title">{initial ? "Edit engine profile" : "Add an engine"}</h2>
          </div>
          <button aria-label="Close dialog" className="icon-button" onClick={onClose} type="button">
            ×
          </button>
        </div>
        <p className="helper-copy">
          {manifest.display_name}: {manifest.description}
        </p>
        {manifest.category === "test_fixture" && (
          <p className="notice notice--warning">
            This adapter is a deterministic test fixture. Its probe and timings do not measure
            language-model inference performance.
          </p>
        )}
        <form className="engine-form" onSubmit={submit}>
          <label>
            Profile name
            <input
              autoFocus
              data-initial-focus
              maxLength={120}
              onChange={(event) => setField("label", event.target.value)}
              required
              value={values.label}
            />
          </label>
          <label>
            Adapter
            <select disabled value={manifest.adapter_id}>
              <option value={manifest.adapter_id}>{manifest.display_name}</option>
            </select>
          </label>
          <label>
            Connection mode
            <select
              onChange={(event) => setField("integration_mode", event.target.value as EngineMode)}
              required
              value={values.integration_mode}
            >
              <option value="">Choose a mode</option>
              {manifest.integration_modes.map((mode) => (
                <option key={mode} value={mode}>
                  {titleCase(mode)}
                </option>
              ))}
            </select>
          </label>
          <label>
            {isOpenAiCompatible
              ? "API base URL"
              : isLlamaCpp && values.integration_mode === "managed_process"
                ? "Local bind URL"
                : isLlamaCpp
                  ? "llama-server endpoint URL"
                  : "Endpoint URL"}{" "}
            {!isOpenAiCompatible && !isLlamaCpp && <span className="optional-label">Optional</span>}
            <input
              autoComplete="url"
              maxLength={1000}
              onChange={(event) => setField("endpoint_url", event.target.value)}
              placeholder={
                isOpenAiCompatible
                  ? "http://127.0.0.1:8000/v1"
                  : isLlamaCpp
                    ? "http://127.0.0.1:9931"
                    : "http://127.0.0.1:8000"
              }
              required={isOpenAiCompatible || isLlamaCpp}
              type="url"
              value={values.endpoint_url}
            />
          </label>
          <label>
            Engine display name <span className="optional-label">Optional</span>
            <input
              maxLength={160}
              onChange={(event) => setField("engine_display_name", event.target.value)}
              value={values.engine_display_name}
            />
          </label>
          {isLlamaCpp && (
            <>
              <p className="notice">
                GGUF model files stay on this machine. LocalForge never downloads or uploads them.
                GPU acceleration depends on the backend compiled into your selected llama-server
                binary.
              </p>
              <label>
                Model ID{" "}
                <span className="optional-label">Optional if the server lists a model</span>
                <input
                  maxLength={500}
                  onChange={(event) => setField("model_id", event.target.value)}
                  placeholder="Model ID reported by llama-server"
                  value={values.model_id}
                />
              </label>
              <label>
                Request timeout (seconds)
                <input
                  max={300}
                  min={0.1}
                  onChange={(event) => setField("timeout_seconds", event.target.value)}
                  step="any"
                  type="number"
                  value={values.timeout_seconds}
                />
              </label>
              {values.integration_mode === "managed_process" && (
                <details className="advanced-fields" open>
                  <summary>Local launch settings</summary>
                  <p className="helper-copy">
                    Use absolute paths. LocalForge starts only this selected executable and stops
                    only the process it launched. GGUF files must already be present.
                  </p>
                  <label>
                    llama-server executable path
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("executable_path", event.target.value)}
                      placeholder="C:\\llama.cpp\\llama-server.exe"
                      required
                      value={values.executable_path}
                    />
                  </label>
                  <label>
                    Existing GGUF model path
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("model_path", event.target.value)}
                      placeholder="C:\\Models\\model.gguf"
                      required
                      value={values.model_path}
                    />
                  </label>
                  <label>
                    Working directory
                    <input
                      autoComplete="off"
                      maxLength={1000}
                      onChange={(event) => setField("working_directory", event.target.value)}
                      placeholder="C:\\llama.cpp\\build\\bin"
                      required
                      value={values.working_directory}
                    />
                  </label>
                  <label>
                    Startup timeout (seconds)
                    <input
                      max={300}
                      min={0.1}
                      onChange={(event) => setField("startup_timeout_seconds", event.target.value)}
                      step="any"
                      type="number"
                      value={values.startup_timeout_seconds}
                    />
                  </label>
                  <details className="advanced-fields">
                    <summary>Optional server tuning</summary>
                    <p className="helper-copy">
                      Settings are passed to llama-server as documented command-line arguments.
                      Hardware compatibility is determined by your binary and model.
                    </p>
                    {(
                      [
                        ["context_size", "Context size", "4096"],
                        ["parallelism", "Parallel requests", "1"],
                        ["batch_size", "Batch size", "2048"],
                        ["ubatch_size", "Physical batch size", "512"],
                      ] as const
                    ).map(([key, label, placeholder]) => (
                      <label key={key}>
                        {label} <span className="optional-label">Optional</span>
                        <input
                          min={1}
                          onChange={(event) => setField(key, event.target.value)}
                          placeholder={placeholder}
                          step={1}
                          type="number"
                          value={values[key]}
                        />
                      </label>
                    ))}
                    <label>
                      GPU layers <span className="optional-label">Optional</span>
                      <input
                        maxLength={16}
                        onChange={(event) => setField("gpu_layers", event.target.value)}
                        placeholder="0, auto, or all"
                        value={values.gpu_layers}
                      />
                    </label>
                  </details>
                </details>
              )}
            </>
          )}
          {isOpenAiCompatible && (
            <>
              <label>
                Model ID{" "}
                <span className="optional-label">Optional if the endpoint lists models</span>
                <input
                  maxLength={500}
                  onChange={(event) => setField("model_id", event.target.value)}
                  placeholder="model ID reported by your endpoint"
                  value={values.model_id}
                />
              </label>
              <label>
                Request timeout (seconds)
                <input
                  max={300}
                  min={0.1}
                  onChange={(event) => setField("timeout_seconds", event.target.value)}
                  step="any"
                  type="number"
                  value={values.timeout_seconds}
                />
              </label>
              <label>
                Streaming mode
                <select
                  onChange={(event) =>
                    setField(
                      "streaming_mode",
                      event.target.value as EngineFormValues["streaming_mode"],
                    )
                  }
                  value={values.streaming_mode}
                >
                  <option value="auto">Use streaming when supported</option>
                  <option value="required">Require streaming</option>
                  <option value="disabled">Use non-streaming responses</option>
                </select>
              </label>
              <details className="advanced-fields">
                <summary>Endpoint options</summary>
                <p className="helper-copy">
                  Retries apply only when the connection fails before an HTTP response. The generic
                  adapter never replays a request after output begins.
                </p>
                <label>
                  Connection retries <span className="optional-label">0–3, default 0</span>
                  <input
                    max={3}
                    min={0}
                    onChange={(event) => setField("max_retries", event.target.value)}
                    step={1}
                    type="number"
                    value={values.max_retries}
                  />
                </label>
                <label>
                  Retry delay (seconds)
                  <input
                    max={2}
                    min={0}
                    onChange={(event) => setField("retry_backoff_seconds", event.target.value)}
                    step="any"
                    type="number"
                    value={values.retry_backoff_seconds}
                  />
                </label>
                <label>
                  Metrics route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("metrics_route", event.target.value)}
                    placeholder="/metrics"
                    value={values.metrics_route}
                  />
                </label>
                <label className="workload-check">
                  <input
                    checked={values.include_usage}
                    onChange={(event) => setField("include_usage", event.target.checked)}
                    type="checkbox"
                  />
                  Request optional streaming usage counts when supported
                </label>
                <label>
                  API key credential reference <span className="optional-label">Optional</span>
                  <input
                    autoComplete="off"
                    maxLength={256}
                    onChange={(event) => setField("credential_ref", event.target.value)}
                    placeholder="keyring://LocalForge/account-name"
                    value={values.credential_ref}
                  />
                  <span className="helper-copy">
                    Enter a reference to an existing OS keyring entry. Never paste the key itself.
                  </span>
                </label>
                {initial?.credential_configured && (
                  <label className="workload-check">
                    <input
                      checked={values.clear_credential}
                      onChange={(event) => setField("clear_credential", event.target.checked)}
                      type="checkbox"
                    />
                    Remove the saved credential reference
                  </label>
                )}
              </details>
            </>
          )}
          <details className="advanced-fields">
            <summary>Connection details</summary>
            {!isLlamaCpp && (
              <>
                <label>
                  Health route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("health_route", event.target.value)}
                    placeholder="/health"
                    value={values.health_route}
                  />
                </label>
                <label>
                  Models route <span className="optional-label">Optional</span>
                  <input
                    maxLength={500}
                    onChange={(event) => setField("models_route", event.target.value)}
                    placeholder="/v1/models"
                    value={values.models_route}
                  />
                </label>
              </>
            )}
            <label>
              Notes <span className="optional-label">Optional</span>
              <textarea
                maxLength={2000}
                onChange={(event) => setField("user_notes", event.target.value)}
                rows={3}
                value={values.user_notes}
              />
            </label>
          </details>
          {remote && manifest.category === "test_fixture" ? (
            <p className="notice notice--warning">
              This fixture never contacts its configured endpoint. It cannot verify this remote host
              or send it model requests. Use a network-capable adapter to probe a remote engine.
            </p>
          ) : remote ? (
            <p className="notice notice--warning">
              This URL points to a remote host. Before probing, LOCALFORGE will explain which
              requests leave this machine and ask you to confirm.
            </p>
          ) : null}
          {initial?.credential_configured && (
            <p className="notice">
              A credential is configured for this profile. Its value is stored outside this profile
              and is never displayed here.
            </p>
          )}
          {formError && (
            <p className="form-error" role="alert">
              {formError}
            </p>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button className="button button--quiet" onClick={onClose} type="button">
              Cancel
            </button>
            <button className="button button--primary" disabled={busy} type="submit">
              {busy ? "Saving…" : initial ? "Save changes" : "Add engine"}
            </button>
          </div>
        </form>
      </AccessibleDialog>
    </div>
  );
}

function isRemoteEndpoint(rawUrl: string): boolean {
  if (!rawUrl.trim()) return false;
  try {
    const host = new URL(rawUrl).hostname.toLowerCase();
    return !(
      ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host) || host.endsWith(".localhost")
    );
  } catch {
    return false;
  }
}

function CapabilityTable({ items }: { items: Record<string, CapabilityValue> }) {
  const rows = Object.entries(items);
  if (rows.length === 0)
    return (
      <p className="empty-inline">
        No capability values were reported. Each unreported capability remains unknown.
      </p>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Capability</th>
            <th scope="col">Result</th>
            <th scope="col">Source and details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, value]) => (
            <tr key={name}>
              <th scope="row">{titleCase(name)}</th>
              <td>
                <span className={`capability-state capability-state--${value.state}`}>
                  {titleCase(value.state)}
                </span>
              </td>
              <td>
                {[value.source, value.details].filter(Boolean).join(" · ") || "No details reported"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProbeDetails({
  result,
  models,
  modelsLoading,
  modelsError,
}: {
  result: ProbeResult;
  models?: EngineModel[];
  modelsLoading?: boolean;
  modelsError?: string;
}) {
  const metrics = Object.fromEntries(
    Object.entries(result.capabilities.capabilities).filter(
      ([key]) => key.includes("metric") || key.startsWith("server_"),
    ),
  );
  return (
    <div className="probe-details">
      <div className="probe-summary-grid">
        <div>
          <span>Probe status</span>
          <strong>{statusLabel(result.status)}</strong>
        </div>
        <div>
          <span>Health</span>
          <strong>{capabilityLabel(result.health)}</strong>
        </div>
        <div>
          <span>Engine version</span>
          <strong>{result.engine_version || "Unknown"}</strong>
        </div>
        <div>
          <span>Streaming</span>
          <strong>{capabilityLabel(result.checks.streaming)}</strong>
        </div>
        <div>
          <span>Model inventory</span>
          <strong>
            {capabilityLabel(
              result.checks.model_inventory ?? result.capabilities.capabilities.model_inventory,
            )}
          </strong>
        </div>
        <div>
          <span>Native metrics</span>
          <strong>
            {capabilityLabel(
              result.checks.native_metrics ?? result.capabilities.capabilities.native_metrics,
            )}
          </strong>
        </div>
      </div>
      <section aria-labelledby="probe-models-title">
        <h4 id="probe-models-title">Models reported by this probe</h4>
        {result.models.length === 0 ? (
          <p className="empty-inline">
            No models were reported. Check the model inventory state before treating this as an
            empty catalog.
          </p>
        ) : (
          <ModelTable models={result.models} />
        )}
      </section>
      <section aria-labelledby="probe-capabilities-title">
        <h4 id="probe-capabilities-title">Capabilities</h4>
        <CapabilityTable items={result.capabilities.capabilities} />
      </section>
      {Object.keys(metrics).length > 0 && (
        <section aria-labelledby="probe-metrics-title">
          <h4 id="probe-metrics-title">Metrics capability details</h4>
          <CapabilityTable items={metrics} />
        </section>
      )}
      <section aria-labelledby="probe-diagnostics-title">
        <h4 id="probe-diagnostics-title">Diagnostics</h4>
        {result.diagnostics.length === 0 ? (
          <p className="empty-inline">No diagnostics were returned by the adapter.</p>
        ) : (
          <ul className="diagnostic-list">
            {result.diagnostics.map((diagnostic, index) => (
              <li
                className={`diagnostic diagnostic--${diagnostic.severity}`}
                key={`${diagnostic.code}-${index}`}
              >
                <span>{titleCase(diagnostic.severity)}</span>
                <strong>{diagnostic.code}</strong>
                <p>{diagnostic.message}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="source-note">
        Probe recorded {formatDate(result.probed_at)} · adapter {result.adapter_id}{" "}
        {result.adapter_version}
      </p>
      {models && (
        <section aria-labelledby="saved-models-title">
          <h4 id="saved-models-title">Saved model inventory</h4>
          {models.length === 0 ? (
            <p className="empty-inline">The API has no saved model records for this profile.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col">Reference</th>
                    <th scope="col">State</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((model) => (
                    <tr key={model.id}>
                      <th scope="row">{model.display_name}</th>
                      <td>{model.model_ref || "Unknown"}</td>
                      <td>{titleCase(model.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {modelsLoading && <p role="status">Refreshing saved model inventory…</p>}
      {modelsError && (
        <p className="form-error" role="alert">
          {modelsError}
        </p>
      )}
    </div>
  );
}

function ModelTable({ models }: { models: AdapterModel[] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Reference</th>
            <th scope="col">Revision</th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => (
            <tr key={model.model_ref}>
              <th scope="row">{model.display_name}</th>
              <td>{model.model_ref}</td>
              <td>{model.revision || "Unknown"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EnginesScreen() {
  const catalog = useResource(listEngineCatalog);
  const profiles = useResource(listEngineProfiles);
  const [editing, setEditing] = useState<EngineProfile | null | "new">(null);
  const [selectedAdapterId, setSelectedAdapterId] = useState("");
  const [saving, setSaving] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyProfile, setBusyProfile] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<Record<string, ProbeResult>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [modelData, setModelData] = useState<
    Record<
      string,
      { loading: boolean; models?: Awaited<ReturnType<typeof listEngineModels>>; error?: string }
    >
  >({});
  const [remotePrompt, setRemotePrompt] = useState<{
    id: string;
    host: string;
    message: string;
  } | null>(null);
  const [removePrompt, setRemovePrompt] = useState<EngineProfile | null>(null);

  const manifestById = useMemo(
    () =>
      new Map(
        (catalog.resource.status === "ready" ? catalog.resource.value : []).map((manifest) => [
          manifest.adapter_id,
          manifest,
        ]),
      ),
    [catalog.resource],
  );

  const save = async (input: EngineProfileInput) => {
    setSaving(true);
    setDialogError("");
    try {
      if (editing && editing !== "new") {
        const patch: Partial<EngineProfileInput> = {
          label: input.label,
          engine_display_name: input.engine_display_name,
          user_notes: input.user_notes,
        };
        if (input.integration_mode !== editing.integration_mode) {
          patch.integration_mode = input.integration_mode;
        }
        if (input.endpoint_url !== editing.endpoint_url) {
          patch.endpoint_url = input.endpoint_url;
        }
        if (input.health_route !== undefined) patch.health_route = input.health_route;
        if (input.models_route !== undefined) patch.models_route = input.models_route;
        if (input.engine_extensions !== undefined)
          patch.engine_extensions = input.engine_extensions;
        if (input.secret_refs !== undefined) patch.secret_refs = input.secret_refs;
        await updateEngineProfile(editing.id, patch);
      } else {
        await createEngineProfile(input);
      }
      setEditing(null);
      await profiles.reload();
    } catch (error) {
      setDialogError(error instanceof Error ? error.message : "Could not save the engine profile.");
    } finally {
      setSaving(false);
    }
  };

  const probe = async (id: string, confirmRemote = false) => {
    const profile =
      profiles.resource.status === "ready"
        ? profiles.resource.value.find((item) => item.id === id)
        : undefined;
    const manifest = profile ? manifestById.get(profile.adapter_id) : undefined;
    if (profile?.is_remote && manifest?.category === "test_fixture") {
      setActionError(
        `${manifest.display_name} is a local test fixture and does not contact ${profile.endpoint_url}. It cannot verify a remote endpoint; use a network-capable adapter to probe that host.`,
      );
      return;
    }
    setBusyProfile(id);
    setActionError("");
    try {
      const response = await probeEngineProfile(id, confirmRemote);
      setProbeResults((current) => ({ ...current, [id]: response.result }));
      setRemotePrompt(null);
      setExpanded((current) => ({ ...current, [id]: true }));
      setModelData((current) => ({ ...current, [id]: { loading: true } }));
      await profiles.reload();
      try {
        const models = await listEngineModels(id);
        setModelData((current) => ({ ...current, [id]: { loading: false, models } }));
      } catch (error) {
        setModelData((current) => ({
          ...current,
          [id]: {
            loading: false,
            error: error instanceof Error ? error.message : "Could not load saved models.",
          },
        }));
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "remote_confirmation_required") {
        setRemotePrompt({
          id,
          host: error.host || "configured remote host",
          message: error.message,
        });
      } else {
        setActionError(
          error instanceof Error ? error.message : "The probe could not be completed.",
        );
      }
    } finally {
      setBusyProfile(null);
    }
  };

  const toggleManagedProcess = async (profile: EngineProfile) => {
    setBusyProfile(profile.id);
    setActionError("");
    try {
      if (profile.managed_process_running) await stopEngineProfile(profile.id);
      else await startEngineProfile(profile.id);
      await profiles.reload();
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : "Could not change this engine process state.",
      );
    } finally {
      setBusyProfile(null);
    }
  };

  const inspect = async (id: string) => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
    if (modelData[id] && !modelData[id].error) return;
    setModelData((current) => ({ ...current, [id]: { loading: true } }));
    try {
      const models = await listEngineModels(id);
      setModelData((current) => ({ ...current, [id]: { loading: false, models } }));
    } catch (error) {
      setModelData((current) => ({
        ...current,
        [id]: {
          loading: false,
          error: error instanceof Error ? error.message : "Could not load saved models.",
        },
      }));
    }
  };

  const remove = async () => {
    if (!removePrompt) return;
    setBusyProfile(removePrompt.id);
    setActionError("");
    try {
      await deleteEngineProfile(removePrompt.id);
      setRemovePrompt(null);
      await profiles.reload();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not remove this profile.");
    } finally {
      setBusyProfile(null);
    }
  };

  const availableManifests = catalog.resource.status === "ready" ? catalog.resource.value : [];
  return (
    <div className="screen-stack">
      <div className="engine-workbench">
        <section className="panel engine-catalog-panel" aria-labelledby="engine-catalog-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">01 / INSTALLED ADAPTERS</p>
              <h2 id="engine-catalog-title">Engine catalog</h2>
            </div>
            <button
              className="button button--primary"
              disabled={!availableManifests.length}
              onClick={() => {
                setDialogError("");
                setSelectedAdapterId(availableManifests[0]?.adapter_id ?? "");
                setEditing("new");
              }}
              type="button"
            >
              Add engine
            </button>
          </div>
          <p className="helper-copy">
            Only adapters registered with this local service are shown. Connection modes and known
            limitations come from each adapter manifest.
          </p>
          <ResourcePanel
            resource={catalog.resource}
            onRetry={() => void catalog.reload()}
            loadingLabel="Loading installed adapters…"
          >
            {(items) =>
              items.length === 0 ? (
                <p className="empty-inline">No adapters are registered in this installation.</p>
              ) : (
                <div className="catalog-grid">
                  {items.map((manifest) => (
                    <article
                      className={`catalog-card ${
                        manifest.category === "test_fixture"
                          ? "catalog-card--fixture"
                          : "catalog-card--endpoint"
                      }`}
                      key={manifest.adapter_id}
                    >
                      <div className="catalog-card-intro">
                        <span className="catalog-card-icon" aria-hidden="true">
                          {manifest.category === "test_fixture" ? (
                            <IconTerminal2 size={23} stroke={1.8} />
                          ) : (
                            <IconApi size={23} stroke={1.8} />
                          )}
                        </span>
                        <div className="catalog-card-copy">
                          <div className="catalog-card-header">
                            <span className="status-pill status-pill--enabled">Installed</span>
                            <span className="catalog-category">{titleCase(manifest.category)}</span>
                          </div>
                          <h3>{manifest.display_name}</h3>
                          <p>{manifest.description}</p>
                        </div>
                      </div>
                      <div className="catalog-card-specs">
                        <div className="catalog-meta">
                          <span>Connection modes</span>
                          <strong>{manifest.integration_modes.map(titleCase).join(" · ")}</strong>
                        </div>
                        <details className="catalog-details catalog-capabilities">
                          <summary>
                            Declared capabilities
                            <span>{manifest.declared_capabilities.length}</span>
                          </summary>
                          {manifest.declared_capabilities.length > 0 ? (
                            <ul>
                              {manifest.declared_capabilities.map((capability) => (
                                <li key={capability}>{titleCase(capability)}</li>
                              ))}
                            </ul>
                          ) : (
                            <p>Capabilities are not declared for this adapter.</p>
                          )}
                        </details>
                        {manifest.known_limitations.length > 0 && (
                          <details className="catalog-details catalog-limitations">
                            <summary>Known limitations</summary>
                            <ul>
                              {manifest.known_limitations.map((limitation) => (
                                <li key={limitation}>{limitation}</li>
                              ))}
                            </ul>
                          </details>
                        )}
                      </div>
                      <button
                        className="button button--secondary catalog-card-action"
                        onClick={() => {
                          setDialogError("");
                          setSelectedAdapterId(manifest.adapter_id);
                          setEditing("new");
                        }}
                        type="button"
                      >
                        Add this adapter
                      </button>
                    </article>
                  ))}
                </div>
              )
            }
          </ResourcePanel>
        </section>
        <section className="panel engine-profiles-panel" aria-labelledby="engine-profiles-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">02 / LOCAL CONNECTIONS</p>
              <h2 id="engine-profiles-title">Engine profiles</h2>
              <p className="engine-profiles-description">
                Local servers and user-selected endpoints, in one place.
              </p>
            </div>
            <button
              className="button button--quiet"
              onClick={() => void profiles.reload()}
              type="button"
            >
              Refresh
            </button>
          </div>
          {actionError && (
            <p className="form-error" role="alert">
              {actionError}
            </p>
          )}
          <ResourcePanel
            resource={profiles.resource}
            onRetry={() => void profiles.reload()}
            loadingLabel="Loading engine profiles…"
          >
            {(items) =>
              items.length === 0 ? (
                <div className="empty-inline empty-inline--action">
                  <figure className="engine-profile-empty-visual" aria-hidden="true">
                    <img src="/images/localforge-engine-detail.jpg" alt="" />
                    <figcaption>LOCAL WORKSPACE / READY TO CONNECT</figcaption>
                  </figure>
                  <div className="engine-profile-empty-copy">
                    <p>
                      <strong>No engine profiles are configured.</strong>
                    </p>
                    <p>Connect a local runtime or a user-selected endpoint to begin testing.</p>
                    <button
                      className="button button--primary"
                      disabled={!availableManifests.length}
                      onClick={() => {
                        setDialogError("");
                        setSelectedAdapterId(availableManifests[0]?.adapter_id ?? "");
                        setEditing("new");
                      }}
                      type="button"
                    >
                      Add your first engine
                    </button>
                  </div>
                </div>
              ) : (
                <div className="engine-profile-list">
                  {items.map((profile) => {
                    const manifest = manifestById.get(profile.adapter_id);
                    const probeResult = probeResults[profile.id];
                    const savedCapabilities = profile.capabilities;
                    const modelsState = modelData[profile.id];
                    return (
                      <article className="engine-profile-card" key={profile.id}>
                        <div className="engine-profile-header">
                          <div>
                            <div className="profile-name-row">
                              <h3>{profile.label}</h3>
                              <span className={`status-pill status-pill--${profile.status}`}>
                                {statusLabel(profile.status)}
                              </span>
                            </div>
                            <p>
                              {profile.engine_display_name ||
                                manifest?.display_name ||
                                profile.adapter_id}{" "}
                              · {titleCase(profile.integration_mode)}
                            </p>
                          </div>
                          <div className="engine-actions">
                            {profile.integration_mode === "managed_process" && (
                              <button
                                className={
                                  profile.managed_process_running
                                    ? "button button--danger-quiet"
                                    : "button button--primary"
                                }
                                disabled={!manifest || busyProfile === profile.id}
                                onClick={() => void toggleManagedProcess(profile)}
                                type="button"
                              >
                                {busyProfile === profile.id
                                  ? profile.managed_process_running
                                    ? "Stopping…"
                                    : "Starting…"
                                  : profile.managed_process_running
                                    ? "Stop server"
                                    : "Start server"}
                              </button>
                            )}
                            <button
                              className="button button--quiet"
                              disabled={
                                !manifest ||
                                busyProfile === profile.id ||
                                (profile.integration_mode === "managed_process" &&
                                  !profile.managed_process_running)
                              }
                              title={
                                manifest
                                  ? profile.integration_mode === "managed_process" &&
                                    !profile.managed_process_running
                                    ? "Start the local server before probing it."
                                    : undefined
                                  : "This adapter is not installed. Reinstall it to probe this profile."
                              }
                              onClick={() => void probe(profile.id)}
                              type="button"
                            >
                              {busyProfile === profile.id ? "Probing…" : "Probe"}
                            </button>
                            <button
                              className="button button--quiet"
                              disabled={
                                profile.integration_mode === "managed_process" &&
                                !profile.managed_process_running
                              }
                              onClick={() => void inspect(profile.id)}
                              type="button"
                            >
                              {expanded[profile.id] ? "Hide details" : "Inspect"}
                            </button>
                            <button
                              className="button button--quiet"
                              disabled={
                                !manifest ||
                                busyProfile === profile.id ||
                                profile.managed_process_running
                              }
                              title={
                                manifest
                                  ? undefined
                                  : "This adapter is not installed. Remove the profile or reinstall its adapter to edit it."
                              }
                              onClick={() => {
                                setDialogError("");
                                setEditing(profile);
                              }}
                              type="button"
                            >
                              Edit
                            </button>
                            <button
                              className="button button--danger-quiet"
                              disabled={
                                busyProfile === profile.id || profile.managed_process_running
                              }
                              onClick={() => setRemovePrompt(profile)}
                              type="button"
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                        {!manifest && (
                          <p className="inline-warning" role="status">
                            Adapter {profile.adapter_id} is not installed. Probing and editing are
                            unavailable; this profile can still be removed.
                          </p>
                        )}
                        <dl className="profile-attributes">
                          <div>
                            <dt>Adapter</dt>
                            <dd>{manifest?.display_name || profile.adapter_id}</dd>
                          </div>
                          <div>
                            <dt>Endpoint</dt>
                            <dd>
                              {profile.endpoint_url || "Not configured"}
                              {profile.is_remote && <span className="remote-chip">Remote</span>}
                            </dd>
                          </div>
                          <div>
                            <dt>Version</dt>
                            <dd>{profile.engine_version || "Unknown / not probed"}</dd>
                          </div>
                          <div>
                            <dt>Last probe</dt>
                            <dd>{formatDate(profile.last_probed_at)}</dd>
                          </div>
                          <div>
                            <dt>Credentials</dt>
                            <dd>
                              {profile.credential_configured
                                ? "Configured · value hidden"
                                : "Not configured"}
                            </dd>
                          </div>
                        </dl>
                        {expanded[profile.id] && (
                          <div className="profile-inspection">
                            {probeResult ? (
                              <ProbeDetails
                                result={probeResult}
                                models={modelsState?.models}
                                modelsLoading={modelsState?.loading}
                                modelsError={modelsState?.error}
                              />
                            ) : (
                              <>
                                <div className="inspection-heading">
                                  <div>
                                    <h4>Saved capability snapshot</h4>
                                    <p>
                                      {profile.last_probed_at
                                        ? `Last updated ${formatDate(profile.last_probed_at)}`
                                        : "No probe snapshot exists yet."}
                                    </p>
                                  </div>
                                </div>
                                {savedCapabilities ? (
                                  <CapabilityTable items={savedCapabilities} />
                                ) : (
                                  <p className="empty-inline">
                                    Capabilities unknown until this profile has a probe result.
                                  </p>
                                )}
                                {modelsState?.loading && (
                                  <p role="status">Loading saved model inventory…</p>
                                )}
                                {modelsState?.error && (
                                  <p className="form-error" role="alert">
                                    {modelsState.error}
                                  </p>
                                )}
                                {modelsState?.models && (
                                  <section>
                                    <h4>Saved model inventory</h4>
                                    {modelsState.models.length ? (
                                      <div className="table-scroll">
                                        <table>
                                          <thead>
                                            <tr>
                                              <th scope="col">Model</th>
                                              <th scope="col">Reference</th>
                                              <th scope="col">State</th>
                                            </tr>
                                          </thead>
                                          <tbody>
                                            {modelsState.models.map((model) => (
                                              <tr key={model.id}>
                                                <th scope="row">{model.display_name}</th>
                                                <td>{model.model_ref || "Unknown"}</td>
                                                <td>{titleCase(model.status)}</td>
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    ) : (
                                      <p className="empty-inline">
                                        No saved model records. Inventory may be empty or
                                        unsupported; check the capability value above.
                                      </p>
                                    )}
                                  </section>
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              )
            }
          </ResourcePanel>
        </section>
      </div>
      {editing && manifestById.get(editing === "new" ? selectedAdapterId : editing.adapter_id) && (
        <EngineProfileDialog
          busy={saving}
          error={dialogError}
          initial={editing === "new" ? undefined : editing}
          manifest={manifestById.get(editing === "new" ? selectedAdapterId : editing.adapter_id)!}
          onClose={() => setEditing(null)}
          onSubmit={(input) => void save(input)}
        />
      )}
      {remotePrompt && (
        <div className="dialog-scrim">
          <AccessibleDialog
            className="dialog-card dialog-card--compact"
            titleId="remote-dialog-title"
          >
            <div className="section-title-row">
              <div>
                <p className="eyebrow">REMOTE DATA EGRESS</p>
                <h2 id="remote-dialog-title">Confirm remote probe</h2>
              </div>
              <button
                aria-label="Close dialog"
                className="icon-button"
                onClick={() => setRemotePrompt(null)}
                type="button"
              >
                ×
              </button>
            </div>
            <p className="notice notice--warning">
              Target host: <strong>{remotePrompt.host}</strong>
            </p>
            <p>{remotePrompt.message}</p>
            <p>
              The probe will contact this host. If you later run a benchmark against it, prompts and
              model requests will also leave this machine. Do not continue unless you control or
              trust this endpoint.
            </p>
            <div className="dialog-actions">
              <button
                className="button button--quiet"
                onClick={() => setRemotePrompt(null)}
                autoFocus
                data-initial-focus
                type="button"
              >
                Cancel
              </button>
              <button
                className="button button--primary"
                onClick={() => void probe(remotePrompt.id, true)}
                type="button"
              >
                I understand — probe remote engine
              </button>
            </div>
          </AccessibleDialog>
        </div>
      )}
      {removePrompt && (
        <div className="dialog-scrim">
          <AccessibleDialog
            className="dialog-card dialog-card--compact"
            titleId="remove-dialog-title"
          >
            <div className="section-title-row">
              <h2 id="remove-dialog-title">Remove engine profile?</h2>
              <button
                aria-label="Close dialog"
                className="icon-button"
                onClick={() => setRemovePrompt(null)}
                type="button"
              >
                ×
              </button>
            </div>
            <p>
              This removes “{removePrompt.label}” from this workspace. It does not remove the engine
              or model from your machine.
            </p>
            <div className="dialog-actions">
              <button
                className="button button--quiet"
                onClick={() => setRemovePrompt(null)}
                autoFocus
                data-initial-focus
                type="button"
              >
                Keep profile
              </button>
              <button
                className="button button--danger"
                disabled={busyProfile === removePrompt.id}
                onClick={() => void remove()}
                type="button"
              >
                {busyProfile === removePrompt.id ? "Removing…" : "Remove profile"}
              </button>
            </div>
          </AccessibleDialog>
        </div>
      )}
    </div>
  );
}
