import { useCallback, useEffect, useRef, useState } from "react";

import {
  createEngineProfile,
  enableEngineProfile,
  fetchEngineRecommendations,
  probeEngineProfile,
  type EngineFit,
  type EngineInstallStatus,
  type EngineRecommendation,
  type EngineRecommendationsResult,
} from "./api";
import { probeProblem } from "./engine-onboarding";

export const FIT_LABEL: Record<EngineFit, string> = {
  best: "Best fit",
  good: "Good fit",
  possible: "Possible",
  unsuitable: "Not for this machine",
};

export const INSTALL_STATUS_LABEL: Record<EngineInstallStatus, string> = {
  running: "Running",
  installed: "Installed",
  not_installed: "Not installed",
  unknown: "Unknown",
};

/** Existing status-pill colour for each install status; the label always carries the meaning. */
export const INSTALL_STATUS_PILL: Record<EngineInstallStatus, string> = {
  running: "status-pill--enabled",
  installed: "status-pill--partially_detected",
  not_installed: "status-pill--disabled",
  unknown: "status-pill--disabled",
};

/** Models screen, whose "Suggested for this machine" section answers which model to run. */
export const MODEL_SUGGESTIONS_HREF = "#models";

/** Suitable engines in rank order, and the unsuitable ones grouped separately. */
export function splitRecommendations(items: EngineRecommendation[]): {
  suitable: EngineRecommendation[];
  unsuitable: EngineRecommendation[];
} {
  const ranked = [...items].sort((a, b) => a.rank - b.rank);
  return {
    suitable: ranked.filter((item) => item.fit !== "unsuitable"),
    unsuitable: ranked.filter((item) => item.fit === "unsuitable"),
  };
}

export type RecommendationAction =
  | { kind: "add" }
  | { kind: "benchmark"; profileId: string }
  | { kind: "start" }
  | { kind: "install" }
  | { kind: "none" };

/** The one action that gets this engine going, given its status and any profile already saved. */
export function recommendationAction(
  item: EngineRecommendation,
  addedProfileId?: string,
): RecommendationAction {
  if (item.fit === "unsuitable") return { kind: "none" };
  const profileId = addedProfileId ?? item.existing_profile_ids[0];
  switch (item.status) {
    case "running":
      if (profileId) return { kind: "benchmark", profileId };
      return item.endpoint_url ? { kind: "add" } : { kind: "start" };
    case "installed":
      return { kind: "start" };
    case "not_installed":
    case "unknown":
      return { kind: "install" };
  }
}

/** Measured badge text, only when this machine has a completed run; never an estimate. */
export function measuredText(item: EngineRecommendation): string | null {
  const measured = item.measured;
  if (!measured || measured.completed_runs <= 0) return null;
  const speed = measured.best_generation_tokens_per_second;
  if (speed === null || !Number.isFinite(speed)) {
    return `Measured in ${measured.completed_runs} ${measured.completed_runs === 1 ? "run" : "runs"}`;
  }
  const model = measured.model_ref ? ` · ${measured.model_ref}` : "";
  return `Measured ${speed.toFixed(1)} tok/s${model}`;
}

export function recommendationProfileLabel(item: EngineRecommendation): string {
  return `${item.display_name} on this machine`.slice(0, 120);
}

export type AddOutcome = { profileId: string; ready: boolean; message: string };

/**
 * One-click add for a running engine: create an attached profile, probe it, and enable it if the
 * probe succeeds. Remote endpoints are never probed without the user's explicit confirmation.
 */
export async function addRecommendedEngine(item: EngineRecommendation): Promise<AddOutcome> {
  if (!item.endpoint_url) throw new Error(`No local endpoint is known for ${item.display_name}.`);
  const label = recommendationProfileLabel(item);
  const created = await createEngineProfile({
    label,
    adapter_id: item.adapter_id,
    engine_display_name: null,
    integration_mode: "attached_endpoint",
    endpoint_url: item.endpoint_url,
    user_notes: null,
  });
  if (created.is_remote) {
    return {
      profileId: created.id,
      ready: false,
      message: `Added ${created.label}. Probe it on the Engines screen to confirm it.`,
    };
  }
  const probe = await probeEngineProfile(created.id, false);
  if (probe.result.status !== "succeeded") {
    return {
      profileId: created.id,
      ready: false,
      message: `Added ${created.label}, but its probe found a problem: ${probeProblem(probe.result)} It stays disabled.`,
    };
  }
  await enableEngineProfile(created.id);
  return {
    profileId: created.id,
    ready: true,
    message: `${created.label} is ready. Benchmark it to measure it on this machine.`,
  };
}

export type RecommendationsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: EngineRecommendationsResult; refreshing: boolean };

/** Loads recommendations; refresh keeps the current list visible while it re-fetches. */
export function useEngineRecommendations(enabled = true) {
  const [state, setState] = useState<RecommendationsState>({ status: "loading" });
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const id = ++requestId.current;
    setState((current) =>
      current.status === "ready" ? { ...current, refreshing: true } : { status: "loading" },
    );
    try {
      const value = await fetchEngineRecommendations();
      if (id === requestId.current) setState({ status: "ready", value, refreshing: false });
    } catch (error) {
      if (id === requestId.current)
        setState({
          status: "error",
          message:
            error instanceof Error ? error.message : "Engine recommendations could not be loaded.",
        });
    }
  }, []);
  const loaded = useRef(false);
  useEffect(() => {
    if (!enabled || loaded.current) return;
    loaded.current = true;
    void load();
  }, [enabled, load]);
  return { state, reload: load };
}

export function readStoredFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "true";
  } catch {
    return false;
  }
}

export function writeStoredFlag(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, value ? "true" : "false");
  } catch {
    // Storage can be unavailable (private mode, blocked site data); the default still works.
  }
}
