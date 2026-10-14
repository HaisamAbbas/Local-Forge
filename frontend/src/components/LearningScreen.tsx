import { useEffect, useId, useMemo, useState } from "react";

import {
  getRun,
  listBenchmarkPlans,
  listRunMetrics,
  listRuns,
  type BenchmarkPlan,
  type BenchmarkRun,
  type MetricSample,
} from "../lib/api";
import {
  EXPERIMENT_GUIDES,
  GLOSSARY_CATEGORIES,
  SOURCE_LABELS,
  diagnoseRun,
  diagnosisContextFromPlan,
  formatMetricValue,
  glossaryAnchorId,
  glossaryEntry,
  glossaryHref,
  learningHref,
  parseLearningHash,
  searchGlossary,
  type DiagnosisFinding,
  type ExperimentGuide,
  type GlossaryCategory,
  type GlossaryEntry,
} from "../lib/learning";
import { MetricHelp } from "./MetricHelp";

const SECTIONS = [
  { id: "learning-glossary", label: "Metric glossary" },
  { id: "learning-guides", label: "Experiment guides" },
  { id: "learning-diagnosis", label: "Diagnose a run" },
] as const;

function jumpTo(id: string) {
  const section = document.getElementById(id);
  if (!section) return;
  section.scrollIntoView?.({ block: "start" });
  section.querySelector<HTMLElement>("h2")?.focus();
}

