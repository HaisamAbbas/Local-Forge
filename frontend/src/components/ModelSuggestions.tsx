import { useCallback, useEffect, useId, useState } from "react";

import {
  cancelModelPull,
  fetchModelSuggestions,
  getModelPull,
  listModelPulls,
  pullModelWithOllama,
  type ModelPull,
  type ModelPullTarget,
  type ModelSuggestion,
  type ModelSuggestionsResult,
  type ModelUseCase,
} from "../lib/api";
import {
  FIT_LABEL,
  INITIAL_SUGGESTIONS,
  STRENGTH_LABEL,
  USE_CASE_TABS,
  benchmarkHref,
  benchmarkProfileId,
  formatContext,
  formatGb,
  formatParams,
  pullIsActive,
  pullProgressText,
  speedText,
} from "../lib/model-suggestions";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: ModelSuggestionsResult };

/** Per-model pull flow: in-page confirmation, then live progress from the API. */
type PullUi =
  | { stage: "confirm"; targetId: string }
  | { stage: "starting" }
  | { stage: "tracking"; pull: ModelPull }
  | { stage: "error"; message: string };

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function speedBasisText(result: ModelSuggestionsResult): string {
  if (result.speed_basis === "measured")
    return `Speeds are estimated from your runs: this machine streams about ${result.read_gib_per_second} GB of model per second while generating.`;
  if (result.speed_basis === "gpu_bandwidth_rough")
    return "Speeds are rough estimates from your GPU's published memory bandwidth; benchmark to measure them.";
  return "Speeds are unknown until you benchmark a model here; start with a small one and LocalForge will estimate the rest.";
}

/**
 * "Suggested for this machine": catalog models ranked per use case. Pulling is the only action
 * that downloads anything, and it always asks for confirmation in the page first.
 */
