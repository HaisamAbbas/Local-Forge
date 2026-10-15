import { useEffect, useId, useState } from "react";

import { fetchHardwareFit, type HardwareFitMachine, type HardwareFitResult } from "../lib/api";
import {
  FIT_FILTERS,
  FIT_SORTS,
  RUN_MODE_LABEL,
  STRENGTH_FILTERS,
  TIER_LABEL,
  formatGb,
  formatParams,
  pullCommand,
  runsWell,
  speedLabel,
  visibleModels,
  type FitFilter,
  type FitSort,
} from "../lib/hardware-fit";

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; value: HardwareFitResult };

function MachineFacts({ machine }: { machine: HardwareFitMachine }) {
  const gpus = machine.accelerators.length
    ? machine.accelerators.map((item) => {
        const memory = item.memory_gb !== null ? ` · ${formatGb(item.memory_gb)}` : "";
        const kind =
          item.kind === "integrated"
            ? " (integrated, shares RAM)"
            : item.kind === "unified"
              ? " (unified memory)"
              : "";
        return `${item.name}${memory}${kind}`;
      })
    : [machine.accelerators_detected ? "No GPU detected" : "Not detected"];
  const cores =
    machine.cpu_physical_cores !== null && machine.cpu_logical_cores !== null
      ? `${machine.cpu_physical_cores} cores, ${machine.cpu_logical_cores} threads`
      : machine.cpu_logical_cores !== null
        ? `${machine.cpu_logical_cores} threads`
        : "Not detected";
  const budget =
    machine.gpu_budget_gb !== null
      ? `${formatGb(machine.gpu_budget_gb)} of GPU memory${
          machine.ram_budget_gb !== null ? `, plus ${formatGb(machine.ram_budget_gb)} of RAM` : ""
        }`
      : machine.ram_budget_gb !== null
        ? `${formatGb(machine.ram_budget_gb)} of RAM (after leaving 6 GB for the system)`
        : "Unknown";
  const facts: [string, string][] = [
    ["Processor", machine.cpu_model ?? "Not detected"],
    ["Cores", cores],
    [
      "System memory",
      machine.memory_total_gb !== null ? formatGb(machine.memory_total_gb) : "Not detected",
    ],
    ["Graphics", gpus.join("; ")],
    ["Models run on", machine.compute_path_label],
    ["Memory models can use", budget],
  ];
  return (
    <div className="facts-grid">
      {facts.map(([label, value]) => (
        <div className="fact-card" key={label}>
          <span className="fact-label">{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

export function HardwareFitScreen() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [scanning, setScanning] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState<FitFilter>("runs_well");
  const [sort, setSort] = useState<FitSort>("best");
  const [strength, setStrength] = useState("all");
  const [search, setSearch] = useState("");
  const ids = useId();

  useEffect(() => {
    let active = true;
    fetchHardwareFit(false).then(
      (value) => {
        if (active) setState({ status: "ready", value });
      },
      (error: unknown) => {
        if (active) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : "Could not detect this machine.",
          });
        }
      },
    );
    return () => {
      active = false;
    };
  }, [reloadKey]);

  async function detectAgain() {
    setScanning(true);
    try {
      setState({ status: "ready", value: await fetchHardwareFit(true) });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "Could not detect this machine.",
      });
    } finally {
      setScanning(false);
    }
  }

  if (state.status === "loading") {
    return (
      <p role="status" aria-busy="true">
        Detecting this machine…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div className="state-message state-message--error" role="alert">
        <span>{state.message}</span>
        <button
          className="button button--quiet"
          onClick={() => {
            setState({ status: "loading" });
            setReloadKey((n) => n + 1);
          }}
          type="button"
        >
          Retry
        </button>
      </div>
    );
  }

  const result = state.value;
  const shown = visibleModels(result.models, { filter, sort, strength, search });
  const wellCount = result.models.filter(runsWell).length;

  return (
    <div className="hardware-fit">
      <section className="panel" aria-labelledby={`${ids}-machine`}>
        <div className="section-title-row">
          <div>
            <p className="eyebrow">THIS MACHINE</p>
            <h2 id={`${ids}-machine`}>Detected hardware</h2>
            <p className="helper-copy">From {result.detected_by}. Nothing leaves this computer.</p>
          </div>
          <button
            className="button button--quiet"
            disabled={scanning}
            onClick={() => void detectAgain()}
            type="button"
          >
            {scanning ? "Detecting…" : "Detect again"}
          </button>
        </div>
        <MachineFacts machine={result.machine} />
        {result.machine.notes.map((note) => (
          <p className="helper-copy hardware-fit__note" key={note}>
            {note}
          </p>
        ))}
      </section>

      <section className="panel" aria-labelledby={`${ids}-models`}>
        <div className="section-title-row">
          <div>
            <p className="eyebrow">MODEL FIT</p>
            <h2 id={`${ids}-models`}>Which language models suit this hardware</h2>
            <p className="helper-copy">
              {wellCount} of {result.models.length} catalog models run well here. Each row shows the
              best quantization this machine can hold. {result.method} Catalog{" "}
              {result.catalog_version}.
            </p>
          </div>
        </div>

        <div className="hardware-fit__controls">
          <label>
            <span className="fact-label">Show</span>
            <select value={filter} onChange={(event) => setFilter(event.target.value as FitFilter)}>
              {FIT_FILTERS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="fact-label">Good for</span>
            <select value={strength} onChange={(event) => setStrength(event.target.value)}>
              {STRENGTH_FILTERS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="fact-label">Sort by</span>
            <select value={sort} onChange={(event) => setSort(event.target.value as FitSort)}>
              {FIT_SORTS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="fact-label">Search</span>
            <input
              type="search"
              value={search}
              placeholder="Model or publisher"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>

        {shown.length === 0 ? (
          <p className="empty-inline">
            No models match these filters. Try “Runs at all” or “Everything”.
          </p>
        ) : (
          <div className="table-scroll">
            <table className="hardware-fit__table">
              <caption className="visually-hidden">Models graded for this machine</caption>
              <thead>
                <tr>
                  <th scope="col">Model</th>
                  <th scope="col">Size</th>
                  <th scope="col">Quantization</th>
                  <th scope="col">Memory needed</th>
                  <th scope="col">Runs on</th>
                  <th scope="col">Speed</th>
                  <th scope="col">Fit</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((model) => {
                  const command = pullCommand(model);
                  return (
                    <tr key={model.model_id} aria-label={model.name}>
                      <th scope="row">
                        <strong>{model.name}</strong>
                        {model.downloaded ? (
                          <span className="hardware-fit__tag">Downloaded</span>
                        ) : null}
                        <span className="table-subtext">{model.explanation}</span>
                        {command ? (
                          <span className="table-subtext">
                            Get the 4-bit build: <code>{command}</code>
                          </span>
                        ) : null}
                      </th>
                      <td>{formatParams(model)}</td>
                      <td>
                        {model.run_mode === "none" ? "Smallest: " : ""}
                        {model.quantization}
                        {model.lower_quality ? (
                          <span className="table-subtext">Lower quality</span>
                        ) : null}
                      </td>
                      <td>{formatGb(model.memory_required_gb)}</td>
                      <td>{RUN_MODE_LABEL[model.run_mode]}</td>
                      <td>{speedLabel(model)}</td>
                      <td>
                        <span className={`hardware-fit__tier hardware-fit__tier--${model.tier}`}>
                          {TIER_LABEL[model.tier]}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="helper-copy hardware-fit__footnote">
          To download a model, open <a href="#models">Models</a>. To measure real speed on this
          machine, run a benchmark: estimates improve once you have.
        </p>
      </section>
    </div>
  );
}
