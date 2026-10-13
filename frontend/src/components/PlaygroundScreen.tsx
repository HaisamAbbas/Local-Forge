import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  ApiError,
  listEngineCatalog,
  listEngineModels,
  listEngineProfiles,
  streamPlaygroundPrompt,
  type EngineModel,
  type EngineProfile,
  type PlaygroundStreamEvent,
} from "../lib/api";

type ModelChoice = {
  profile: EngineProfile;
  model: EngineModel;
  cancellationMayContinue: boolean;
};
type StreamState = "idle" | "streaming" | "complete" | "error" | "cancelled";

function remoteHost(profile: EngineProfile | null): string {
  if (!profile?.endpoint_url) return "the configured remote endpoint";
  try {
    return new URL(profile.endpoint_url).host;
  } catch {
    return "the configured remote endpoint";
  }
}

export function PlaygroundScreen() {
  const [choices, setChoices] = useState<ModelChoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selection, setSelection] = useState("");
  const [prompt, setPrompt] = useState("");
  const [maxTokens, setMaxTokens] = useState(512);
  const [confirmRemote, setConfirmRemote] = useState(false);
  const [output, setOutput] = useState("");
  const [usage, setUsage] = useState<Extract<PlaygroundStreamEvent, { type: "usage" }> | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [streamError, setStreamError] = useState("");
  const abortController = useRef<AbortController | null>(null);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      setLoadError("");
      try {
        const [profiles, adapters] = await Promise.all([listEngineProfiles(), listEngineCatalog()]);
        const manifests = new Map(adapters.map((adapter) => [adapter.adapter_id, adapter]));
        const enabled = profiles.filter((profile) => {
          const manifest = manifests.get(profile.adapter_id);
          return (
            profile.status === "enabled" &&
            (profile.integration_mode !== "managed_process" || profile.managed_process_running) &&
            manifest?.category !== "test_fixture" &&
            Boolean(manifest?.declared_capabilities.includes("chat_completion")) &&
            Boolean(manifest?.declared_capabilities.includes("streaming")) &&
            profile.capabilities?.chat_completion?.state !== "unsupported" &&
            profile.capabilities?.streaming?.state !== "unsupported"
          );
        });
        const modelLists = await Promise.all(
          enabled.map(async (profile) => {
            try {
              return { profile, models: await listEngineModels(profile.id) };
            } catch {
              return { profile, models: [] as EngineModel[] };
            }
          }),
        );
        const nextChoices = modelLists.flatMap(({ profile, models }) =>
          models
            .filter((model) => model.status === "available" && Boolean(model.model_ref))
            .map((model) => ({
              profile,
              model,
              cancellationMayContinue:
                manifests.get(profile.adapter_id)?.declared_capabilities.includes("cancellation") !==
                  true ||
                profile.capabilities?.cancellation?.state !== "supported",
            })),
        );
        if (!active) return;
        setChoices(nextChoices);
        setSelection((current) =>
          nextChoices.some(({ model }) => model.id === current)
            ? current
            : (nextChoices[0]?.model.id ?? ""),
        );
      } catch (error) {
        if (!active) return;
        setLoadError(error instanceof Error ? error.message : "Could not load connected engines.");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
      abortController.current?.abort();
    };
  }, []);

  const choice = useMemo(
    () => choices.find(({ model }) => model.id === selection) ?? null,
    [choices, selection],
  );
  const needsRemoteConfirmation = Boolean(choice?.profile.is_remote);
  const profileAllowsRemote = !needsRemoteConfirmation || choice?.profile.remote_access_enabled === true;
  const canSend =
    Boolean(choice) &&
    Boolean(prompt.trim()) &&
    streamState !== "streaming" &&
    profileAllowsRemote &&
    (!needsRemoteConfirmation || confirmRemote);

  const handleEvent = (event: PlaygroundStreamEvent) => {
    if (event.type === "chunk") setOutput((current) => current + event.text);
    if (event.type === "usage") setUsage(event);
    if (event.type === "complete") {
      setElapsedMs(event.elapsed_ms);
      setStreamState("complete");
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!choice || !canSend) return;
    const controller = new AbortController();
    const remoteConsent = confirmRemote;
    abortController.current = controller;
    setOutput("");
    setUsage(null);
    setElapsedMs(null);
    setStreamError("");
    setStreamState("streaming");
    setConfirmRemote(false);
    try {
      await streamPlaygroundPrompt(
        {
          profile_id: choice.profile.id,
          model_ref: choice.model.model_ref ?? "",
          prompt,
          max_tokens: maxTokens,
          temperature: 0.7,
          confirm_remote_egress: remoteConsent,
        },
        controller.signal,
        handleEvent,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        setStreamState("cancelled");
      } else {
        setStreamState("error");
        setStreamError(
          error instanceof ApiError || error instanceof Error
            ? error.message
            : "The prompt could not be completed.",
        );
      }
    } finally {
      if (abortController.current === controller) abortController.current = null;
    }
  };

  const stop = () => abortController.current?.abort();
  const usableChoices = choices.length > 0;

  return (
    <div className="screen-stack playground-screen">
      <section className="panel playground-panel" aria-labelledby="playground-title">
        <div className="section-title-row">
          <div>
            <p className="eyebrow">LIVE INFERENCE</p>
            <h2 id="playground-title">Send a prompt</h2>
          </div>
          <span className="playground-local-label">Runs through your connected engine</span>
        </div>

        {loading ? (
          <p className="playground-empty" role="status">Loading connected engines and models…</p>
        ) : loadError ? (
          <div className="notice notice--warning" role="alert">
            {loadError} <button className="button button--secondary" onClick={() => window.location.reload()} type="button">Retry</button>
          </div>
        ) : !usableChoices ? (
          <div className="playground-empty">
            <h3>No ready inference models</h3>
            <p>
              Add an engine in LOCALFORGE, probe its connection, select an available model, and enable
              the profile. Test fixtures do not run real models.
            </p>
            <a className="button button--primary" href="#engines">Set up an engine</a>
          </div>
        ) : (
          <form className="playground-form" onSubmit={submit}>
            <label className="playground-field" htmlFor="playground-model">
              <span>Connected model</span>
              <select
                id="playground-model"
                value={selection}
                disabled={streamState === "streaming"}
                onChange={(event) => {
                  setSelection(event.target.value);
                  setConfirmRemote(false);
                }}
              >
                {choices.map(({ profile, model }) => (
                  <option key={model.id} value={model.id}>
                    {profile.label} · {model.display_name}
                  </option>
                ))}
              </select>
            </label>

            {needsRemoteConfirmation && (
              <div className="playground-remote-notice">
                <p>
                  This model is hosted at <strong>{remoteHost(choice?.profile ?? null)}</strong>. The
                  prompt and generated response will leave this machine.
                </p>
                {profileAllowsRemote ? (
                  <label className="playground-check">
                    <input
                      checked={confirmRemote}
                      onChange={(event) => setConfirmRemote(event.target.checked)}
                      type="checkbox"
                    />
                    <span>Send this prompt to that remote engine</span>
                  </label>
                ) : (
                  <p>
                    Remote use is not enabled for this profile. <a href="#engines">Review its connection settings</a>.
                  </p>
                )}
              </div>
            )}

            <label className="playground-field" htmlFor="playground-prompt">
              <span>Your prompt</span>
              <textarea
                id="playground-prompt"
                autoComplete="off"
                maxLength={32_000}
                placeholder="Ask your model something…"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={7}
                required
              />
            </label>
            <div className="playground-controls">
              <label className="playground-field playground-token-field" htmlFor="playground-max-tokens">
                <span>Maximum response</span>
                <select
                  id="playground-max-tokens"
                  value={maxTokens}
                  disabled={streamState === "streaming"}
                  onChange={(event) => setMaxTokens(Number(event.target.value))}
                >
                  {[256, 512, 1024, 2048].map((value) => (
                    <option key={value} value={value}>{value} tokens</option>
                  ))}
                </select>
              </label>
              <div className="playground-actions">
                {streamState === "streaming" ? (
                  <button className="button button--secondary" onClick={stop} type="button">Stop output</button>
                ) : (
                  <button className="button button--primary" disabled={!canSend} type="submit">
                    Send prompt
                  </button>
                )}
              </div>
            </div>
            {choice?.cancellationMayContinue && (
              <p className="playground-cancellation-note">
                Stopping closes LOCALFORGE’s output stream. This engine may continue generating on its
                side.
              </p>
            )}
            <p className="playground-privacy">
              LOCALFORGE does not save prompt history. This prompt and response remain in this screen until you leave or reload it.
            </p>
          </form>
        )}
      </section>

      {usableChoices && (
        <section className="panel playground-response" aria-labelledby="playground-response-title">
          <div className="section-title-row">
            <div>
              <p className="eyebrow">MODEL OUTPUT</p>
              <h2 id="playground-response-title">Response</h2>
            </div>
            {streamState === "streaming" && <span className="playground-stream-status" role="status">Generating…</span>}
          </div>
          <div className="playground-output" aria-live="polite" aria-busy={streamState === "streaming"}>
            {output ? <pre>{output}</pre> : (
              <p className="playground-output-placeholder">
                {streamState === "streaming" ? "Waiting for the first tokens…" : "Your model’s response will appear here."}
              </p>
            )}
          </div>
          {streamError && <p className="playground-error" role="alert">{streamError}</p>}
          {streamState === "cancelled" && (
            <p className="playground-muted" role="status">
              {choice?.cancellationMayContinue
                ? "Stopped receiving output. The engine may still be generating."
                : "Response stopped."}
            </p>
          )}
          {(elapsedMs !== null || usage) && (
            <div className="playground-metadata" aria-label="Response details">
              {elapsedMs !== null && <span>{(elapsedMs / 1000).toFixed(2)} s</span>}
              {usage?.input_tokens !== null && usage?.input_tokens !== undefined && <span>{usage.input_tokens} input tokens</span>}
              {usage?.output_tokens !== null && usage?.output_tokens !== undefined && <span>{usage.output_tokens} output tokens</span>}
              {usage && !usage.exact && <span>Token counts estimated</span>}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
