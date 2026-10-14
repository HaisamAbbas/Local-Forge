import type {
  AdapterManifest,
  DiscoveredEngine,
  DiscoveryPortStatus,
  EngineProfile,
  ProbeResult,
} from "../lib/api";
import type { CandidateAddState, DiscoveryState } from "../lib/engine-discovery";
import { engineDeepLink, engineSetupSteps, type EngineSetupStep } from "../lib/engine-onboarding";

const PORT_STATUS_LABEL: Record<DiscoveryPortStatus, string> = {
  found: "engine found",
  no_response: "nothing answering",
  unrecognized: "answered, but not a known engine",
  skipped_self: "skipped (LOCALFORGE itself)",
};

const MODEL_PREVIEW_COUNT = 3;

function modelSummary(models: string[]): string {
  if (models.length === 0) return "No models reported yet";
  // Servers such as llama-server list the loaded file path as the model id; show its file name.
  const shown = models
    .slice(0, MODEL_PREVIEW_COUNT)
    .map((model) =>
      /^([A-Za-z]:[\\/]|\/)|\\/.test(model)
        ? (model.replace(/\\/g, "/").split("/").pop() ?? model)
        : model,
    )
    .join(", ");
  const rest = models.length - MODEL_PREVIEW_COUNT;
  const count = models.length === 1 ? "1 model" : `${models.length} models`;
  return `${count}: ${shown}${rest > 0 ? ` and ${rest} more` : ""}`;
}