export function ModelSuggestions({
  onModelsChanged,
  pollIntervalMs = 1000,
}: {
  onModelsChanged?: () => void;
  pollIntervalMs?: number;
}) {
  const [useCase, setUseCase] = useState<ModelUseCase>("chat");
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [showAll, setShowAll] = useState(false);
  const [pulls, setPulls] = useState<Record<string, PullUi>>({});
  const [reloadKey, setReloadKey] = useState(0);
  const tabsId = useId();

  useEffect(() => {
    let active = true;
    fetchModelSuggestions(useCase).then(
      (value) => {
        if (active) setState({ status: "ready", value });
      },
      (error: unknown) => {
        if (active)
          setState({
            status: "error",
            message: errorText(error, "Model suggestions could not be loaded."),
          });
      },
    );
    return () => {
      active = false;
    };
  }, [useCase, reloadKey]);

  // Pick up pulls that were started before this page was opened.
  useEffect(() => {
    let active = true;
    listModelPulls().then(
      (items) => {
        if (!active || !Array.isArray(items)) return;
        const running = items.filter((pull) => pull.catalog_model_id && pullIsActive(pull));
        if (running.length === 0) return;
        setPulls((current) => {
          const next = { ...current };
          for (const pull of running) next[pull.catalog_model_id!] = { stage: "tracking", pull };
          return next;
        });
      },
      () => undefined,
    );
    return () => {
      active = false;
    };
  }, []);

  // Poll active pulls; each state update re-arms the timer until no pull is active.
  useEffect(() => {
    const trackedActive = Object.entries(pulls).filter(
      (entry): entry is [string, { stage: "tracking"; pull: ModelPull }] =>
        entry[1].stage === "tracking" && pullIsActive(entry[1].pull),
    );
    if (trackedActive.length === 0) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void Promise.all(
        trackedActive.map(async ([modelId, ui]) => {
          try {
            return [modelId, await getModelPull(ui.pull.id)] as const;
          } catch {
            return null;
          }
        }),
      ).then((updates) => {
        if (cancelled) return;
        const finishedOk = updates.some(
          (update) => update && !pullIsActive(update[1]) && update[1].status === "succeeded",
        );
        setPulls((current) => {
          const next = { ...current };
          for (const update of updates) {
            if (!update) continue;
            const [modelId, pull] = update;
            const previous = next[modelId];
            if (previous?.stage !== "tracking" || previous.pull.id !== pull.id) continue;
            next[modelId] = { stage: "tracking", pull };
          }
          return next;
        });
        if (finishedOk) {
          setReloadKey((key) => key + 1);
          onModelsChanged?.();
        }
      });
    }, pollIntervalMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pulls, pollIntervalMs, onModelsChanged]);

  const setPull = useCallback((modelId: string, ui: PullUi | null) => {
    setPulls((current) => {
      const next = { ...current };
      if (ui === null) delete next[modelId];
      else next[modelId] = ui;
      return next;
    });
  }, []);

  const startPull = async (item: ModelSuggestion, targetId: string) => {
    if (!item.sources.ollama_tag) return;
    setPull(item.model_id, { stage: "starting" });
    try {
      const pull = await pullModelWithOllama(targetId, item.sources.ollama_tag);
      setPull(item.model_id, { stage: "tracking", pull });
    } catch (error) {
      setPull(item.model_id, {
        stage: "error",
        message: errorText(error, "Ollama could not start the download."),
      });
    }
  };

  const cancelPull = async (item: ModelSuggestion, pull: ModelPull) => {
    try {
      setPull(item.model_id, { stage: "tracking", pull: await cancelModelPull(pull.id) });
    } catch (error) {
      setPull(item.model_id, {
        stage: "error",
        message: errorText(error, "The download could not be cancelled."),
      });
    }
  };

  const changeUseCase = (next: ModelUseCase) => {
    if (next === useCase) return;
    setUseCase(next);
    setShowAll(false);
    setState({ status: "loading" });
  };

  return (
    <section className="panel models-suggest" aria-labelledby="models-suggest-title">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">MODEL SUGGESTIONS</p>
          <h2 id="models-suggest-title">Suggested for this machine</h2>
          {state.status === "ready" ? (
            <>
              <p className="helper-copy">
                {state.value.machine_summary}. {speedBasisText(state.value)}
              </p>
              <p className="helper-copy models-suggest__method">
                {state.value.method} Catalog {state.value.catalog_version} (
                {state.value.catalog_date}).
              </p>
            </>
          ) : (
            <p className="helper-copy">
              Well-known open models ranked by memory fit and estimated speed on this machine.
            </p>
          )}
        </div>
      </div>
      <div className="models-suggest__tabs" role="tablist" aria-label="Use case">
        {USE_CASE_TABS.map((tab) => (
          <button
            aria-controls={`${tabsId}-panel`}
            aria-selected={tab.id === useCase}
            className={`button ${tab.id === useCase ? "button--primary" : "button--quiet"}`}
            key={tab.id}
            onClick={() => changeUseCase(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div id={`${tabsId}-panel`} role="tabpanel" aria-label={`${useCase} suggestions`}>
        {state.status === "loading" ? (
          <p role="status" aria-busy="true">
            Ranking models for this machine…
          </p>
        ) : state.status === "error" ? (
          <div className="state-message state-message--error" role="alert">
            <span>{state.message}</span>
            <button
              className="button button--quiet"
              onClick={() => setReloadKey((key) => key + 1)}
              type="button"
            >
              Retry
            </button>
          </div>
        ) : (
          <SuggestionList
            result={state.value}
            showAll={showAll}
            onToggleAll={() => setShowAll((value) => !value)}
            pulls={pulls}
            onAskPull={(item, targetId) => setPull(item.model_id, { stage: "confirm", targetId })}
            onChooseTarget={(item, targetId) =>
              setPull(item.model_id, { stage: "confirm", targetId })
            }
            onConfirmPull={(item, targetId) => void startPull(item, targetId)}
            onDismiss={(item) => setPull(item.model_id, null)}
            onCancelPull={(item, pull) => void cancelPull(item, pull)}
          />
        )}
      </div>
    </section>
  );
}

type ListActions = {
  pulls: Record<string, PullUi>;
  onAskPull: (item: ModelSuggestion, targetId: string) => void;
  onChooseTarget: (item: ModelSuggestion, targetId: string) => void;
  onConfirmPull: (item: ModelSuggestion, targetId: string) => void;
  onDismiss: (item: ModelSuggestion) => void;
  onCancelPull: (item: ModelSuggestion, pull: ModelPull) => void;
};

function SuggestionList({
  result,
  showAll,
  onToggleAll,
  ...actions
}: ListActions & {
  result: ModelSuggestionsResult;
  showAll: boolean;
  onToggleAll: () => void;
}) {
  const usable = result.suggestions.filter((item) => item.fit !== "too_large");
  const shown = showAll ? result.suggestions : usable.slice(0, INITIAL_SUGGESTIONS);
  const hidden = result.suggestions.length - shown.length;
  if (result.suggestions.length === 0)
    return <p className="empty-inline">No catalog models match this use case.</p>;
  return (
    <>
      {usable.length === 0 && !showAll && (
        <p className="empty-inline">
          No catalog model for this use case fits this machine&apos;s memory.
        </p>
      )}
      <ol className="models-suggest__list" aria-label={`${result.use_case_label} models`}>
        {shown.map((item) => (
          <SuggestionCard
            item={item}
            key={item.model_id}
            targets={result.pull_targets}
            {...actions}
          />
        ))}
      </ol>
      {(hidden > 0 || showAll) && (
        <button className="button button--quiet" onClick={onToggleAll} type="button">
          {showAll
            ? "Show fewer"
            : `Show all ${result.suggestions.length}, including ones that do not fit`}
        </button>
      )}
    </>
  );
}

function SuggestionCard({
  item,
  targets,
  pulls,
  onAskPull,
  onChooseTarget,
  onConfirmPull,
  onDismiss,
  onCancelPull,
}: ListActions & { item: ModelSuggestion; targets: ModelPullTarget[] }) {
  const titleId = useId();
  const pullUi = pulls[item.model_id];
  const profileId = benchmarkProfileId(item);
  const enabledTargets = targets.filter((target) => target.enabled);
  const pulledProfile =
    pullUi?.stage === "tracking" && pullUi.pull.status === "succeeded"
      ? pullUi.pull.engine_profile_id
      : null;
  const benchmarkProfile = profileId ?? pulledProfile;
  return (
    <li className="models-suggest__card" aria-labelledby={titleId}>
      <div className="models-suggest__header">
        <span className="engine-recs__rank" aria-label={`Rank ${item.rank}`}>
          {item.rank}
        </span>
        <h3 id={titleId}>{item.name}</h3>
        <span className={`models-suggest__fit models-suggest__fit--${item.fit}`}>
          {FIT_LABEL[item.fit]}
        </span>
        {item.downloaded.length > 0 && (
          <span className="models-suggest__downloaded">Downloaded</span>
        )}
      </div>
      <p className="models-suggest__meta">
        {formatParams(item)} · {formatGb(item.download_size_gb)} download · needs about{" "}
        {formatGb(item.memory_required_gb)} · {formatContext(item.context_tokens)} · {item.license}
      </p>
      <p className="models-suggest__speed">
        <strong>{speedText(item)}</strong> · {item.speed.label}
        {item.speed.verdict ? ` · ${item.speed.verdict}` : ""}
      </p>
      <ul className="models-suggest__strengths" aria-label="Strengths">
        {item.strengths.map((strength) => (
          <li key={strength}>{STRENGTH_LABEL[strength] ?? strength}</li>
        ))}
      </ul>
      {item.reasons.length > 0 && (
        <ul className="engine-recs__reasons">
          {item.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {item.cautions.length > 0 && (
        <ul className="engine-recs__cautions" aria-label="Cautions">
          {item.cautions.map((caution) => (
            <li key={caution}>
              <span className="engine-recs__caution-label">Caution:</span> {caution}
            </li>
          ))}
        </ul>
      )}
      {item.notes && <p className="helper-copy">{item.notes}</p>}
      <div className="models-suggest__actions">
        {benchmarkProfile && (
          <a className="button button--primary" href={benchmarkHref(benchmarkProfile)}>
            Benchmark
          </a>
        )}
        {(!benchmarkProfile || pulledProfile) && item.sources.ollama_tag && (
          <PullAction
            item={item}
            targets={enabledTargets}
            anyTargets={targets.length > 0}
            pullUi={pullUi}
            onAskPull={onAskPull}
            onChooseTarget={onChooseTarget}
            onConfirmPull={onConfirmPull}
            onDismiss={onDismiss}
            onCancelPull={onCancelPull}
          />
        )}
        {item.sources.gguf_url && (
          <a
            className="button button--quiet"
            href={item.sources.gguf_url}
            rel="noreferrer"
            target="_blank"
          >
            Get the GGUF
          </a>
        )}
      </div>
      {item.sources.gguf_url && item.sources.gguf_file && (
        <details className="engine-recs__more">
          <summary>Use with llama.cpp or KoboldCpp</summary>
          <p className="helper-copy">
            On Hugging Face, open <strong>Files</strong> and download{" "}
            <code>{item.sources.gguf_file}</code>. Then, in{" "}
            <a className="text-link" href="#engines">
              Engines
            </a>
            , start llama.cpp (llama-server) or KoboldCpp with that file selected as the model.
          </p>
        </details>
      )}
    </li>
  );
}

function PullAction({
  item,
  targets,
  anyTargets,
  pullUi,
  onAskPull,
  onChooseTarget,
  onConfirmPull,
  onDismiss,
  onCancelPull,
}: Omit<ListActions, "pulls"> & {
  item: ModelSuggestion;
  targets: ModelPullTarget[];
  anyTargets: boolean;
  pullUi: PullUi | undefined;
}) {
  const tag = item.sources.ollama_tag!;
  if (targets.length === 0) {
    return (
      <p className="helper-copy models-suggest__note">
        {anyTargets
          ? "Enable your local Ollama engine to pull models with it. "
          : "Add a local Ollama engine to pull this model. "}
        <a className="text-link" href="#engines">
          Open Engines
        </a>
      </p>
    );
  }
  if (!pullUi) {
    return (
      <button
        className="button button--primary"
        onClick={() => onAskPull(item, targets[0]!.engine_profile_id)}
        type="button"
      >
        Pull with Ollama
      </button>
    );
  }
  if (pullUi.stage === "confirm") {
    const target = targets.find((entry) => entry.engine_profile_id === pullUi.targetId);
    return (
      <div
        className="notice models-suggest__confirm"
        role="group"
        aria-label={`Confirm download of ${item.name}`}
      >
        <p>
          Download about <strong>{formatGb(item.download_size_gb)}</strong> (<code>{tag}</code>)
          with Ollama{target ? ` on ${target.label}` : ""}? Ollama fetches it from its registry
          (registry.ollama.ai) and stores it in its own model folder.
        </p>
        {targets.length > 1 && (
          <label className="models-suggest__target">
            Ollama engine
            <select
              onChange={(event) => onChooseTarget(item, event.target.value)}
              value={pullUi.targetId}
            >
              {targets.map((entry) => (
                <option key={entry.engine_profile_id} value={entry.engine_profile_id}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="models-group-actions">
          <button
            className="button button--primary"
            onClick={() => onConfirmPull(item, pullUi.targetId)}
            type="button"
          >
            Download {formatGb(item.download_size_gb)}
          </button>
          <button className="button button--quiet" onClick={() => onDismiss(item)} type="button">
            Cancel
          </button>
        </div>
      </div>
    );
  }
  if (pullUi.stage === "starting") {
    return (
      <p role="status" aria-busy="true">
        Asking Ollama to start the download…
      </p>
    );
  }
  if (pullUi.stage === "error") {
    return (
      <div className="models-suggest__progress">
        <p className="form-error" role="alert">
          {pullUi.message}
        </p>
        <button
          className="button button--quiet"
          onClick={() => onAskPull(item, targets[0]!.engine_profile_id)}
          type="button"
        >
          Try again
        </button>
      </div>
    );
  }
  const pull = pullUi.pull;
  if (pull.status === "running") {
    return (
      <div className="models-suggest__progress">
        <progress
          aria-label={`Download progress for ${item.name}`}
          max={100}
          value={pull.percent ?? undefined}
        />
        <p className="helper-copy" role="status">
          Downloading with Ollama · {pullProgressText(pull)}
        </p>
        <button
          className="button button--quiet"
          onClick={() => onCancelPull(item, pull)}
          type="button"
        >
          Cancel download
        </button>
      </div>
    );
  }
  if (pull.status === "succeeded") {
    return (
      <p className="models-refresh-note" role="status">
        Downloaded with Ollama.{" "}
        {pull.probe_status === "running" ? "Refreshing the engine…" : (pull.probe_message ?? "")}
      </p>
    );
  }
  return (
    <div className="models-suggest__progress">
      {pull.status === "failed" ? (
        <p className="form-error" role="alert">
          The download failed: {pull.error ?? "Ollama reported an error."}
        </p>
      ) : (
        <p className="helper-copy" role="status">
          Download cancelled. Ollama keeps the parts it already fetched and resumes from them.
        </p>
      )}
      <button
        className="button button--quiet"
        onClick={() => onAskPull(item, targets[0]!.engine_profile_id)}
        type="button"
      >
        Pull with Ollama
      </button>
    </div>
  );
}
