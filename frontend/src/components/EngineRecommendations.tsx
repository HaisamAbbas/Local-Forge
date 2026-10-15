import { useRef, useState } from "react";

import type { EngineRecommendation, EngineRecommendationsResult } from "../lib/api";
import { engineDeepLink } from "../lib/engine-onboarding";
import {
  FIT_LABEL,
  INSTALL_STATUS_LABEL,
  INSTALL_STATUS_PILL,
  MODEL_SUGGESTIONS_HREF,
  addRecommendedEngine,
  measuredText,
  readStoredFlag,
  recommendationAction,
  splitRecommendations,
  useEngineRecommendations,
  writeStoredFlag,
  type AddOutcome,
} from "../lib/engine-recommendations";

const TOP_COUNT = 3;
const VISIBLE_NOTES = 2;

type AddState = { status: "adding" } | { status: "error"; message: string };

function runHref(runId: string): string {
  return `#runs?run=${encodeURIComponent(runId)}`;
}

function CopyCommand({ command, engineName }: { command: string; engineName: string }) {
  const codeRef = useRef<HTMLElement>(null);
  const [message, setMessage] = useState("");
  const selectCommand = () => {
    const node = codeRef.current;
    const selection = typeof window.getSelection === "function" ? window.getSelection() : null;
    if (!node || !selection || typeof document.createRange !== "function") return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  };
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(command);
      setMessage("Command copied.");
    } catch {
      selectCommand();
      setMessage("Copying is not available here. The command is selected; copy it manually.");
    }
  };
  return (
    <div className="engine-recs__command">
      <code ref={codeRef}>{command}</code>
      <button
        aria-label={`Copy install command for ${engineName}`}
        className="button button--quiet"
        onClick={() => void copy()}
        type="button"
      >
        Copy
      </button>
      <span className="engine-recs__copy-status" role="status" aria-live="polite">
        {message}
      </span>
    </div>
  );
}

