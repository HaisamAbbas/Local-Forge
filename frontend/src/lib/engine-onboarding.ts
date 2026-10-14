import type { EngineProfile, ProbeResult } from "./api";

/** Steps from "profile saved" to "first benchmark", shown as a checklist on each engine card. */
export type EngineSetupStepId = "added" | "start" | "probe" | "enable" | "playground" | "benchmark";
/** done: completed. next: the first incomplete step (the primary action). pending: not yet. */
export type EngineSetupStepState = "done" | "next" | "pending" | "failed";
export type EngineSetupStep = { id: EngineSetupStepId; state: EngineSetupStepState };

export type ProbeOutcome = "none" | "succeeded" | "failed";

/** Latest probe outcome: this session's result wins, then what the saved profile implies. */
export function probeOutcome(profile: EngineProfile, sessionResult?: ProbeResult): ProbeOutcome {
  if (sessionResult) return sessionResult.status;
  if (profile.status === "needs_attention") return "failed";
  if (profile.status === "enabled" || profile.last_probed_at) return "succeeded";
  return "none";
}

export function engineSetupSteps(
  profile: EngineProfile,
  sessionResult?: ProbeResult,
): EngineSetupStep[] {
  const managed = profile.integration_mode === "managed_process";
  const outcome = probeOutcome(profile, sessionResult);
  const enabled = profile.status === "enabled";
  const completion: Array<{ id: EngineSetupStepId; done: boolean; failed?: boolean }> = [
    { id: "added", done: true },
    ...(managed ? [{ id: "start" as const, done: profile.managed_process_running }] : []),
    { id: "probe", done: outcome === "succeeded", failed: outcome === "failed" },
    { id: "enable", done: enabled },
  ];
  let nextAssigned = false;
  const steps: EngineSetupStep[] = completion.map(({ id, done, failed }) => {
    if (done) return { id, state: "done" };
    if (!nextAssigned) {
      nextAssigned = true;
      return { id, state: failed ? "failed" : "next" };
    }
    return { id, state: "pending" };
  });
  // Playground and Benchmark are links; Benchmark becomes the primary action once enabled.
  steps.push({ id: "playground", state: nextAssigned ? "pending" : "done" });
  steps.push({ id: "benchmark", state: nextAssigned ? "pending" : "next" });
  return steps;
}

/** Engine profile id from a deep link such as "#workloads?engine=<id>", for the given screen. */
export function engineIdFromHash(hash: string, screen: string): string | null {
  const [hashScreen, query = ""] = hash.replace(/^#/, "").split("?", 2);
  if (hashScreen !== screen) return null;
  const id = new URLSearchParams(query).get("engine");
  return id && id.trim() ? id.trim() : null;
}

export function engineDeepLink(screen: "playground" | "workloads", profileId: string): string {
  return `#${screen}?engine=${encodeURIComponent(profileId)}`;
}

/** The first problem a failed probe reported, in plain words. */
export function probeProblem(result: ProbeResult): string {
  const diagnostic =
    result.diagnostics.find((item) => item.severity === "error") ?? result.diagnostics[0];
  if (diagnostic?.message) return diagnostic.message;
  if (result.reachable.state !== "supported" && result.reachable.details)
    return result.reachable.details;
  return "The engine did not answer the probe. Check that it is running, then probe again.";
}
