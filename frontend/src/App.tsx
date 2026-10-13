import { useEffect, useState } from "react";

import { ApiStatus } from "./components/ApiStatus";
import { RunsScreen } from "./components/RunsScreen";
import { WorkloadsScreen } from "./components/WorkloadsScreen";
import { EnginesScreen, OverviewScreen, SystemScreen } from "./components/WorkspaceScreens";

const navigation = [
  {
    id: "overview",
    label: "Overview",
    description: "A live view of your machine, connected engines, and benchmark activity.",
  },
  {
    id: "engines",
    label: "Engines",
    description: "Connect runtimes and inspect what each server supports.",
  },
  {
    id: "system",
    label: "System",
    description: "Inspect detected machine details and their sources.",
  },
  {
    id: "workloads",
    label: "Workloads",
    description: "Define request patterns to measure an engine under realistic traffic.",
  },
  {
    id: "runs",
    label: "Runs",
    description: "Inspect benchmark progress, outcomes, and measurements.",
  },
  {
    id: "experiments",
    label: "Experiments",
    description: "Compare controlled configuration changes.",
  },
  {
    id: "learning",
    label: "Learning",
    description: "Explore inference concepts through measurable experiments.",
  },
] as const;

type ScreenId = (typeof navigation)[number]["id"];

function locationFromHash(): { screen: ScreenId; runId: string | null } {
  const [hashScreen, query = ""] = window.location.hash.slice(1).split("?", 2);
  const screen = navigation.some((item) => item.id === hashScreen)
    ? (hashScreen as ScreenId)
    : "overview";
  const requestedRunId = new URLSearchParams(query).get("run");
  const runId =
    screen === "runs" &&
    requestedRunId &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      requestedRunId,
    )
      ? requestedRunId
      : null;
  return { screen, runId };
}

export function App() {
  const [location, setLocation] = useState(locationFromHash);
  const activeScreen = location.screen;
  const currentScreen = navigation.find((item) => item.id === activeScreen) ?? navigation[0]!;

  useEffect(() => {
    const updateScreen = () => setLocation(locationFromHash());
    window.addEventListener("hashchange", updateScreen);
    return () => window.removeEventListener("hashchange", updateScreen);
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-copy">
          <p className="eyebrow">INFERENCE LAB / PRIVATE BY DEFAULT</p>
          <p className="topbar-caption">Benchmark local runtimes and endpoints you choose.</p>
        </div>
      </header>
      <div className="workspace">
        <header className="site-navigation">
          <a className="brand" href="#overview" aria-label="LOCALFORGE Inference Lab home">
            <span className="brand-mark" aria-hidden="true">
              LF
            </span>
            <span className="brand-name">
              LOCAL<span>FORGE</span>
              <small>Inference Lab</small>
            </span>
          </a>

          <nav className="primary-navigation" aria-label="Primary navigation">
            {navigation.map((item, index) => (
              <a
                aria-current={activeScreen === item.id ? "page" : undefined}
                className="navigation-link"
                href={`#${item.id}`}
                key={item.id}
              >
                <span className="navigation-label">{item.label}</span>
                <span className="navigation-index" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
              </a>
            ))}
          </nav>
          <div className="site-nav-status">
            <ApiStatus />
          </div>
        </header>

        <main className={`main-content main-content--${activeScreen}`}>
          <div className={`page-heading page-heading--${activeScreen}`}>
            <div>
              <p className="eyebrow">WORKSPACE / {currentScreen.label.toUpperCase()}</p>
              <h1>{currentScreen.label}</h1>
              <p className="page-description">{currentScreen.description}</p>
            </div>
            <span className="local-badge">
              <span className="local-dot" aria-hidden="true" />
              Private by default
            </span>
          </div>

          {activeScreen === "overview" ? (
            <OverviewScreen />
          ) : activeScreen === "engines" ? (
            <EnginesScreen />
          ) : activeScreen === "system" ? (
            <SystemScreen />
          ) : activeScreen === "workloads" ? (
            <WorkloadsScreen />
          ) : activeScreen === "runs" ? (
            <RunsScreen
              selectedRunId={location.runId}
              onSelectRun={(id) => {
                window.location.hash = id ? `runs?run=${encodeURIComponent(id)}` : "runs";
              }}
            />
          ) : (
            <SectionEmptyState screen={activeScreen} />
          )}
        </main>
      </div>
    </div>
  );
}

function SectionEmptyState({ screen }: { screen: "experiments" | "learning" }) {
  return (
    <section className="section-empty-state" aria-live="polite">
      <p className="eyebrow">
        {screen === "experiments" ? "CONTROLLED COMPARISONS" : "INFERENCE FIELD NOTES"}
      </p>
      <h2>
        {screen === "experiments"
          ? "Compare one change at a time."
          : "Build intuition from measured results."}
      </h2>
      <p>
        {screen === "experiments"
          ? "Side-by-side experiment workflows are not available yet. You can still create a repeatable workload and inspect its measured runs."
          : "Learning guides are not available yet. Start with your machine details, then run a workload to see how your chosen engine responds."}
      </p>
      <div className="section-empty-actions">
        {screen === "experiments" ? (
          <>
            <a className="button button--primary" href="#workloads">
              Open workloads
            </a>
            <a className="text-link" href="#runs">
              View run history
            </a>
          </>
        ) : (
          <>
            <a className="button button--primary" href="#system">
              Inspect this machine
            </a>
            <a className="text-link" href="#workloads">
              Build a workload
            </a>
          </>
        )}
      </div>
    </section>
  );
}
