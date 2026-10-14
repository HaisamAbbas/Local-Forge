import { useCallback, useState } from "react";

import { discoverEngines, type EngineDiscoveryResult } from "./api";

export type DiscoveryState =
  | { status: "idle" }
  | { status: "scanning" }
  | { status: "done"; result: EngineDiscoveryResult }
  | { status: "error"; message: string };

export type CandidateAddState = { status: "adding" } | { status: "error"; message: string };

export function useEngineDiscovery() {
  const [state, setState] = useState<DiscoveryState>({ status: "idle" });
  const scan = useCallback(async () => {
    setState({ status: "scanning" });
    try {
      setState({ status: "done", result: await discoverEngines() });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "The scan could not be completed.",
      });
    }
  }, []);
  const dismiss = useCallback(() => setState({ status: "idle" }), []);
  return { state, scan, dismiss };
}

export function scanStatusMessage(state: DiscoveryState): string {
  if (state.status === "scanning") return "Scanning this computer for running engines…";
  if (state.status !== "done") return "";
  const count = state.result.candidates.length;
  if (count === 0) return "Scan finished. No running engines were found.";
  return `Scan finished. Found ${count === 1 ? "1 engine" : `${count} engines`}.`;
}