export function EngineDiscoveryPanel({
  state,
  manifests,
  profiles,
  addedByEndpoint,
  addStates,
  onAdd,
  onAddManually,
  onDismiss,
  onScan,
  onShowProfile,
}: {
  state: DiscoveryState;
  manifests: Map<string, AdapterManifest>;
  profiles: EngineProfile[];
  /** Profiles created from this scan, by endpoint, so their rows switch to "Already added". */
  addedByEndpoint: Record<string, string>;
  addStates: Record<string, CandidateAddState>;
  onAdd: (candidate: DiscoveredEngine) => void;
  onAddManually: () => void;
  onDismiss: () => void;
  onScan: () => void;
  onShowProfile: (profileId: string) => void;
}) {
  if (state.status === "idle") return null;
  const knownIds = new Set(profiles.map((profile) => profile.id));
  return (
    <section className="engine-discovery" aria-labelledby="engine-discovery-title">
      <div className="engine-discovery__header">
        <div>
          <h3 id="engine-discovery-title">Engines running on this computer</h3>
          <p className="helper-copy">
            The scan only checks local ports on this computer (127.0.0.1). Nothing on your network
            or the internet is contacted.
          </p>
        </div>
        <button className="button button--quiet" onClick={onDismiss} type="button">
          Close scan results
        </button>
      </div>
      {state.status === "scanning" && (
        <p className="engine-discovery__progress" aria-hidden="true">
          Scanning…
        </p>
      )}
      {state.status === "error" && (
        <div className="state-message state-message--error" role="alert">
          <span>{state.message}</span>
          <button className="button button--quiet" onClick={onScan} type="button">
            Scan again
          </button>
        </div>
      )}
      {state.status === "done" && (
        <>
          {state.result.candidates.length === 0 ? (
            <div className="engine-discovery__empty">
              <p>
                <strong>No running engines were found.</strong>
              </p>
              <p>
                Start your engine (for example <code>ollama serve</code> or{" "}
                <code>llama-server</code>), then scan again. If it listens on an unusual port or
                runs on another machine, add it manually.
              </p>
              <div className="engine-discovery__actions">
                <button className="button button--primary" onClick={onScan} type="button">
                  Scan again
                </button>
                <button className="button button--quiet" onClick={onAddManually} type="button">
                  Add manually
                </button>
              </div>
            </div>
          ) : (
            <ul className="engine-discovery__list">
              {state.result.candidates.map((candidate) => {
                const existingId =
                  addedByEndpoint[candidate.endpoint_url] ??
                  candidate.existing_profile_ids.find((id) => knownIds.has(id)) ??
                  candidate.existing_profile_ids[0];
                const manifest = manifests.get(candidate.adapter_id);
                const addState = addStates[candidate.endpoint_url];
                return (
                  <li className="engine-discovery__candidate" key={candidate.endpoint_url}>
                    <div className="engine-discovery__facts">
                      <h4>{candidate.display_name}</h4>
                      <dl>
                        <div>
                          <dt>Version</dt>
                          <dd>{candidate.engine_version ?? "Not reported"}</dd>
                        </div>
                        <div>
                          <dt>Endpoint</dt>
                          <dd>{candidate.endpoint_url}</dd>
                        </div>
                        <div>
                          <dt>Models</dt>
                          <dd>{modelSummary(candidate.models)}</dd>
                        </div>
                        <div>
                          <dt>How it was recognized</dt>
                          <dd>{candidate.evidence}</dd>
                        </div>
                      </dl>
                      {addState?.status === "error" && (
                        <p className="form-error" role="alert">
                          {addState.message}
                        </p>
                      )}
                      {!manifest && !existingId && (
                        <p className="inline-warning">
                          The {candidate.adapter_id} adapter is not installed, so this engine cannot
                          be added.
                        </p>
                      )}
                    </div>
                    <div className="engine-discovery__candidate-action">
                      {existingId ? (
                        <>
                          <span className="status-pill status-pill--enabled">Already added</span>
                          {knownIds.has(existingId) && (
                            <button
                              aria-label={`Show profile for ${candidate.display_name}`}
                              className="button button--quiet"
                              onClick={() => onShowProfile(existingId)}
                              type="button"
                            >
                              Show profile
                            </button>
                          )}
                        </>
                      ) : (
                        <button
                          aria-label={
                            addState?.status === "adding"
                              ? `Adding ${candidate.display_name}`
                              : `Add ${candidate.display_name}`
                          }
                          className="button button--primary"
                          disabled={!manifest || addState?.status === "adding"}
                          onClick={() => onAdd(candidate)}
                          type="button"
                        >
                          {addState?.status === "adding" ? "Adding…" : "Add"}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <details className="engine-discovery__scanned">
            <summary>
              Checked {state.result.scanned.length}{" "}
              {state.result.scanned.length === 1 ? "port" : "ports"} on 127.0.0.1 in{" "}
              {Math.round(state.result.duration_ms)} ms
            </summary>
            <ul>
              {state.result.scanned.map((port) => (
                <li key={port.port}>
                  Port {port.port}: {PORT_STATUS_LABEL[port.status] ?? port.status}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </section>
  );
}

const STEP_MARK = "✓";

function formatProbeTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function stepClass(step: EngineSetupStep): string {
  return `engine-setup__step engine-setup__step--${step.state}`;
}

/**
 * Compact next-steps row on an engine card. Incomplete steps carry their action buttons here;
 * once a step is done its maintenance action (re-probe, stop server) lives in the card header.
 */
export function EngineSetupChecklist({
  profile,
  probeResult,
  busy,
  busyAction,
  canProbe,
  probeBlockedReason,
  onStart,
  onProbe,
  onEnable,
}: {
  profile: EngineProfile;
  probeResult?: ProbeResult;
  busy: boolean;
  busyAction: "start" | "probe" | "enable" | null;
  canProbe: boolean;
  probeBlockedReason?: string;
  onStart: () => void;
  onProbe: () => void;
  onEnable: () => void;
}) {
  const steps = engineSetupSteps(profile, probeResult);
  const probedAt = formatProbeTime(probeResult?.probed_at ?? profile.last_probed_at);
  const button = (step: EngineSetupStep) =>
    step.state === "next" || step.state === "failed"
      ? "button button--primary engine-setup__button"
      : "button button--quiet engine-setup__button";

  const content = (step: EngineSetupStep) => {
    switch (step.id) {
      case "added":
        return <span>{STEP_MARK} Added</span>;
      case "start":
        return step.state === "done" ? (
          <span>{STEP_MARK} Server running</span>
        ) : (
          <button className={button(step)} disabled={busy} onClick={onStart} type="button">
            {busyAction === "start" ? "Starting…" : "Start server"}
          </button>
        );
      case "probe":
        return step.state === "done" ? (
          <span>
            {STEP_MARK} Probed{probedAt ? <small> {probedAt}</small> : null}
          </span>
        ) : (
          <button
            className={button(step)}
            disabled={busy || !canProbe || step.state === "pending"}
            onClick={onProbe}
            title={probeBlockedReason}
            type="button"
          >
            {busyAction === "probe" ? "Probing…" : "Probe"}
          </button>
        );
      case "enable":
        return step.state === "done" ? (
          <span>{STEP_MARK} Enabled</span>
        ) : (
          <button
            className={button(step)}
            disabled={busy || step.state !== "next"}
            onClick={onEnable}
            type="button"
          >
            {busyAction === "enable" ? "Enabling…" : "Enable"}
          </button>
        );
      case "playground":
        return step.state === "pending" ? (
          <span>Try in Playground</span>
        ) : (
          <a className="button button--quiet" href={engineDeepLink("playground", profile.id)}>
            Try in Playground
          </a>
        );
      case "benchmark":
        return step.state === "pending" ? (
          <span>Benchmark</span>
        ) : (
          <a className="button button--primary" href={engineDeepLink("workloads", profile.id)}>
            Benchmark
          </a>
        );
    }
  };

  return (
    <ol className="engine-setup" aria-label={`Next steps for ${profile.label}`}>
      {steps.map((step) => (
        <li
          aria-current={step.state === "next" || step.state === "failed" ? "step" : undefined}
          className={stepClass(step)}
          key={step.id}
        >
          {content(step)}
        </li>
      ))}
    </ol>
  );
}
