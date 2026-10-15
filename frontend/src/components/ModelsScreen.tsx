import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  listEngineProfiles,
  listModels,
  probeEngineProfile,
  type EngineProfile,
  type ModelRecord,
} from "../lib/api";
import { ModelSuggestions } from "./ModelSuggestions";

type Inventory = { models: ModelRecord[]; profiles: EngineProfile[] };
type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: Inventory };

type RefreshState =
  | { status: "busy" }
  | { status: "done"; message: string }
  | { status: "error"; message: string }
  | { status: "confirm_remote"; host: string; message: string };

type EngineGroup = {
  id: string;
  label: string;
  profile: EngineProfile | null;
  models: ModelRecord[];
};

const UNKNOWN = "Unknown";

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function formatSize(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return UNKNOWN;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${amount.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unitIndex]}`;
}

function formatContext(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return UNKNOWN;
  return `${value.toLocaleString()} tokens`;
}

function text(value: string | null | undefined): string {
  return value === null || value === undefined || value.trim() === "" ? UNKNOWN : value;
}

function statusClass(status: string): string {
  if (status === "available") return "enabled";
  if (status === "missing") return "needs_attention";
  return "disabled";
}

function statusText(status: string): string {
  return status.replaceAll("_", " ").replace(/^\w/, (letter) => letter.toUpperCase());
}

async function loadInventory(): Promise<Inventory> {
  const [models, profiles] = await Promise.all([listModels(), listEngineProfiles()]);
  return { models, profiles };
}

/** Engine profiles first (in their saved order), then any models whose profile is not listed. */
function groupByEngine({ models, profiles }: Inventory): EngineGroup[] {
  const groups = new Map<string, EngineGroup>();
  for (const profile of profiles) {
    groups.set(profile.id, { id: profile.id, label: profile.label, profile, models: [] });
  }
  for (const model of models) {
    let group = groups.get(model.engine_profile_id);
    if (!group) {
      group = {
        id: model.engine_profile_id,
        label: model.engine_label || `Engine ${model.engine_profile_id.slice(0, 8)}`,
        profile: null,
        models: [],
      };
      groups.set(model.engine_profile_id, group);
    }
    group.models.push(model);
  }
  return [...groups.values()];
}

/**
 * Suggested models for this machine, then models visible to connected engines grouped by engine
 * profile. Refreshing re-probes the engine. Models are downloaded only through a confirmed
 * "Pull with Ollama"; LOCALFORGE never removes models.
 */
export function ModelsScreen({ pullPollIntervalMs }: { pullPollIntervalMs?: number } = {}) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [refresh, setRefresh] = useState<Record<string, RefreshState>>({});

  const reload = useCallback(async (showLoading = true) => {
    if (showLoading) setState({ status: "loading" });
    try {
      setState({ status: "ready", value: await loadInventory() });
    } catch (error) {
      setState({ status: "error", message: errorText(error, "Models could not be loaded.") });
    }
  }, []);

  useEffect(() => {
    let active = true;
    loadInventory().then(
      (value) => {
        if (active) setState({ status: "ready", value });
      },
      (error: unknown) => {
        if (active)
          setState({ status: "error", message: errorText(error, "Models could not be loaded.") });
      },
    );
    return () => {
      active = false;
    };
  }, []);

  const inventoryChanged = useCallback(() => void reload(false), [reload]);

  const refreshEngine = async (profileId: string, confirmRemote: boolean) => {
    setRefresh((current) => ({ ...current, [profileId]: { status: "busy" } }));
    try {
      const response = await probeEngineProfile(profileId, confirmRemote);
      const count = response.result.models.length;
      setRefresh((current) => ({
        ...current,
        [profileId]: {
          status: response.result.status === "succeeded" ? "done" : "error",
          message:
            response.result.status === "succeeded"
              ? `Probe succeeded · ${count} ${count === 1 ? "model" : "models"} reported.`
              : "The probe did not succeed. Open the engine for diagnostics.",
        },
      }));
      await reload(false);
    } catch (error) {
      if (error instanceof ApiError && error.code === "remote_confirmation_required") {
        setRefresh((current) => ({
          ...current,
          [profileId]: {
            status: "confirm_remote",
            host: error.host || "the configured remote host",
            message: error.message,
          },
        }));
      } else {
        setRefresh((current) => ({
          ...current,
          [profileId]: {
            status: "error",
            message: errorText(error, "The engine could not be refreshed."),
          },
        }));
      }
    }
  };

  return (
    <div className="screen-stack">
      <ModelSuggestions onModelsChanged={inventoryChanged} pollIntervalMs={pullPollIntervalMs} />
      <section className="panel models-panel" aria-labelledby="models-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">MODEL INVENTORY</p>
            <h2 id="models-title">Models visible to connected engines</h2>
            <p className="helper-copy">
              Inventory comes from engine probes. LOCALFORGE downloads a model only when you confirm
              Pull with Ollama above, and never removes models; install others with the engine
              itself, then refresh. Values the engine does not report are shown as unknown.
            </p>
          </div>
          <button className="button button--quiet" onClick={() => void reload()} type="button">
            Refresh list
          </button>
        </div>
        {state.status === "loading" ? (
          <p role="status" aria-busy="true">
            Loading models…
          </p>
        ) : state.status === "error" ? (
          <div className="state-message state-message--error" role="alert">
            <span>{state.message}</span>
            <button className="button button--quiet" onClick={() => void reload()} type="button">
              Retry
            </button>
          </div>
        ) : (
          <EngineGroups
            groups={groupByEngine(state.value)}
            refresh={refresh}
            onRefresh={(id, confirmRemote) => void refreshEngine(id, confirmRemote)}
            onCancelRemote={(id) =>
              setRefresh((current) => {
                const next = { ...current };
                delete next[id];
                return next;
              })
            }
          />
        )}
      </section>
    </div>
  );
}

function EngineGroups({
  groups,
  refresh,
  onRefresh,
  onCancelRemote,
}: {
  groups: EngineGroup[];
  refresh: Record<string, RefreshState>;
  onRefresh: (id: string, confirmRemote: boolean) => void;
  onCancelRemote: (id: string) => void;
}) {
  if (groups.length === 0) {
    return (
      <div className="empty-state models-empty">
        <div>
          <h3>No engines connected</h3>
          <p>Add and probe an engine to see the models it can serve.</p>
        </div>
        <a className="button button--primary" href="#engines">
          Open Engines
        </a>
      </div>
    );
  }
  return (
    <div className="models-groups">
      {groups.map((group) => {
        const headingId = `models-engine-${group.id}`;
        const refreshState = refresh[group.id];
        const profile = group.profile;
        const notRunning =
          profile?.integration_mode === "managed_process" && !profile.managed_process_running;
        return (
          <section className="models-group" aria-labelledby={headingId} key={group.id}>
            <div className="models-group-header">
              <div>
                <h3 id={headingId}>{group.label}</h3>
                <p className="helper-copy">
                  {profile
                    ? `${profile.engine_display_name || profile.adapter_id} · ${statusText(profile.status)}`
                    : "Engine profile not found"}
                  {" · "}
                  {group.models.length} {group.models.length === 1 ? "model" : "models"}
                </p>
              </div>
              <div className="models-group-actions">
                <a className="text-link" href="#engines">
                  View engine
                </a>
                {profile && (
                  <button
                    aria-label={`Refresh from engine ${group.label}`}
                    className="button button--quiet"
                    disabled={refreshState?.status === "busy" || notRunning}
                    onClick={() => onRefresh(group.id, false)}
                    title={notRunning ? "Start the managed server on Engines first." : undefined}
                    type="button"
                  >
                    {refreshState?.status === "busy" ? "Refreshing…" : "Refresh from engine"}
                  </button>
                )}
              </div>
            </div>
            {refreshState?.status === "done" && (
              <p className="models-refresh-note" role="status">
                {refreshState.message}
              </p>
            )}
            {refreshState?.status === "error" && (
              <p className="form-error" role="alert">
                {refreshState.message}
              </p>
            )}
            {refreshState?.status === "confirm_remote" && (
              <div className="notice notice--warning models-remote-confirm">
                <p>
                  Refreshing contacts <strong>{refreshState.host}</strong>. {refreshState.message}
                </p>
                <div className="models-group-actions">
                  <button
                    className="button button--primary"
                    onClick={() => onRefresh(group.id, true)}
                    type="button"
                  >
                    Contact remote engine
                  </button>
                  <button
                    className="button button--quiet"
                    onClick={() => onCancelRemote(group.id)}
                    type="button"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {group.models.length === 0 ? (
              <p className="empty-inline">
                No models recorded for this engine. Refresh from the engine after installing models,
                or check its model inventory capability.
              </p>
            ) : (
              <div className="table-scroll">
                <table className="models-table">
                  <caption className="visually-hidden">Models served by {group.label}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Model</th>
                      <th scope="col">Status</th>
                      <th scope="col">Format</th>
                      <th scope="col">Quantization</th>
                      <th scope="col">Size</th>
                      <th scope="col">Context length</th>
                      <th scope="col">Revision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.models.map((model) => (
                      <tr key={model.id}>
                        <th scope="row">
                          <span className="models-name">
                            {model.display_name || model.model_ref || UNKNOWN}
                          </span>
                          <code className="models-ref">{text(model.model_ref)}</code>
                        </th>
                        <td>
                          <span className={`status-pill status-pill--${statusClass(model.status)}`}>
                            {statusText(model.status)}
                          </span>
                        </td>
                        <td>{text(model.format)}</td>
                        <td>{text(model.quantization)}</td>
                        <td>{formatSize(model.size_bytes)}</td>
                        <td>{formatContext(model.max_context_tokens)}</td>
                        <td>{text(model.revision)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