/** Learning screen: metric glossary, experiment guides, and an evidence-based diagnosis panel. */
export function LearningScreen() {
  const [location, setLocation] = useState(() => parseLearningHash(window.location.hash));

  useEffect(() => {
    const update = () => setLocation(parseLearningHash(window.location.hash));
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  return (
    <div className="screen-stack learning-screen">
      <nav className="learning-jump" aria-label="Learning sections">
        {SECTIONS.map((section) => (
          <button
            className="button button--quiet"
            key={section.id}
            onClick={() => jumpTo(section.id)}
            type="button"
          >
            {section.label}
          </button>
        ))}
      </nav>
      <MetricGlossary selectedMetric={location.metric} />
      <ExperimentGuides />
      <DiagnosisPanel requestedRunId={location.run} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Glossary
// ---------------------------------------------------------------------------------------------

function MetricGlossary({ selectedMetric }: { selectedMetric: string | null }) {
  const searchId = useId();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<GlossaryCategory | "all">("all");
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(selectedMetric ? [selectedMetric] : []),
  );
  const [linkedMetric, setLinkedMetric] = useState(selectedMetric);

  // A new deep link opens its entry and clears filters that would hide it.
  if (selectedMetric !== linkedMetric) {
    setLinkedMetric(selectedMetric);
    if (selectedMetric) {
      setOpen((current) => new Set(current).add(selectedMetric));
      setQuery("");
      setCategory("all");
    }
  }

  useEffect(() => {
    if (!selectedMetric) return;
    const entry = document.getElementById(glossaryAnchorId(selectedMetric));
    entry?.scrollIntoView?.({ block: "center" });
    entry?.querySelector<HTMLElement>("summary")?.focus();
  }, [selectedMetric]);

  const results = useMemo(() => searchGlossary(query, category), [query, category]);
  const grouped = GLOSSARY_CATEGORIES.map((group) => ({
    ...group,
    entries: results.filter((entry) => entry.category === group.id),
  })).filter((group) => group.entries.length > 0);

  function toggle(name: string, isOpen: boolean) {
    setOpen((current) => {
      if (current.has(name) === isOpen) return current;
      const next = new Set(current);
      if (isOpen) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  return (
    <section className="panel" id="learning-glossary" aria-labelledby="learning-glossary-title">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">REFERENCE · {searchGlossary("").length} METRICS</p>
          <h2 id="learning-glossary-title" tabIndex={-1}>
            Metric glossary
          </h2>
          <p className="helper-copy">
            Every value LocalForge records, with the exact formula it uses and where the number
            comes from. Unavailable values are never shown as zero.
          </p>
        </div>
      </div>

      <div className="learning-controls">
        <label htmlFor={searchId}>
          Search metrics
          <input
            id={searchId}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="For example TTFT, prefill or memory"
            type="search"
            value={query}
          />
        </label>
        <div className="measure-levels" role="group" aria-label="Filter by category">
          {[{ id: "all" as const, label: "All" }, ...GLOSSARY_CATEGORIES].map((item) => (
            <button
              aria-pressed={category === item.id}
              className={`measure-level${category === item.id ? " measure-level--active" : ""}`}
              key={item.id}
              onClick={() => setCategory(item.id)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      <p className="measure-note" aria-live="polite">
        {results.length === 1 ? "1 metric" : `${results.length} metrics`} shown
      </p>

      {grouped.length === 0 ? (
        <p className="empty-inline">No metric matches “{query.trim()}”. Try a shorter term.</p>
      ) : (
        grouped.map((group) => (
          <div className="learning-glossary-group" key={group.id}>
            <h3>{group.label}</h3>
            {group.entries.map((entry) => (
              <GlossaryItem
                entry={entry}
                isOpen={open.has(entry.name)}
                isSelected={selectedMetric === entry.name}
                key={entry.name}
                onToggle={(isOpen) => toggle(entry.name, isOpen)}
              />
            ))}
          </div>
        ))
      )}
    </section>
  );
}

function GlossaryItem({
  entry,
  isOpen,
  isSelected,
  onToggle,
}: {
  entry: GlossaryEntry;
  isOpen: boolean;
  isSelected: boolean;
  onToggle: (isOpen: boolean) => void;
}) {
  const source = SOURCE_LABELS[entry.source];
  return (
    <details
      className={`learning-metric${isSelected ? " learning-metric--selected" : ""}`}
      id={glossaryAnchorId(entry.name)}
      onToggle={(event) => onToggle(event.currentTarget.open)}
      open={isOpen}
    >
      <summary>
        <span className="learning-metric__title">{entry.label}</span>
        <code className="learning-metric__name">{entry.name}</code>
        <span className="learning-metric__tags">
          <span className="learning-chip">{entry.unit}</span>
          <span className={`learning-chip learning-chip--${entry.source}`}>{source.label}</span>
        </span>
      </summary>
      <div className="learning-metric__body">
        <p>{entry.summary}</p>
        <dl className="learning-facts">
          <div>
            <dt>Formula</dt>
            <dd>
              <code>{entry.formula}</code>
            </dd>
          </div>
          <div>
            <dt>Unit</dt>
            <dd>{entry.unit}</dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>
              {source.label}. {source.description}
            </dd>
          </div>
          <div>
            <dt>Recorded as</dt>
            <dd>{entry.aggregations}</dd>
          </div>
        </dl>
        <div className="learning-columns">
          <div>
            <h4>What usually moves it</h4>
            <ul>
              {entry.movedBy.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div>
            <h4>Caveats</h4>
            <ul>
              {entry.caveats.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        </div>
        <div className="learning-metric__footer">
          {entry.related.length > 0 && (
            <p className="measure-note">
              Related:{" "}
              {entry.related.map((name, index) => (
                <span key={name}>
                  {index > 0 && ", "}
                  <MetricHelp metric={name} />
                </span>
              ))}
            </p>
          )}
          <a className="text-link" href={glossaryHref(entry.name)}>
            Link to this definition
          </a>
        </div>
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------------------------
// Experiment guides
// ---------------------------------------------------------------------------------------------

function ExperimentGuides() {
  const presets = EXPERIMENT_GUIDES.filter((guide) => guide.kind === "preset");
  const classics = EXPERIMENT_GUIDES.filter((guide) => guide.kind === "classic");
  return (
    <section className="panel" id="learning-guides" aria-labelledby="learning-guides-title">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">CHANGE ONE THING · MEASURE · COMPARE</p>
          <h2 id="learning-guides-title" tabIndex={-1}>
            Experiment guides
          </h2>
          <p className="helper-copy">
            Write down a hypothesis before you run, change a single setting between runs, and
            compare the metrics listed on each card.
          </p>
        </div>
        <a href="#workloads">Open workloads</a>
      </div>
      <div className="run-subsection">
        <h3>Workload presets</h3>
        <div className="learning-guide-grid">
          {presets.map((guide) => (
            <GuideCard guide={guide} key={guide.id} />
          ))}
        </div>
      </div>
      <div className="run-subsection">
        <h3>Classic experiments</h3>
        <div className="learning-guide-grid">
          {classics.map((guide) => (
            <GuideCard guide={guide} key={guide.id} />
          ))}
        </div>
      </div>
    </section>
  );
}

function GuideCard({ guide }: { guide: ExperimentGuide }) {
  const titleId = useId();
  return (
    <article className="learning-guide" aria-labelledby={titleId}>
      <p className="eyebrow">
        {guide.kind === "preset" ? `PRESET · ${guide.presetId}` : "CLASSIC EXPERIMENT"}
      </p>
      <h4 className="learning-guide__title" id={titleId}>
        {guide.title}
      </h4>
      <p className="learning-guide__shape">{guide.shape}</p>
      <h5>What this teaches</h5>
      <p>{guide.teaches}</p>
      <h5>Hypothesis prompt</h5>
      <p className="learning-hypothesis">{guide.hypothesis}</p>
      <h5>Change one at a time</h5>
      <ul>
        {guide.changeOneAtATime.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <h5>Metrics to watch</h5>
      <ul className="learning-chip-list">
        {guide.watch.map((name) => (
          <li key={name}>
            <MetricHelp className="learning-chip" metric={name} />
          </li>
        ))}
      </ul>
      <p className="measure-note">{guide.tip}</p>
      <a className="text-link learning-guide__action" href="#workloads">
        Build this workload<span className="visually-hidden">: {guide.title}</span>
      </a>
    </article>
  );
}

// ---------------------------------------------------------------------------------------------
// Diagnosis
// ---------------------------------------------------------------------------------------------

type DiagnosisState =
  | { runId: string; metrics: MetricSample[]; error: null }
  | { runId: string; metrics: null; error: string };

function runTime(run: BenchmarkRun): string {
  return run.completed_at ?? run.updated_at ?? run.created_at;
}

function runOptionLabel(run: BenchmarkRun, plan: BenchmarkPlan | undefined): string {
  const when = new Date(runTime(run));
  const date = Number.isNaN(when.getTime()) ? runTime(run) : when.toLocaleString();
  return `${plan?.name ?? "Run"} · ${date} · ${run.id.slice(0, 8)}`;
}

function DiagnosisPanel({ requestedRunId }: { requestedRunId: string | null }) {
  const selectId = useId();
  const [runs, setRuns] = useState<BenchmarkRun[] | null>(null);
  const [plans, setPlans] = useState<Map<string, BenchmarkPlan>>(new Map());
  const [listError, setListError] = useState<string | null>(null);
  const [chosenRunId, setChosenRunId] = useState<string | null>(requestedRunId);
  const [linkedRunId, setLinkedRunId] = useState(requestedRunId);
  const [diagnosis, setDiagnosis] = useState<DiagnosisState | null>(null);

  if (requestedRunId !== linkedRunId) {
    setLinkedRunId(requestedRunId);
    if (requestedRunId) setChosenRunId(requestedRunId);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([listRuns(100), listBenchmarkPlans().catch(() => [] as BenchmarkPlan[])])
      .then(async ([allRuns, allPlans]) => {
        const completed = allRuns.filter((run) => run.status === "completed");
        if (requestedRunId && !completed.some((run) => run.id === requestedRunId)) {
          try {
            const linked = await getRun(requestedRunId);
            if (linked.status === "completed") completed.push(linked);
          } catch {
            // A linked run that cannot be loaded is simply not offered.
          }
        }
        completed.sort((left, right) => runTime(right).localeCompare(runTime(left)));
        if (cancelled) return;
        setRuns(completed);
        setPlans(new Map(allPlans.map((plan) => [plan.id, plan])));
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setListError(error instanceof Error ? error.message : "Runs could not be loaded.");
        }
      });
    return () => {
      cancelled = true;
    };
    // Loaded once; a later deep link selects from this list or is fetched below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedRun =
    runs?.find((run) => run.id === chosenRunId) ?? (runs && runs.length ? runs[0] : undefined);
  const selectedRunId = selectedRun?.id ?? null;

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    listRunMetrics(selectedRunId)
      .then((response) => {
        if (!cancelled)
          setDiagnosis({ runId: selectedRunId, metrics: response.metrics, error: null });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setDiagnosis({
            runId: selectedRunId,
            metrics: null,
            error: error instanceof Error ? error.message : "Measurements could not be loaded.",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const plan = selectedRun ? plans.get(selectedRun.plan_id) : undefined;
  const current = diagnosis && diagnosis.runId === selectedRunId ? diagnosis : null;
  const findings = useMemo(
    () => (current?.metrics ? diagnoseRun(current.metrics, diagnosisContextFromPlan(plan)) : null),
    [current, plan],
  );

  function choose(id: string) {
    setChosenRunId(id);
    const metric = parseLearningHash(window.location.hash).metric;
    window.history.replaceState(null, "", learningHref({ metric, run: id }));
  }

  return (
    <section className="panel" id="learning-diagnosis" aria-labelledby="learning-diagnosis-title">
      <div className="section-title-row">
        <div>
          <p className="eyebrow">EVIDENCE, NOT VERDICTS</p>
          <h2 id="learning-diagnosis-title" tabIndex={-1}>
            Diagnose a run
          </h2>
          <p className="helper-copy">
            Pick a completed run to see which patterns its recorded metrics are consistent with.
          </p>
        </div>
      </div>

      {listError ? (
        <p className="empty-inline" role="alert">
          Runs could not be loaded: {listError}
        </p>
      ) : runs === null ? (
        <p className="empty-inline" aria-live="polite">
          Loading completed runs…
        </p>
      ) : runs.length === 0 ? (
        <p className="empty-inline">
          No completed runs yet.{" "}
          <a className="text-link" href="#workloads">
            Build a workload
          </a>{" "}
          and run it, then come back here.
        </p>
      ) : (
        <>
          <div className="learning-controls">
            <label htmlFor={selectId}>
              Completed run
              <select
                id={selectId}
                onChange={(event) => choose(event.target.value)}
                value={selectedRunId ?? ""}
              >
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {runOptionLabel(run, plans.get(run.plan_id))}
                  </option>
                ))}
              </select>
            </label>
            {selectedRunId && (
              <a className="text-link" href={`#runs?run=${encodeURIComponent(selectedRunId)}`}>
                Open this run's measurements
              </a>
            )}
          </div>
          <div className="notice learning-disclaimer">
            These observations are heuristics over recorded measurements. They describe what the
            data is consistent with, not a confirmed root cause. To test an explanation, change one
            setting and run again.
          </div>
          {current?.error ? (
            <p className="empty-inline" role="alert">
              Measurements could not be loaded: {current.error}
            </p>
          ) : !findings ? (
            <p className="empty-inline" aria-live="polite">
              Loading measurements…
            </p>
          ) : (
            <DiagnosisResults findings={findings} />
          )}
        </>
      )}
    </section>
  );
}

function DiagnosisResults({ findings }: { findings: DiagnosisFinding[] }) {
  const observed = findings.filter((finding) => finding.status === "observed");
  const quiet = findings.filter((finding) => finding.status === "not_observed");
  const insufficient = findings.filter((finding) => finding.status === "insufficient_data");
  return (
    <div className="learning-diagnosis" aria-live="polite">
      <div className="run-subsection">
        <h3>Observations ({observed.length})</h3>
        {observed.length === 0 ? (
          <p className="empty-inline">
            None of the checks found a pattern worth flagging in this run.
          </p>
        ) : (
          <ul className="learning-findings">
            {observed.map((finding) => (
              <FindingCard finding={finding} key={finding.id} />
            ))}
          </ul>
        )}
      </div>
      {quiet.length > 0 && (
        <details className="measure-details run-subsection">
          <summary>Checked without a signal ({quiet.length})</summary>
          <ul className="learning-findings">
            {quiet.map((finding) => (
              <FindingCard finding={finding} key={finding.id} />
            ))}
          </ul>
        </details>
      )}
      {insufficient.length > 0 && (
        <details className="measure-details run-subsection">
          <summary>Not enough data ({insufficient.length})</summary>
          <ul className="learning-findings">
            {insufficient.map((finding) => (
              <FindingCard finding={finding} key={finding.id} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function FindingCard({ finding }: { finding: DiagnosisFinding }) {
  const badge =
    finding.status === "insufficient_data"
      ? "Not enough data"
      : finding.status === "not_observed"
        ? "No signal"
        : finding.severity === "attention"
          ? "Worth a look"
          : "Note";
  const definitions = finding.metrics.filter((name) => glossaryEntry(name));
  return (
    <li
      className={`learning-finding learning-finding--${finding.status} learning-finding--${finding.severity}`}
    >
      <div className="learning-finding__head">
        <span className="learning-badge">{badge}</span>
        <h4>{finding.title}</h4>
      </div>
      <p>{finding.statement}</p>
      {finding.evidence.length > 0 && (
        <dl className="measure-list learning-evidence">
          {finding.evidence.map((item) => (
            <div key={`${item.metric}-${item.label}`}>
              <dt>
                <MetricHelp metric={item.metric}>{item.label}</MetricHelp>
              </dt>
              <dd>
                {formatMetricValue(item.value, item.unit)}
                {item.sampleCount !== null && (
                  <span className="measure-muted"> · n={item.sampleCount}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {finding.nextStep && (
        <p className="measure-note">
          <strong>Try next:</strong> {finding.nextStep}
        </p>
      )}
      {definitions.length > 0 && (
        <p className="measure-note">
          Definitions:{" "}
          {definitions.map((name, index) => (
            <span key={name}>
              {index > 0 && ", "}
              <MetricHelp metric={name} />
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
