import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from "react";

import {
  ApiError,
  createBenchmarkPlan,
  createWorkload,
  enableEngineProfile,
  fetchWorkspaceSettings,
  importDataset,
  listBenchmarkPlans,
  listDatasets,
  listEngineModels,
  listEngineProfiles,
  listMachineProfiles,
  listWorkloads,
  preflightBenchmarkPlan,
  preflightSavedPlan,
  replayWorkload,
  startBenchmarkRun,
  type BenchmarkPlan,
  type BenchmarkPreflight,
  type Dataset,
  type DatasetImportResponse,
  type EngineModel,
  type EngineProfile,
  type MachineProfile,
  type Workload,
  type WorkloadInput,
  type WorkspaceSettingsResponse,
} from "../lib/api";

type ScreenData = {
  engines: EngineProfile[];
  machines: MachineProfile[];
  datasets: Dataset[];
  workloads: Workload[];
  plans: BenchmarkPlan[];
  settings: WorkspaceSettingsResponse | null;
};

type WorkloadForm = {
  name: string;
  engineId: string;
  modelId: string;
  preset: string;
  kind: "chat" | "completion";
  source: "synthetic" | "dataset";
  datasetId: string;
  trafficMode: "closed_loop" | "open_loop";
  requestCount: string;
  durationSeconds: string;
  concurrency: string;
  concurrencyLevels: string;
  arrivalRate: string;
  inputTokens: string;
  outputTokens: string;
  maxOutputTokens: string;
  warmupCount: string;
  randomSeed: string;
  promptSizingMethod: "characters" | "words" | "tokenizer";
  tokenizerName: string;
  temperature: string;
  topP: string;
  topK: string;
  retainPromptOutput: boolean;
};

type PlanInput = {
  name: string;
  engine_profile_id: string;
  machine_profile_id: string | null;
  model_artifact_id: string;
  workload_id: string;
  retain_prompt_output: boolean;
};

const PRESETS = [
  { value: "", label: "Custom workload", kind: "chat" as const, traffic: "closed_loop" as const },
  {
    value: "fixed_prefill",
    label: "Fixed prompt prefill",
    kind: "completion" as const,
    traffic: "closed_loop" as const,
  },
  {
    value: "fixed_decode",
    label: "Fixed output decode",
    kind: "completion" as const,
    traffic: "closed_loop" as const,
  },
  {
    value: "mixed_chat",
    label: "Short chat interaction",
    kind: "chat" as const,
    traffic: "closed_loop" as const,
  },
  {
    value: "concurrency_sweep",
    label: "Concurrency sweep",
    kind: "chat" as const,
    traffic: "closed_loop" as const,
  },
  {
    value: "target_request_rate",
    label: "Target request rate",
    kind: "chat" as const,
    traffic: "open_loop" as const,
  },
  {
    value: "long_context",
    label: "Long context",
    kind: "completion" as const,
    traffic: "closed_loop" as const,
  },
];

const DEFAULT_MAX_OUTPUT_TOKENS = 256;

const INITIAL_FORM: WorkloadForm = {
  name: "My benchmark plan",
  engineId: "",
  modelId: "",
  preset: "mixed_chat",
  kind: "chat",
  source: "synthetic",
  datasetId: "",
  trafficMode: "closed_loop",
  requestCount: "20",
  durationSeconds: "",
  concurrency: "1",
  concurrencyLevels: "1, 2, 4, 8",
  arrivalRate: "5",
  inputTokens: "512",
  outputTokens: "128",
  maxOutputTokens: "128",
  warmupCount: "2",
  randomSeed: "0",
  promptSizingMethod: "characters",
  tokenizerName: "",
  temperature: "",
  topP: "",
  topK: "",
  retainPromptOutput: false,
};

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "The local API request failed.";
}