function NoteList({ items, className }: { items: string[]; className: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={className}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function RecommendationRow({
  item,
  addedProfileId,
  addState,
  onAdd,
  canAddManaged,
  onAddManaged,
}: {
  item: EngineRecommendation;
  addedProfileId?: string;
  addState?: AddState;
  onAdd: (item: EngineRecommendation) => void;
  canAddManaged?: (adapterId: string) => boolean;
  onAddManaged?: (item: EngineRecommendation) => void;
}) {
  const action = recommendationAction(item, addedProfileId);
  const measured = measuredText(item);
  const shownReasons = item.reasons.slice(0, VISIBLE_NOTES);
  const shownCautions = item.cautions.slice(0, VISIBLE_NOTES);
  const moreReasons = item.reasons.slice(VISIBLE_NOTES);
  const moreCautions = item.cautions.slice(VISIBLE_NOTES);
  const moreCount = moreReasons.length + moreCautions.length;
  const managedAvailable =
    item.adapter_id === "localforge.llama_cpp" &&
    onAddManaged !== undefined &&
    (canAddManaged?.(item.adapter_id) ?? false);
  const titleId = `engine-rec-${item.adapter_id.replace(/[^A-Za-z0-9_-]/g, "-")}`;

  return (
    <li className="engine-recs__item" aria-labelledby={titleId}>
      <div className="engine-recs__item-header">
        <span className="engine-recs__rank" aria-label={`Rank ${item.rank}`}>
          {item.rank}
        </span>
        <h3 id={titleId}>{item.display_name}</h3>
        <span className={`engine-recs__fit engine-recs__fit--${item.fit}`}>
          {FIT_LABEL[item.fit]}
        </span>
        <span className={`status-pill ${INSTALL_STATUS_PILL[item.status]}`}>
          {INSTALL_STATUS_LABEL[item.status]}
        </span>
      </div>
      <NoteList className="engine-recs__reasons" items={shownReasons} />
      {shownCautions.length > 0 && (
        <ul className="engine-recs__cautions" aria-label="Cautions">
          {shownCautions.map((caution) => (
            <li key={caution}>
              <span className="engine-recs__caution-label">Caution:</span> {caution}
            </li>
          ))}
        </ul>
      )}
      {moreCount > 0 && (
        <details className="engine-recs__more">
          <summary>
            {moreCount} more {moreCount === 1 ? "note" : "notes"}
          </summary>
          <NoteList className="engine-recs__reasons" items={moreReasons} />
          {moreCautions.length > 0 && (
            <ul className="engine-recs__cautions" aria-label="More cautions">
              {moreCautions.map((caution) => (
                <li key={caution}>
                  <span className="engine-recs__caution-label">Caution:</span> {caution}
                </li>
              ))}
            </ul>
          )}
        </details>
      )}
      <p className="engine-recs__measured">
        {measured && item.measured?.run_id ? (
          <a className="engine-recs__measured-badge" href={runHref(item.measured.run_id)}>
            {measured}
          </a>
        ) : measured ? (
          <span className="engine-recs__measured-badge">{measured}</span>
        ) : (
          <span className="engine-recs__unmeasured">Not measured on this machine yet</span>
        )}
      </p>
      {addState?.status === "error" && (
        <p className="form-error" role="alert">
          {addState.message}
        </p>
      )}
      <div className="engine-recs__action">
        {action.kind === "add" && (
          <button
            aria-label={
              addState?.status === "adding"
                ? `Adding ${item.display_name} on this machine`
                : `Add ${item.display_name} on this machine`
            }
            className="button button--primary"
            disabled={addState?.status === "adding"}
            onClick={() => onAdd(item)}
            type="button"
          >
            {addState?.status === "adding" ? "Adding…" : "Add"}
          </button>
        )}
        {action.kind === "benchmark" && (
          <a
            aria-label={`Benchmark ${item.display_name}`}
            className="button button--primary"
            href={engineDeepLink("workloads", action.profileId)}
          >
            Benchmark
          </a>
        )}
        {action.kind === "start" && (
          <details className="engine-recs__start">
            <summary className="button button--quiet">How to start it</summary>
            {item.status_detail && <p>{item.status_detail}</p>}
            <p>{item.install.summary}</p>
            <div className="engine-recs__start-actions">
              {managedAvailable && (
                <button
                  className="button button--primary"
                  onClick={() => onAddManaged?.(item)}
                  type="button"
                >
                  Add as managed
                </button>
              )}
              {item.install.url && (
                <a
                  className="text-link"
                  href={item.install.url}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  {item.display_name} setup guide
                </a>
              )}
            </div>
          </details>
        )}
        {action.kind === "install" && (
          <div className="engine-recs__install">
            <a
              aria-label={`Install guide for ${item.display_name} (opens in a new tab)`}
              className="button button--quiet"
              href={item.install.url}
              rel="noopener noreferrer"
              target="_blank"
            >
              Install guide
            </a>
            {item.install.summary && <p className="helper-copy">{item.install.summary}</p>}
            {item.install.command && (
              <CopyCommand command={item.install.command} engineName={item.display_name} />
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function unsuitableReason(item: EngineRecommendation): string {
  return item.cautions[0] ?? item.reasons[0] ?? "It does not fit this machine's hardware.";
}

function RecommendationsBody({
  value,
  compact,
  addedProfiles,
  addStates,
  onAdd,
  canAddManaged,
  onAddManaged,
}: {
  value: EngineRecommendationsResult;
  compact: boolean;
  addedProfiles: Record<string, string>;
  addStates: Record<string, AddState>;
  onAdd: (item: EngineRecommendation) => void;
  canAddManaged?: (adapterId: string) => boolean;
  onAddManaged?: (item: EngineRecommendation) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const { suitable, unsuitable } = splitRecommendations(value.recommendations);
  if (value.recommendations.length === 0) {
    return (
      <p className="empty-inline">
        No engines could be ranked for this machine yet. Refresh after installing or starting an
        engine, or add one manually.
      </p>
    );
  }
  const visible = showAll && !compact ? suitable : suitable.slice(0, TOP_COUNT);
  const rowProps = (item: EngineRecommendation) => ({
    item,
    addedProfileId: addedProfiles[item.adapter_id],
    addState: addStates[item.adapter_id],
    onAdd,
    canAddManaged,
    onAddManaged,
  });
  return (
    <>
      {suitable.length === 0 ? (
        <p className="empty-inline">None of the known engines suit this machine.</p>
      ) : (
        <ol className="engine-recs__list" aria-label="Engines ranked for this machine">
          {visible.map((item) => (
            <RecommendationRow key={item.adapter_id} {...rowProps(item)} />
          ))}
        </ol>
      )}
      {!compact && suitable.length > TOP_COUNT && (
        <button
          aria-expanded={showAll}
          className="button button--quiet engine-recs__toggle"
          onClick={() => setShowAll((current) => !current)}
          type="button"
        >
          {showAll ? `Show top ${TOP_COUNT} only` : `Show all ${suitable.length} engines`}
        </button>
      )}
      {compact && (suitable.length > TOP_COUNT || unsuitable.length > 0) && (
        <a className="text-link engine-recs__see-all" href="#engines">
          See all {value.recommendations.length} engines on the Engines screen
        </a>
      )}
      {!compact && unsuitable.length > 0 && (
        <details className="engine-recs__unsuitable">
          <summary>Not for this machine ({unsuitable.length})</summary>
          <ul>
            {unsuitable.map((item) => (
              <li key={item.adapter_id}>
                <strong>{item.display_name}</strong>{" "}
                <span className="engine-recs__fit engine-recs__fit--unsuitable">
                  {FIT_LABEL.unsuitable}
                </span>
                <span className="engine-recs__unsuitable-reason">{unsuitableReason(item)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/**
 * "Which engines suit this machine": a ranked list from the recommendations endpoint with the one
 * action that gets each engine going. Rankings come from hardware fit and what is installed; a
 * speed is only shown when a benchmark on this machine measured it.
 */
export function EngineRecommendations({
  compact = false,
  collapseStorageKey,
  onAdd,
  onAdded,
  canAddManaged,
  onAddManaged,
}: {
  /** Top 3 only, with a link to the Engines screen for the rest. */
  compact?: boolean;
  /** When set, the section can collapse and remembers that choice under this key. */
  collapseStorageKey?: string;
  /** Replaces the built-in create → probe → enable flow (the Engines screen supplies its own). */
  onAdd?: (item: EngineRecommendation) => Promise<AddOutcome>;
  onAdded?: (outcome: AddOutcome) => void;
  canAddManaged?: (adapterId: string) => boolean;
  onAddManaged?: (item: EngineRecommendation) => void;
}) {
  const [collapsed, setCollapsed] = useState(() =>
    collapseStorageKey ? readStoredFlag(collapseStorageKey) : false,
  );
  const { state, reload } = useEngineRecommendations(!collapsed);
  const [addedProfiles, setAddedProfiles] = useState<Record<string, string>>({});
  const [addStates, setAddStates] = useState<Record<string, AddState>>({});
  const [liveMessage, setLiveMessage] = useState("");

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    if (collapseStorageKey) writeStoredFlag(collapseStorageKey, next);
  };

  const refresh = async () => {
    setLiveMessage("Checking this computer for running engines…");
    await reload();
    setLiveMessage("Engine recommendations updated.");
  };

  const add = async (item: EngineRecommendation) => {
    const key = item.adapter_id;
    setAddStates((current) => ({ ...current, [key]: { status: "adding" } }));
    setLiveMessage(`Adding ${item.display_name}…`);
    try {
      const outcome = await (onAdd ?? addRecommendedEngine)(item);
      setAddedProfiles((current) => ({ ...current, [key]: outcome.profileId }));
      setAddStates((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setLiveMessage(outcome.message);
      onAdded?.(outcome);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not add this engine.";
      setAddStates((current) => ({ ...current, [key]: { status: "error", message } }));
      setLiveMessage("");
    }
  };

  const refreshing = state.status === "loading" || (state.status === "ready" && state.refreshing);
  return (
    <section
      className={`panel engine-recs${compact ? " engine-recs--compact" : ""}`}
      aria-labelledby="engine-recs-title"
    >
      <div className="section-title-row">
        <div>
          <p className="eyebrow">ENGINE RECOMMENDATIONS</p>
          <h2 id="engine-recs-title">Best engine fit for this machine</h2>
        </div>
        <div className="engine-recs__header-actions">
          {!collapsed && (
            <button
              aria-label="Refresh engine recommendations"
              className="button button--quiet"
              disabled={refreshing}
              onClick={() => void refresh()}
              type="button"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          )}
          {collapseStorageKey && (
            <button
              aria-controls="engine-recs-content"
              aria-expanded={!collapsed}
              className="button button--quiet"
              onClick={toggleCollapsed}
              type="button"
            >
              {collapsed ? "Show recommendations" : "Hide recommendations"}
            </button>
          )}
        </div>
      </div>
      <p className="visually-hidden" role="status" aria-live="polite">
        {liveMessage}
      </p>
      {!collapsed && (
        <div id="engine-recs-content">
          {state.status === "loading" && (
            <p className="state-message" aria-busy="true">
              Checking this machine and the engines on it…
            </p>
          )}
          {state.status === "error" && (
            <div className="state-message state-message--error" role="alert">
              <span>Engine recommendations could not be loaded. {state.message}</span>
              <button className="button button--quiet" onClick={() => void reload()} type="button">
                Try again
              </button>
            </div>
          )}
          {state.status === "ready" && (
            <>
              <div className="engine-recs__machine">
                <p>
                  <strong>{state.value.machine.summary}</strong>
                </p>
                {state.value.model_size_guidance.text && (
                  <p>{state.value.model_size_guidance.text}</p>
                )}
                <p>
                  <a className="text-link" href={MODEL_SUGGESTIONS_HREF}>
                    Which model should I run?
                  </a>
                </p>
                <p className="helper-copy engine-recs__method">{state.value.method}</p>
              </div>
              <RecommendationsBody
                addStates={addStates}
                addedProfiles={addedProfiles}
                canAddManaged={canAddManaged}
                compact={compact}
                onAdd={(item) => void add(item)}
                onAddManaged={onAddManaged}
                value={state.value}
              />
            </>
          )}
        </div>
      )}
    </section>
  );
}