function targetText(target: Record<string, unknown>, key: string) {
  const value = target[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "Not recorded";
}

function issueLabel(code: string) {
  return code.replaceAll("_", " ");
}

function formatEstimate(value: number | null, suffix: string) {
  if (value === null) return "Not available";
  return `${Number.isInteger(value) ? value : value.toFixed(1)} ${suffix}`;
}

async function fetchScreenData(): Promise<ScreenData> {
  const [engines, machines, datasets, workloads, plans, settings] = await Promise.all([
    listEngineProfiles(),
    listMachineProfiles(),
    listDatasets(),
    listWorkloads(),
    listBenchmarkPlans(),
    // Settings only adjust defaults; the builder still works with built-in defaults without them.
    fetchWorkspaceSettings().catch(() => null),
  ]);
  return { engines, machines, datasets, workloads, plans, settings };
}

/** Engines offered for new plans; test fixtures stay hidden unless Settings enables them. */
function plannableEngines(data: ScreenData | null): EngineProfile[] {
  if (!data) return [];
  const settings = data.settings;
  if (!settings || settings.settings.show_test_fixture_engines) return data.engines;
  const fixtures = new Set(settings.test_fixture_adapter_ids);
  return data.engines.filter((engine) => !fixtures.has(engine.adapter_id));
}

function withSettingsDefaults(
  current: WorkloadForm,
  settings: WorkspaceSettingsResponse | null,
): WorkloadForm {
  if (!settings) return current;
  return {
    ...current,
    warmupCount:
      current.warmupCount === INITIAL_FORM.warmupCount
        ? String(settings.settings.default_warmup_count)
        : current.warmupCount,
    retainPromptOutput:
      current.retainPromptOutput === INITIAL_FORM.retainPromptOutput
        ? settings.settings.default_retain_prompt_output
        : current.retainPromptOutput,
  };
}

export function WorkloadsScreen() {
  const [data, setData] = useState<ScreenData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(INITIAL_FORM);
  const [modelResource, setModelResource] = useState<{
    engineId: string;
    models: EngineModel[];
    error: string | null;
  }>({ engineId: "", models: [], error: null });
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<DatasetImportResponse | null>(null);
  const [importName, setImportName] = useState("");
  const [importFormat, setImportFormat] = useState<"jsonl" | "csv">("jsonl");
  const [promptField, setPromptField] = useState("prompt");
  const [answerField, setAnswerField] = useState("");
  const [rowLimit, setRowLimit] = useState("10000");
  const [shuffleSeed, setShuffleSeed] = useState("0");
  const [retainDataset, setRetainDataset] = useState(false);
  const [stage, setStage] = useState<"form" | "review">("form");
  const [busy, setBusy] = useState(false);
  const [topError, setTopError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [workload, setWorkload] = useState<Workload | null>(null);
  const [planInput, setPlanInput] = useState<PlanInput | null>(null);
  const [preflight, setPreflight] = useState<BenchmarkPreflight | null>(null);
  const [activePlan, setActivePlan] = useState<BenchmarkPlan | null>(null);
  const [savedPlan, setSavedPlan] = useState(false);
  const [remoteAcknowledged, setRemoteAcknowledged] = useState(false);
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [contentionOverrideNeeded, setContentionOverrideNeeded] = useState(false);
  const [contentionAcknowledged, setContentionAcknowledged] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [runContended, setRunContended] = useState(false);

  const reloadData = useCallback(async () => {
    try {
      const result = await fetchScreenData();
      setData(result);
      setLoadError(null);
      const firstEngine = plannableEngines(result)[0];
      setForm((current) =>
        current.engineId || !firstEngine ? current : { ...current, engineId: firstEngine.id },
      );
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    let active = true;
    void fetchScreenData().then(
      (result) => {
        if (!active) return;
        setData(result);
        const firstEngine = plannableEngines(result)[0];
        setForm((current) => {
          const withDefaults = withSettingsDefaults(current, result.settings);
          return withDefaults.engineId || !firstEngine
            ? withDefaults
            : { ...withDefaults, engineId: firstEngine.id };
        });
      },
      (error: unknown) => {
        if (active) setLoadError(errorMessage(error));
      },
    );
    return () => {
      active = false;
    };
  }, []);

  const selectedEngine = useMemo(
    () => data?.engines.find((engine) => engine.id === form.engineId) ?? null,
    [data?.engines, form.engineId],
  );

  useEffect(() => {
    let active = true;
    if (!form.engineId) return;
    listEngineModels(form.engineId).then(
      (value) => {
        if (!active) return;
        setModelResource({ engineId: form.engineId, models: value, error: null });
        setForm((current) => ({
          ...current,
          modelId: value.some((model) => model.id === current.modelId)
            ? current.modelId
            : (value[0]?.id ?? ""),
        }));
      },
      (error: unknown) => {
        if (active)
          setModelResource({ engineId: form.engineId, models: [], error: errorMessage(error) });
      },
    );
    return () => {
      active = false;
    };
  }, [form.engineId]);

  const models = modelResource.engineId === form.engineId ? modelResource.models : [];
  const modelsError = modelResource.engineId === form.engineId ? modelResource.error : null;
  const selectedModel = models.find((model) => model.id === form.modelId) ?? null;
  const chosenDataset = data?.datasets.find((dataset) => dataset.id === form.datasetId) ?? null;
  const highRiskWarnings = (preflight?.warnings ?? []).filter((issue) =>
    /high_concurrency|concurrency_capacity_unknown|memory|large_context/.test(issue.code),
  );
  const hasContentionWarning = Boolean(
    preflight?.resource_contention.length || contentionOverrideNeeded,
  );
  const canLaunch = Boolean(
    activePlan &&
    savedPlan &&
    activePlan.status === "ready" &&
    preflight?.valid &&
    (!preflight.remote_data_egress || remoteAcknowledged) &&
    (highRiskWarnings.length === 0 || riskAcknowledged) &&
    (!hasContentionWarning || contentionAcknowledged),
  );

  function update<K extends keyof WorkloadForm>(key: K, value: WorkloadForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setFieldErrors((current) => {
      const next = { ...current };
      const apiFieldByFormKey: Partial<Record<keyof WorkloadForm, string>> = {
        engineId: "engine_profile_id",
        modelId: "model_artifact_id",
        source: "input_mode",
        datasetId: "dataset_id",
        trafficMode: "traffic_mode",
        requestCount: "request_count",
        durationSeconds: "duration_seconds",
        concurrencyLevels: "concurrency_levels",
        arrivalRate: "arrival_rate_per_second",
        inputTokens: "target_input_tokens",
        outputTokens: "target_output_tokens",
        maxOutputTokens: "max_output_tokens",
        warmupCount: "warmup_count",
        randomSeed: "random_seed",
        promptSizingMethod: "prompt_sizing_method",
      };
      delete next[key];
      delete next[apiFieldByFormKey[key] ?? key];
      return next;
    });
  }

  function onEngineChange(event: ChangeEvent<HTMLSelectElement>) {
    const engine = data?.engines.find((item) => item.id === event.target.value);
    setForm((current) =>
      current.engineId === event.target.value
        ? current
        : { ...current, engineId: event.target.value, modelId: "" },
    );
    if (engine)
      setFieldErrors((current) => {
        const next = { ...current };
        delete next.engine_profile_id;
        return next;
      });
  }

  function onPresetChange(event: ChangeEvent<HTMLSelectElement>) {
    const preset = PRESETS.find((item) => item.value === event.target.value) ?? PRESETS[0]!;
    const defaultSizing: Record<string, [input: number, output: number]> = {
      fixed_prefill: [1024, 32],
      fixed_decode: [128, 256],
      mixed_chat: [512, 128],
      concurrency_sweep: [512, 128],
      target_request_rate: [512, 128],
      long_context: [8192, 128],
    };
    const sizing = defaultSizing[preset.value];
    setForm((current) => ({
      ...current,
      preset: preset.value,
      kind: preset.kind,
      trafficMode: preset.traffic,
      ...(sizing
        ? {
            inputTokens: String(sizing[0]),
            outputTokens: String(sizing[1]),
            maxOutputTokens: String(sizing[1]),
          }
        : {}),
      concurrencyLevels:
        preset.value === "concurrency_sweep" && !current.concurrencyLevels
          ? "1, 2, 4, 8"
          : current.concurrencyLevels,
    }));
  }

  async function onSubmitWorkload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setTopError(null);
    setFieldErrors({});
    setRunId(null);
    try {
      let datasetId = form.datasetId;
      if (form.source === "dataset" && file) {
        if (file.size > 100 * 1024 * 1024) {
          setFieldErrors({ file: "Files must be 100 MB or smaller." });
          return;
        }
        const imported = await importDataset(file, {
          name: importName.trim() || file.name.replace(/\.[^.]+$/, ""),
          format: importFormat,
          promptField: promptField.trim(),
          expectedAnswerField: answerField.trim(),
          rowLimit: Number(rowLimit),
          shuffleSeed: Number(shuffleSeed),
          promptSizingMethod: form.promptSizingMethod,
          tokenizerName: form.tokenizerName.trim(),
          retainContent: retainDataset,
        });
        datasetId = imported.dataset.id;
        setForm((current) => ({ ...current, datasetId }));
        setImportResult(imported);
        setFile(null);
        await reloadData();
      }
      if (form.source === "dataset" && !datasetId) {
        setFieldErrors({ dataset_id: "Select an imported dataset or choose a file to import." });
        return;
      }
      if (!selectedEngine || !form.modelId) {
        setFieldErrors({ engine_profile_id: "Choose a probed engine and one of its models." });
        return;
      }
      if (form.requestCount.trim() === "" && form.durationSeconds.trim() === "") {
        setFieldErrors({ request_count: "Set a request limit, a duration, or both." });
        return;
      }

      const requestCount = form.requestCount.trim() ? Number(form.requestCount) : null;
      const durationSeconds = form.durationSeconds.trim() ? Number(form.durationSeconds) : null;
      const samplingConfig: WorkloadInput["sampling_config"] = {};
      if (form.temperature.trim()) samplingConfig.temperature = Number(form.temperature);
      if (form.topP.trim()) samplingConfig.top_p = Number(form.topP);
      if (form.topK.trim()) samplingConfig.top_k = Number(form.topK);
      const levels = form.concurrencyLevels
        .split(",")
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value) && value > 0);
      const selectedDataset = data?.datasets.find((item) => item.id === datasetId) ?? null;
      const workloadInput: WorkloadInput = {
        name: form.name.trim(),
        kind: form.kind,
        input_mode: form.source,
        dataset_id: form.source === "dataset" ? datasetId : null,
        preset: form.preset || null,
        request_count: requestCount,
        duration_seconds: durationSeconds,
        concurrency: Number(form.concurrency),
        concurrency_levels: form.preset === "concurrency_sweep" ? levels : [],
        traffic_mode: form.trafficMode,
        arrival_rate_per_second:
          form.trafficMode === "open_loop" && form.arrivalRate.trim()
            ? Number(form.arrivalRate)
            : null,
        warmup_count: Number(form.warmupCount),
        target_input_tokens:
          form.source === "synthetic" &&
          form.promptSizingMethod !== "tokenizer" &&
          form.inputTokens.trim()
            ? Number(form.inputTokens)
            : null,
        target_output_tokens: form.outputTokens.trim() ? Number(form.outputTokens) : null,
        max_output_tokens: form.maxOutputTokens.trim()
          ? Number(form.maxOutputTokens)
          : DEFAULT_MAX_OUTPUT_TOKENS,
        prompt_sizing_method: form.promptSizingMethod,
        random_seed:
          form.source === "dataset"
            ? (selectedDataset?.shuffle_seed ?? Number(shuffleSeed))
            : Number(form.randomSeed),
        sampling_config: samplingConfig,
      };
      const createdWorkload = await createWorkload(workloadInput);
      setWorkload(createdWorkload);
      const nextPlanInput: PlanInput = {
        name: form.name.trim(),
        engine_profile_id: selectedEngine.id,
        machine_profile_id: selectedEngine.machine_profile_id,
        model_artifact_id: form.modelId,
        workload_id: createdWorkload.id,
        retain_prompt_output: form.retainPromptOutput,
      };
      const checked = await preflightBenchmarkPlan(nextPlanInput);
      setPlanInput(nextPlanInput);
      setPreflight(checked);
      setActivePlan(null);
      setSavedPlan(false);
      setRemoteAcknowledged(false);
      setRiskAcknowledged(false);
      setContentionOverrideNeeded(false);
      setContentionAcknowledged(false);
      setStage("review");
      await reloadData();
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length > 0) {
        setFieldErrors(error.fieldErrors);
      }
      setTopError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function savePlan() {
    if (!planInput) return;
    setBusy(true);
    setTopError(null);
    try {
      const response = await createBenchmarkPlan(planInput);
      setActivePlan(response.plan);
      setPreflight(response.preflight);
      setSavedPlan(true);
      setRemoteAcknowledged(false);
      setRiskAcknowledged(false);
      await reloadData();
    } catch (error) {
      setTopError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function replayPlan(plan: BenchmarkPlan) {
    setBusy(true);
    setTopError(null);
    setRunId(null);
    setRunContended(false);
    try {
      const checked = await preflightSavedPlan(plan.id);
      setActivePlan({ ...plan, status: checked.valid ? "ready" : "blocked" });
      setPlanInput({
        name: plan.name,
        engine_profile_id: plan.engine_profile_id,
        machine_profile_id: plan.machine_profile_id,
        model_artifact_id: plan.model_artifact_id ?? "",
        workload_id: plan.workload_id,
        retain_prompt_output: plan.retain_prompt_output,
      });
      setWorkload(data?.workloads.find((item) => item.id === plan.workload_id) ?? null);
      setPreflight(checked);
      setSavedPlan(true);
      setRemoteAcknowledged(false);
      setRiskAcknowledged(false);
      setContentionOverrideNeeded(false);
      setContentionAcknowledged(false);
      setStage("review");
    } catch (error) {
      setTopError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function replayWorkloadDraft(item: Workload) {
    setBusy(true);
    setTopError(null);
    try {
      const draft = await replayWorkload(item.id);
      setForm((current) => ({
        ...current,
        name: `${draft.name} replay`,
        preset: draft.preset ?? "",
        kind: draft.kind,
        source: draft.input_mode,
        datasetId: draft.dataset_id ?? "",
        trafficMode: draft.traffic_mode,
        requestCount: draft.request_count === null ? "" : String(draft.request_count),
        durationSeconds: draft.duration_seconds === null ? "" : String(draft.duration_seconds),
        concurrency: String(draft.concurrency),
        concurrencyLevels: draft.concurrency_levels.join(", "),
        arrivalRate:
          draft.arrival_rate_per_second === null ? "" : String(draft.arrival_rate_per_second),
        inputTokens: draft.target_input_tokens === null ? "" : String(draft.target_input_tokens),
        outputTokens: draft.target_output_tokens === null ? "" : String(draft.target_output_tokens),
        maxOutputTokens: String(draft.max_output_tokens),
        warmupCount: String(draft.warmup_count),
        randomSeed: String(draft.random_seed),
        promptSizingMethod: draft.prompt_sizing_method,
      }));
      setStage("form");
      document.getElementById("workload-builder-title")?.focus();
    } catch (error) {
      setTopError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function enableSelectedEngine() {
    if (!selectedEngine) return;
    setBusy(true);
    setTopError(null);
    try {
      await enableEngineProfile(selectedEngine.id);
      await reloadData();
    } catch (error) {
      setTopError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function launchRun() {
    if (!activePlan || !canLaunch) return;
    setBusy(true);
    setTopError(null);
    try {
      const result = await startBenchmarkRun(activePlan.id, hasContentionWarning);
      setRunId(result.run.id);
      setRunContended(result.run.potentially_contended);
      setContentionOverrideNeeded(false);
      setContentionAcknowledged(false);
      void reloadData();
    } catch (error) {
      if (error instanceof ApiError && error.code === "resource_lock_conflict") {
        setContentionOverrideNeeded(true);
        setContentionAcknowledged(false);
        setTopError(
          "This target is already running another benchmark. Review the contention warning, then explicitly continue if you accept it.",
        );
      } else {
        setTopError(errorMessage(error));
      }
    } finally {
      setBusy(false);
    }
  }

  function changeStageToForm() {
    setStage("form");
    setTopError(null);
    setFieldErrors({});
    setWorkload(null);
    setPlanInput(null);
    setPreflight(null);
    setActivePlan(null);
    setSavedPlan(false);
  }

  const currentDatasetHash = workload?.dataset_id
    ? (data?.datasets.find((dataset) => dataset.id === workload.dataset_id)?.snapshot_hash ?? null)
    : null;
  const workloadDataset = workload?.dataset_id
    ? (data?.datasets.find((dataset) => dataset.id === workload.dataset_id) ?? null)
    : null;

  if (loadError && !data) {
    return (
      <section className="panel" aria-label="Workload builder">
        <div className="state-message state-message--error" role="alert">
          {loadError}
        </div>
        <button className="button" onClick={() => void reloadData()} type="button">
          Retry
        </button>
      </section>
    );
  }

  return (
    <div className="screen-stack workload-screen">
      {topError && (
        <p className="form-error workload-top-error" role="alert">
          {topError}
        </p>
      )}
      {stage === "form" && Object.values(fieldErrors).some(Boolean) && (
        <div className="field-error-summary" role="alert" aria-label="Workload validation errors">
          <p>Review these workload settings:</p>
          <ul>
            {Object.entries(fieldErrors)
              .filter(([, message]) => Boolean(message))
              .map(([field, message]) => (
                <li key={field}>
                  <strong>{issueLabel(field)}:</strong> {message}
                </li>
              ))}
          </ul>
        </div>
      )}
      {stage === "form" ? (
        <>
          <section className="panel">
            <div className="section-title-row">
              <div>
                <p className="eyebrow">NEW BENCHMARK</p>
                <h2 id="workload-builder-title" tabIndex={-1}>
                  Build a repeatable workload
                </h2>
              </div>
              <span className="workload-step">Step 1 of 2 · Configure</span>
            </div>
            {loadError && (
              <p className="form-error" role="alert">
                {loadError}
              </p>
            )}
            {data && plannableEngines(data).length === 0 && (
              <div className="workload-note workload-no-engine" role="note">
                Add, probe, and enable an engine before building a benchmark. Go to{" "}
                <a href="#engines">Engines</a> to configure a local or remote target.
              </div>
            )}
            <form className="workload-form" onSubmit={(event) => void onSubmitWorkload(event)}>
              <fieldset className="workload-fieldset">
                <legend>Target</legend>
                <div className="workload-grid">
                  <label className="workload-field workload-field--wide">
                    <span>Plan and workload name</span>
                    <input
                      aria-describedby={fieldErrors.name ? "error-name" : undefined}
                      aria-invalid={Boolean(fieldErrors.name)}
                      onChange={(event) => update("name", event.target.value)}
                      required
                      value={form.name}
                    />
                    {fieldErrors.name && <FieldError id="error-name" message={fieldErrors.name} />}
                  </label>
                  <label className="workload-field">
                    <span>Engine profile</span>
                    <select
                      aria-describedby={fieldErrors.engine_profile_id ? "error-engine" : undefined}
                      aria-invalid={Boolean(fieldErrors.engine_profile_id)}
                      onChange={onEngineChange}
                      required
                      value={form.engineId}
                    >
                      <option value="">Choose an engine</option>
                      {plannableEngines(data).map((engine) => (
                        <option key={engine.id} value={engine.id}>
                          {engine.label} · {engine.status}
                        </option>
                      ))}
                    </select>
                    {fieldErrors.engine_profile_id && (
                      <FieldError id="error-engine" message={fieldErrors.engine_profile_id} />
                    )}
                  </label>
                  <label className="workload-field">
                    <span>Model</span>
                    <select
                      aria-label="Model"
                      aria-describedby={fieldErrors.model_artifact_id ? "error-model" : undefined}
                      aria-invalid={Boolean(fieldErrors.model_artifact_id)}
                      disabled={!form.engineId || models.length === 0}
                      onChange={(event) => update("modelId", event.target.value)}
                      required
                      value={form.modelId}
                    >
                      <option value="">
                        {models.length ? "Choose a model" : "Probe the engine to list models"}
                      </option>
                      {models.map((model) => (
                        <option key={model.id} value={model.id}>
                          {model.display_name}
                          {model.max_context_tokens
                            ? ` · ${model.max_context_tokens.toLocaleString()} token context`
                            : " · context unknown"}
                        </option>
                      ))}
                    </select>
                    {modelsError && <FieldError id="error-model" message={modelsError} />}
                    {selectedModel && (
                      <small>
                        Context limit:{" "}
                        {selectedModel.max_context_tokens
                          ? `${selectedModel.max_context_tokens.toLocaleString()} tokens`
                          : "unknown; preflight will warn"}
                        . Token metadata: {selectedModel.tokenizer_identity ?? "not reported"}.
                      </small>
                    )}
                    {selectedEngine?.adapter_id === "localforge.ollama" && (
                      <p className="workload-note workload-field--wide" role="note">
                        {selectedModel
                          ? "This model was reported in the attached local Ollama inventory. It must already be installed. LOCALFORGE never downloads or removes models."
                          : "Only models already installed in the attached local Ollama server can be benchmarked. Install models outside LOCALFORGE, then probe again; LOCALFORGE never downloads or removes models."}
                        {selectedModel && (
                          <>
                            {" "}
                            Underlying runner:{" "}
                            {typeof selectedModel.engine_extensions?.ollama?.runner === "string"
                              ? selectedModel.engine_extensions.ollama.runner
                              : "unknown"}
                            .
                          </>
                        )}
                      </p>
                    )}
                  </label>
                  {selectedEngine && selectedEngine.status !== "enabled" && (
                    <div className="workload-engine-state workload-field--wide">
                      {selectedEngine.status === "disabled" && selectedEngine.last_probed_at ? (
                        <>
                          <span>This engine has been probed but is disabled.</span>
                          <button
                            className="button button--quiet"
                            disabled={busy}
                            onClick={() => void enableSelectedEngine()}
                            type="button"
                          >
                            Enable this probed engine
                          </button>
                        </>
                      ) : (
                        <span>
                          {selectedEngine.status === "needs_attention"
                            ? "The latest engine probe needs attention. "
                            : "Probe this engine profile first. "}
                          Open <a href="#engines">Engines</a> to retry the probe before building a
                          run.
                        </span>
                      )}
                    </div>
                  )}
                  {selectedEngine && (
                    <div className="workload-note workload-field--wide">
                      Target machine:{" "}
                      {data?.machines.find(
                        (machine) => machine.id === selectedEngine.machine_profile_id,
                      )?.label ?? "Resolved by the engine profile or local machine"}
                      . Endpoint: {selectedEngine.endpoint_url ?? "managed or in-process target"}.
                    </div>
                  )}
                </div>
              </fieldset>

              <fieldset className="workload-fieldset">
                <legend>Request source and traffic</legend>
                <div className="workload-grid">
                  <label className="workload-field">
                    <span>Workload template</span>
                    <select
                      aria-label="Workload template"
                      onChange={onPresetChange}
                      value={form.preset}
                    >
                      {PRESETS.map((preset) => (
                        <option key={preset.value || "custom"} value={preset.value}>
                          {preset.label}
                        </option>
                      ))}
                    </select>
                    <small>
                      Available templates reflect the workload types the local API supports.
                    </small>
                  </label>
                  <label className="workload-field">
                    <span>Request source</span>
                    <select
                      onChange={(event) =>
                        update("source", event.target.value as WorkloadForm["source"])
                      }
                      value={form.source}
                    >
                      <option value="synthetic">Generated prompts · deterministic seed</option>
                      <option value="dataset">Imported JSONL or CSV dataset</option>
                    </select>
                  </label>
                  {form.source === "dataset" ? (
                    <>
                      <label className="workload-field workload-field--wide">
                        <span>Choose a validated dataset</span>
                        <select
                          aria-describedby={fieldErrors.dataset_id ? "error-dataset" : undefined}
                          aria-invalid={Boolean(fieldErrors.dataset_id)}
                          onChange={(event) => {
                            update("datasetId", event.target.value);
                            const selected = data?.datasets.find(
                              (dataset) => dataset.id === event.target.value,
                            );
                            if (selected?.prompt_sizing_method) {
                              update(
                                "promptSizingMethod",
                                selected.prompt_sizing_method as WorkloadForm["promptSizingMethod"],
                              );
                            }
                          }}
                          value={form.datasetId}
                        >
                          <option value="">Import a file below or choose a dataset</option>
                          {(data?.datasets ?? []).map((dataset) => (
                            <option key={dataset.id} value={dataset.id}>
                              {dataset.name} · {dataset.item_count ?? "?"} rows ·{" "}
                              {dataset.content_retained ? "content retained" : "metadata only"}
                            </option>
                          ))}
                        </select>
                        {fieldErrors.dataset_id && (
                          <FieldError id="error-dataset" message={fieldErrors.dataset_id} />
                        )}
                      </label>
                      {form.inputTokens.trim() && (
                        <p className="workload-warning workload-field--wide">
                          The synthetic input-size target is preserved in this form but is not
                          applied to imported prompts. Dataset text is replayed exactly as stored.
                        </p>
                      )}
                      <div className="workload-import workload-field--wide">
                        <h3>Import a local file</h3>
                        <p>
                          Upload stays on this LOCALFORGE machine. Preview rows are always redacted.
                        </p>
                        <div className="workload-grid">
                          <label className="workload-field">
                            <span>Dataset name</span>
                            <input
                              onChange={(event) => setImportName(event.target.value)}
                              placeholder="Defaults to file name"
                              value={importName}
                            />
                          </label>
                          <label className="workload-field">
                            <span>File format</span>
                            <select
                              onChange={(event) =>
                                setImportFormat(event.target.value as "jsonl" | "csv")
                              }
                              value={importFormat}
                            >
                              <option value="jsonl">JSONL</option>
                              <option value="csv">CSV</option>
                            </select>
                          </label>
                          <label className="workload-field">
                            <span>Prompt field / column</span>
                            <input
                              onChange={(event) => setPromptField(event.target.value)}
                              required
                              value={promptField}
                            />
                          </label>
                          <label className="workload-field">
                            <span>
                              Expected-answer field <em>optional</em>
                            </span>
                            <input
                              onChange={(event) => setAnswerField(event.target.value)}
                              value={answerField}
                            />
                          </label>
                          <label className="workload-field">
                            <span>Maximum selected rows</span>
                            <input
                              min="1"
                              max="100000"
                              onChange={(event) => setRowLimit(event.target.value)}
                              required
                              type="number"
                              value={rowLimit}
                            />
                          </label>
                          <label className="workload-field">
                            <span>Deterministic sample seed</span>
                            <input
                              min="0"
                              onChange={(event) => setShuffleSeed(event.target.value)}
                              required
                              type="number"
                              value={shuffleSeed}
                            />
                          </label>
                          <label className="workload-field workload-field--wide">
                            <span>Select a JSONL or CSV file · maximum 100 MB</span>
                            <input
                              accept={importFormat === "jsonl" ? ".jsonl,.ndjson,.json" : ".csv"}
                              aria-describedby={fieldErrors.file ? "error-file" : undefined}
                              aria-invalid={Boolean(fieldErrors.file)}
                              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                              type="file"
                            />
                            {fieldErrors.file && (
                              <FieldError id="error-file" message={fieldErrors.file} />
                            )}
                          </label>
                          <label className="workload-check workload-field--wide">
                            <input
                              checked={retainDataset}
                              onChange={(event) => setRetainDataset(event.target.checked)}
                              type="checkbox"
                            />
                            <span>
                              Retain prompt content locally. Required to replay this dataset in a
                              benchmark; leave off if you only need validation metadata.
                            </span>
                          </label>
                        </div>
                        {importResult && <DatasetSummary result={importResult} />}
                      </div>
                      {chosenDataset && !chosenDataset.content_retained && (
                        <p className="workload-warning workload-field--wide">
                          This dataset contains metadata only. Preflight will block a run because
                          request content cannot be replayed.
                        </p>
                      )}
                    </>
                  ) : (
                    <>
                      <label className="workload-field">
                        <span>Request format</span>
                        <select
                          onChange={(event) =>
                            update("kind", event.target.value as WorkloadForm["kind"])
                          }
                          value={form.kind}
                        >
                          <option value="chat">Chat messages</option>
                          <option value="completion">Raw completion</option>
                        </select>
                      </label>
                      <div className="workload-note">
                        Synthetic prompts are generated from the selected seed. No external dataset
                        is fetched.
                      </div>
                    </>
                  )}
                  <fieldset className="workload-mode workload-field--wide">
                    <legend>Traffic mode</legend>
                    <label>
                      <input
                        checked={form.trafficMode === "closed_loop"}
                        disabled={
                          form.preset === "target_request_rate" ||
                          form.preset === "concurrency_sweep"
                        }
                        onChange={() => update("trafficMode", "closed_loop")}
                        type="radio"
                      />{" "}
                      Closed loop: each client sends its next request after the prior response. Set
                      concurrency below.
                    </label>
                    <label>
                      <input
                        checked={form.trafficMode === "open_loop"}
                        disabled={Boolean(form.preset && form.preset !== "target_request_rate")}
                        onChange={() => update("trafficMode", "open_loop")}
                        type="radio"
                      />{" "}
                      Open loop: schedule requests at a target rate regardless of response time.
                      This measures queueing under offered load.
                    </label>
                  </fieldset>
                </div>
              </fieldset>

              <fieldset className="workload-fieldset">
                <legend>Run limits and generation settings</legend>
                <div className="workload-grid">
                  <label className="workload-field">
                    <span>
                      Measured request limit <em>optional if duration is set</em>
                    </span>
                    <input
                      aria-label="Measured request limit"
                      aria-describedby={
                        fieldErrors.request_count ? "error-request-count" : undefined
                      }
                      aria-invalid={Boolean(fieldErrors.request_count)}
                      min="1"
                      max="1000000"
                      onChange={(event) => update("requestCount", event.target.value)}
                      type="number"
                      value={form.requestCount}
                    />
                    {fieldErrors.request_count && (
                      <FieldError id="error-request-count" message={fieldErrors.request_count} />
                    )}
                  </label>
                  <label className="workload-field">
                    <span>
                      Measurement duration in seconds <em>optional if request limit is set</em>
                    </span>
                    <input
                      aria-label="Measurement duration in seconds"
                      min="0.1"
                      max="86400"
                      onChange={(event) => update("durationSeconds", event.target.value)}
                      step="any"
                      type="number"
                      value={form.durationSeconds}
                    />
                  </label>
                  {form.trafficMode === "closed_loop" ? (
                    <label className="workload-field">
                      <span>Virtual-client concurrency</span>
                      <input
                        aria-describedby={fieldErrors.concurrency ? "error-concurrency" : undefined}
                        aria-invalid={Boolean(fieldErrors.concurrency)}
                        min="1"
                        max="100000"
                        onChange={(event) => update("concurrency", event.target.value)}
                        required
                        type="number"
                        value={form.concurrency}
                      />
                      {fieldErrors.concurrency && (
                        <FieldError id="error-concurrency" message={fieldErrors.concurrency} />
                      )}
                    </label>
                  ) : (
                    <>
                      <label className="workload-field">
                        <span>Target request rate per second</span>
                        <input
                          aria-label="Target request rate per second"
                          aria-describedby={
                            fieldErrors.arrival_rate_per_second ? "error-arrival-rate" : undefined
                          }
                          aria-invalid={Boolean(fieldErrors.arrival_rate_per_second)}
                          min="0.01"
                          max="100000"
                          onChange={(event) => update("arrivalRate", event.target.value)}
                          required
                          step="any"
                          type="number"
                          value={form.arrivalRate}
                        />
                        {fieldErrors.arrival_rate_per_second && (
                          <FieldError
                            id="error-arrival-rate"
                            message={fieldErrors.arrival_rate_per_second}
                          />
                        )}
                      </label>
                      <label className="workload-field">
                        <span>Maximum in-flight requests</span>
                        <input
                          aria-label="Maximum in-flight requests"
                          aria-describedby={
                            fieldErrors.concurrency ? "error-concurrency" : undefined
                          }
                          aria-invalid={Boolean(fieldErrors.concurrency)}
                          min="1"
                          max="100000"
                          onChange={(event) => update("concurrency", event.target.value)}
                          required
                          type="number"
                          value={form.concurrency}
                        />
                        {fieldErrors.concurrency && (
                          <FieldError id="error-concurrency" message={fieldErrors.concurrency} />
                        )}
                        <small>
                          Lets the load generator keep the selected request rate when responses are
                          slow. Preflight checks this concurrency against available resources.
                        </small>
                      </label>
                    </>
                  )}
                  {form.preset === "concurrency_sweep" && (
                    <label className="workload-field">
                      <span>Ascending concurrency levels</span>
                      <input
                        aria-describedby={
                          fieldErrors.concurrency_levels ? "error-levels" : undefined
                        }
                        aria-invalid={Boolean(fieldErrors.concurrency_levels)}
                        onChange={(event) => update("concurrencyLevels", event.target.value)}
                        required
                        value={form.concurrencyLevels}
                      />
                      <small>Comma-separated unique positive levels, for example 1, 2, 4.</small>
                      {fieldErrors.concurrency_levels && (
                        <FieldError id="error-levels" message={fieldErrors.concurrency_levels} />
                      )}
                    </label>
                  )}
                  <label className="workload-field">
                    <span>Warmup requests</span>
                    <input
                      min="0"
                      max="100000"
                      onChange={(event) => update("warmupCount", event.target.value)}
                      required
                      type="number"
                      value={form.warmupCount}
                    />
                  </label>
                  {form.source === "synthetic" ? (
                    <>
                      <label className="workload-field">
                        <span>Synthetic prompt seed</span>
                        <input
                          min="0"
                          onChange={(event) => update("randomSeed", event.target.value)}
                          required
                          type="number"
                          value={form.randomSeed}
                        />
                      </label>
                      <label className="workload-field">
                        <span>
                          Input size target in tokens <em>approximate</em>
                        </span>
                        <input
                          aria-label="Input size in tokens"
                          disabled={form.promptSizingMethod === "tokenizer"}
                          min="1"
                          max="10000000"
                          onChange={(event) => update("inputTokens", event.target.value)}
                          type="number"
                          value={form.inputTokens}
                        />
                        {form.promptSizingMethod === "tokenizer" && (
                          <small>
                            No tokenizer is installed. This input size is preserved but not applied;
                            choose character or word approximation to size generated prompts.
                          </small>
                        )}
                      </label>
                    </>
                  ) : (
                    <div className="workload-note workload-field--wide">
                      {chosenDataset
                        ? `Imported prompts are replayed as stored. Counting method: ${chosenDataset.count_method ?? "unknown"}. Dataset sample seed: ${chosenDataset.shuffle_seed ?? "not recorded"}.`
                        : `Imported prompts are replayed as stored. Dataset sample seed: ${shuffleSeed || "not set"}.`}
                    </div>
                  )}
                  <label className="workload-field">
                    <span>
                      Output token target and maximum <em>optional</em>
                    </span>
                    <input
                      aria-label="Output token target and maximum"
                      aria-describedby={
                        [
                          fieldErrors.target_output_tokens ? "error-output-target" : null,
                          fieldErrors.max_output_tokens ? "error-max-output" : null,
                        ]
                          .filter(Boolean)
                          .join(" ") || undefined
                      }
                      aria-invalid={Boolean(
                        fieldErrors.target_output_tokens || fieldErrors.max_output_tokens,
                      )}
                      min="1"
                      max="1000000"
                      onChange={(event) => {
                        update("outputTokens", event.target.value);
                        update("maxOutputTokens", event.target.value);
                      }}
                      type="number"
                      value={form.outputTokens}
                    />
                    {fieldErrors.target_output_tokens && (
                      <FieldError
                        id="error-output-target"
                        message={fieldErrors.target_output_tokens}
                      />
                    )}
                    {fieldErrors.max_output_tokens && (
                      <FieldError id="error-max-output" message={fieldErrors.max_output_tokens} />
                    )}
                    <small>
                      When blank, the target estimate is omitted and the effective output limit is
                      {` ${form.maxOutputTokens.trim() || DEFAULT_MAX_OUTPUT_TOKENS}`} tokens.
                    </small>
                  </label>
                  <label className="workload-field">
                    <span>Prompt counting method</span>
                    <select
                      onChange={(event) =>
                        update(
                          "promptSizingMethod",
                          event.target.value as WorkloadForm["promptSizingMethod"],
                        )
                      }
                      disabled={form.source === "dataset" && !file && Boolean(chosenDataset)}
                      value={form.promptSizingMethod}
                    >
                      <option value="characters">Characters · approximate size</option>
                      <option value="words">Words · approximate size</option>
                      <option value="tokenizer">Tokenizer · unavailable unless installed</option>
                    </select>
                  </label>
                  {form.source === "dataset" && file && form.promptSizingMethod === "tokenizer" && (
                    <label className="workload-field">
                      <span>
                        Tokenizer identity <em>optional</em>
                      </span>
                      <input
                        onChange={(event) => update("tokenizerName", event.target.value)}
                        placeholder="For example, model tokenizer name"
                        value={form.tokenizerName}
                      />
                      <small>
                        The current preflight warns when LOCALFORGE cannot count with this
                        tokenizer.
                      </small>
                    </label>
                  )}
                  <label className="workload-field">
                    <span>
                      Temperature <em>optional</em>
                    </span>
                    <input
                      min="0"
                      onChange={(event) => update("temperature", event.target.value)}
                      step="any"
                      type="number"
                      value={form.temperature}
                    />
                  </label>
                  <label className="workload-field">
                    <span>
                      Top-p <em>optional</em>
                    </span>
                    <input
                      min="0"
                      max="1"
                      onChange={(event) => update("topP", event.target.value)}
                      step="any"
                      type="number"
                      value={form.topP}
                    />
                  </label>
                  <label className="workload-field">
                    <span>
                      Top-k <em>optional</em>
                    </span>
                    <input
                      min="1"
                      onChange={(event) => update("topK", event.target.value)}
                      type="number"
                      value={form.topK}
                    />
                  </label>
                  <label className="workload-check workload-field--wide">
                    <input
                      checked={form.retainPromptOutput}
                      onChange={(event) => update("retainPromptOutput", event.target.checked)}
                      type="checkbox"
                    />
                    <span>
                      Retain prompt and generated output content with this plan. Off by default;
                      timing and aggregate measurements are still recorded.
                    </span>
                  </label>
                </div>
              </fieldset>

              <div className="workload-actions">
                <button className="button button--primary" disabled={busy || !data} type="submit">
                  {busy ? "Checking workload…" : "Review preflight"}
                </button>
              </div>
            </form>
          </section>
          <SavedWorkSection
            data={data}
            busy={busy}
            onReplayPlan={(plan) => void replayPlan(plan)}
            onReplayWorkload={(item) => void replayWorkloadDraft(item)}
          />
        </>
      ) : (
        <section className="panel" aria-labelledby="preflight-title">
          <div className="section-title-row workload-review-title">
            <div>
              <p className="eyebrow">STEP 2 OF 2 · PLAN REVIEW</p>
              <h2 id="preflight-title">Preflight review</h2>
            </div>
            <button
              className="button button--quiet"
              disabled={busy}
              onClick={changeStageToForm}
              type="button"
            >
              Change settings
            </button>
          </div>
          {preflight && (
            <>
              {importResult && workload?.dataset_id === importResult.dataset.id && (
                <DatasetSummary result={importResult} />
              )}
              <div
                className={`preflight-status ${preflight.valid ? "preflight-status--ready" : "preflight-status--blocked"}`}
                role="status"
              >
                {preflight.valid
                  ? "Preflight has no blockers. Estimates are not a guarantee."
                  : `Blocked · ${preflight.blockers.length} issue${preflight.blockers.length === 1 ? "" : "s"} must be resolved before a run can start.`}
              </div>
              <div className="preflight-grid">
                <section className="preflight-card">
                  <h3>Target</h3>
                  <dl>
                    <div>
                      <dt>Engine</dt>
                      <dd>{targetText(preflight.target, "engine_label")}</dd>
                    </div>
                    <div>
                      <dt>Model</dt>
                      <dd>{targetText(preflight.target, "model_name")}</dd>
                    </div>
                    <div>
                      <dt>Machine</dt>
                      <dd>{targetText(preflight.target, "machine_label")}</dd>
                    </div>
                    <div>
                      <dt>Endpoint</dt>
                      <dd>{targetText(preflight.target, "endpoint")}</dd>
                    </div>
                    <div>
                      <dt>Workload</dt>
                      <dd>{targetText(preflight.target, "workload_name")}</dd>
                    </div>
                    <div>
                      <dt>Model context limit</dt>
                      <dd>
                        {typeof preflight.target.model_context_limit === "number"
                          ? `${preflight.target.model_context_limit.toLocaleString()} tokens`
                          : "Unknown"}
                      </dd>
                    </div>
                  </dl>
                </section>
                <section className="preflight-card">
                  <h3>Estimated run</h3>
                  <dl>
                    <div>
                      <dt>Request volume, including warmup</dt>
                      <dd>{formatEstimate(preflight.estimated_request_volume, "requests")}</dd>
                    </div>
                    <div>
                      <dt>Measurement duration</dt>
                      <dd>{formatEstimate(preflight.estimated_duration_seconds, "seconds")}</dd>
                    </div>
                    <div>
                      <dt>Traffic mode</dt>
                      <dd>
                        {workload?.traffic_mode === "open_loop"
                          ? "Open loop · target request rate"
                          : workload?.traffic_mode === "closed_loop"
                            ? "Closed loop · virtual-client concurrency"
                            : "Saved workload"}
                      </dd>
                    </div>
                    <div>
                      <dt>Prompt counting</dt>
                      <dd>
                        {workload?.input_mode === "dataset"
                          ? (workloadDataset?.count_method ?? "Dataset count method unknown")
                          : `${workload?.prompt_sizing_method ?? "See saved configuration"} · approximate generated-input sizing`}
                      </dd>
                    </div>
                    <div>
                      <dt>Input size</dt>
                      <dd>
                        {workload?.input_mode === "dataset"
                          ? "Imported prompt text used as stored"
                          : workload?.target_input_tokens
                            ? `About ${workload.target_input_tokens.toLocaleString()} tokens`
                            : "Default deterministic prompt size"}
                      </dd>
                    </div>
                    <div>
                      <dt>Output token limit</dt>
                      <dd>
                        {workload?.target_output_tokens ?? workload?.max_output_tokens ?? "Unknown"}
                      </dd>
                    </div>
                    <div>
                      <dt>
                        {workload?.input_mode === "dataset"
                          ? "Dataset sample seed"
                          : "Synthetic prompt seed"}
                      </dt>
                      <dd>
                        {workload?.input_mode === "dataset"
                          ? (workloadDataset?.shuffle_seed ?? "Not recorded")
                          : (workload?.random_seed ?? "See saved configuration")}
                      </dd>
                    </div>
                    <div>
                      <dt>Dataset snapshot</dt>
                      <dd className="hash-value">
                        {currentDatasetHash ?? "Synthetic workload or no snapshot"}
                      </dd>
                    </div>
                  </dl>
                </section>
              </div>
              <div className="preflight-grid preflight-grid--lists">
                <IssueList
                  title="Blockers"
                  issues={preflight.blockers}
                  tone="error"
                  empty="No blockers reported."
                />
                <IssueList
                  title="Warnings"
                  issues={preflight.warnings}
                  tone="warning"
                  empty="No warnings reported."
                />
              </div>
              <section className="preflight-card preflight-capabilities">
                <h3>Engine features and resource lock</h3>
                <p>
                  <strong>Supported:</strong>{" "}
                  {preflight.supported_options.join(", ") || "None reported"}
                </p>
                <p>
                  <strong>Unknown:</strong> {preflight.unknown_options.join(", ") || "None"}
                </p>
                <p>
                  <strong>Unsupported:</strong> {preflight.unsupported_options.join(", ") || "None"}
                </p>
                <p>
                  <strong>Resource lock:</strong>{" "}
                  {preflight.resource_contention.length
                    ? preflight.resource_contention.join(" ")
                    : "No active conflict was detected at preflight. The lock is checked again when starting."}
                </p>
                {preflight.missing_prerequisites.length > 0 && (
                  <p>
                    <strong>Missing prerequisites:</strong>{" "}
                    {preflight.missing_prerequisites.join(", ")}
                  </p>
                )}
                {hasContentionWarning && (
                  <label className="workload-check workload-contention-check">
                    <input
                      checked={contentionAcknowledged}
                      onChange={(event) => setContentionAcknowledged(event.target.checked)}
                      type="checkbox"
                    />
                    <span>
                      I accept that another run may contend for this target and want to continue
                      with a potentially contended run.
                    </span>
                  </label>
                )}
              </section>
              {preflight.remote_data_egress && (
                <div className="workload-egress" role="note">
                  <h3>Remote data egress · {preflight.remote_data_egress.host}</h3>
                  <p>{preflight.remote_data_egress.message}</p>
                  <label className="workload-check">
                    <input
                      checked={remoteAcknowledged}
                      onChange={(event) => setRemoteAcknowledged(event.target.checked)}
                      type="checkbox"
                    />
                    <span>
                      I reviewed the destination and approve sending this workload to this remote
                      endpoint.
                    </span>
                  </label>
                </div>
              )}
              {highRiskWarnings.length > 0 && (
                <div className="workload-warning" role="note">
                  <h3>High concurrency notice</h3>
                  <ul>
                    {highRiskWarnings.map((issue) => (
                      <li key={issue.code}>{issue.message}</li>
                    ))}
                  </ul>
                  <label className="workload-check">
                    <input
                      checked={riskAcknowledged}
                      onChange={(event) => setRiskAcknowledged(event.target.checked)}
                      type="checkbox"
                    />
                    <span>
                      I understand this configuration may distort measurements or overload the
                      target.
                    </span>
                  </label>
                </div>
              )}
              {runId && (
                <p className="success-message workload-run-created" role="status">
                  Run queued: <code>{runId}</code>
                  {runContended ? " and marked potentially contended" : ""}.{" "}
                  <a href={`#runs?run=${encodeURIComponent(runId)}`}>Open live run details</a>.
                </p>
              )}
              <div className="workload-actions workload-review-actions">
                {!savedPlan ? (
                  <button
                    className="button button--primary"
                    disabled={busy}
                    onClick={() => void savePlan()}
                    type="button"
                  >
                    {busy ? "Saving plan…" : "Save plan"}
                  </button>
                ) : (
                  <button
                    className="button button--primary"
                    disabled={busy || !canLaunch}
                    onClick={() => void launchRun()}
                    type="button"
                  >
                    {busy
                      ? "Starting run…"
                      : contentionOverrideNeeded
                        ? "Continue with contention"
                        : "Launch benchmark"}
                  </button>
                )}
                {savedPlan && !canLaunch && preflight.valid && (
                  <span className="workload-action-hint">
                    {hasContentionWarning && !contentionAcknowledged
                      ? "Acknowledge the resource lock warning to enable launch."
                      : "Review remote egress and high-risk acknowledgements to enable launch."}
                  </span>
                )}
                {savedPlan && (
                  <span className="workload-action-hint">
                    Plan status: {activePlan?.status ?? "unknown"}. A blocked plan cannot launch.
                  </span>
                )}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function FieldError({ id, message }: { id: string; message: string }) {
  return (
    <span className="field-error" id={id}>
      {message}
    </span>
  );
}

function DatasetSummary({ result }: { result: DatasetImportResponse }) {
  const { dataset } = result;
  function downloadSummary() {
    const summary = {
      dataset_name: dataset.name,
      format: dataset.format,
      parsed_rows: result.parsed_rows,
      selected_rows: result.selected_rows,
      duplicate_rows: result.duplicate_rows,
      rows_excluded_by_limit: result.rows_excluded_by_limit,
      count_method: result.count_method,
      source_hash: dataset.content_hash,
      selected_snapshot_hash: dataset.snapshot_hash,
      content_retained: dataset.content_retained,
      preview: result.preview.map((row) => ({
        row_number: row.row_number,
        prompt_preview: row.prompt_preview,
        prompt_size: row.prompt_size,
        expected_answer_present: row.expected_answer_present,
      })),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(summary, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${dataset.name.replace(/[^a-z0-9-_]+/gi, "-")}-validation-summary.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return (
    <section className="dataset-summary" aria-label="Dataset validation summary" role="status">
      <h4>Dataset validated · preview content redacted</h4>
      <p>
        {result.parsed_rows} parsed · {result.selected_rows} selected · {result.duplicate_rows}{" "}
        duplicates · {result.rows_excluded_by_limit} excluded by row limit.
      </p>
      <p>
        Counting method: {result.count_method}. Source hash:{" "}
        <code>{dataset.content_hash ?? "unavailable"}</code>. Selected snapshot hash:{" "}
        <code>{dataset.snapshot_hash ?? "unavailable"}</code>.
      </p>
      <ul>
        {result.preview.map((row) => (
          <li key={row.row_number}>
            Row {row.row_number}: {row.prompt_preview}
            {row.expected_answer_present ? " · expected answer present" : ""}
          </li>
        ))}
      </ul>
      <p>
        {dataset.content_retained
          ? "Prompt snapshot retained locally for replay."
          : "Only validation metadata retained; this dataset cannot be used to replay requests."}
      </p>
      <button className="button button--quiet" onClick={downloadSummary} type="button">
        Download redacted validation summary
      </button>
    </section>
  );
}

function IssueList({
  title,
  issues,
  tone,
  empty,
}: {
  title: string;
  issues: BenchmarkPreflight["blockers"];
  tone: "error" | "warning";
  empty: string;
}) {
  return (
    <section className={`preflight-card preflight-issues preflight-issues--${tone}`}>
      <h3>
        {title} <span>{issues.length}</span>
      </h3>
      {issues.length ? (
        <ul>
          {issues.map((issue) => (
            <li key={issue.code}>
              <strong>{issueLabel(issue.code)}:</strong> {issue.message}
            </li>
          ))}
        </ul>
      ) : (
        <p>{empty}</p>
      )}
    </section>
  );
}

function SavedWorkSection({
  data,
  busy,
  onReplayPlan,
  onReplayWorkload,
}: {
  data: ScreenData | null;
  busy: boolean;
  onReplayPlan: (plan: BenchmarkPlan) => void;
  onReplayWorkload: (workload: Workload) => void;
}) {
  return (
    <section className="panel saved-work-panel">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">SAVED CONFIGURATIONS</p>
          <h2>Replay a plan or workload</h2>
        </div>
      </div>
      <div className="saved-work-columns">
        <div>
          <h3>Benchmark plans</h3>
          {!data?.plans.length ? (
            <p className="empty-copy">No saved plans yet.</p>
          ) : (
            data.plans.map((plan) => (
              <div className="saved-work-row" key={plan.id}>
                <div>
                  <strong>{plan.name}</strong>
                  <small>
                    {plan.status} · {plan.measured_requests ?? "duration limited"} requests ·{" "}
                    {plan.retain_prompt_output ? "content retained" : "content redacted"}
                  </small>
                </div>
                <button
                  className="button button--quiet"
                  disabled={busy}
                  onClick={() => onReplayPlan(plan)}
                  type="button"
                >
                  Review / replay
                </button>
              </div>
            ))
          )}
        </div>
        <div>
          <h3>Workloads</h3>
          {!data?.workloads.length ? (
            <p className="empty-copy">No saved workloads yet.</p>
          ) : (
            data.workloads.map((workload) => (
              <div className="saved-work-row" key={workload.id}>
                <div>
                  <strong>{workload.name}</strong>
                  <small>
                    {workload.preset ?? workload.input_mode} ·{" "}
                    {workload.traffic_mode.replace("_", " ")} · seed {workload.random_seed}
                  </small>
                </div>
                <button
                  className="button button--quiet"
                  disabled={busy}
                  onClick={() => onReplayWorkload(workload)}
                  type="button"
                >
                  Duplicate workload
                </button>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
