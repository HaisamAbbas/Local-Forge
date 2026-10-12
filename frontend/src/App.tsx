import { useEffect, useState } from "react";

import { ApiStatus } from "./components/ApiStatus";

const navigation = [
  { id: "overview", label: "Overview", description: "Your local inference workspace." },
  {
    id: "engines",
    label: "Engines",
    description: "Connect local or explicitly selected inference engines.",
  },
  {
    id: "workloads",
    label: "Workloads",
    description: "Build repeatable request sets and traffic patterns.",
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

function screenFromHash(): ScreenId {
  const hash = window.location.hash.slice(1);
  return navigation.some((item) => item.id === hash) ? (hash as ScreenId) : "overview";
}

export function App() {
  const [activeScreen, setActiveScreen] = useState<ScreenId>(screenFromHash);
  const currentScreen = navigation.find((item) => item.id === activeScreen) ?? navigation[0]!;

  useEffect(() => {
    const updateScreen = () => setActiveScreen(screenFromHash());
    window.addEventListener("hashchange", updateScreen);
    return () => window.removeEventListener("hashchange", updateScreen);
  }, []);

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Workspace sidebar">
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
          {navigation.map((item) => (
            <a
              aria-current={activeScreen === item.id ? "page" : undefined}
              className="navigation-link"
              href={`#${item.id}`}
              key={item.id}
            >
              <span className="navigation-indicator" aria-hidden="true" />
              {item.label}
            </a>
          ))}
        </nav>

        <div className="sidebar-note">
          <span className="local-dot" aria-hidden="true" />
          Local workspace
          <small>Runs on this machine by default</small>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">LOCAL WORKSPACE</p>
            <p className="topbar-caption">Inference benchmarking and experimentation</p>
          </div>
          <ApiStatus />
        </header>

        <main className="main-content">
          <div className="page-heading">
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
            <OverviewEmptyState />
          ) : (
            <SectionEmptyState label={currentScreen.label} />
          )}
        </main>
      </div>
    </div>
  );
}

function OverviewEmptyState() {
  return (
    <section className="welcome-panel" aria-labelledby="welcome-title">
      <div className="panel-accent" aria-hidden="true" />
      <div className="welcome-copy">
        <p className="eyebrow">READY WHEN YOU ARE</p>
        <h2 id="welcome-title">Measure inference on your machine.</h2>
        <p>
          Connect an inference engine, choose a workload, and compare measured results. The
          dashboard and control service run locally; remote targets are always a deliberate choice.
        </p>
      </div>
      <div className="empty-state-row">
        <span className="empty-state-icon" aria-hidden="true">
          01
        </span>
        <div>
          <h3>No benchmark runs yet</h3>
          <p>This workspace has no measurements. Connect an engine to begin an experiment.</p>
        </div>
        <span className="empty-state-tag">EMPTY WORKSPACE</span>
      </div>
    </section>
  );
}

function SectionEmptyState({ label }: { label: string }) {
  return (
    <section className="section-empty-state" aria-live="polite">
      <p className="eyebrow">{label.toUpperCase()}</p>
      <h2>This section is ready for your workspace.</h2>
      <p>
        No sample engines, workloads, runs, or measurements are shown. The next implementation
        phases will connect this view to your local data.
      </p>
    </section>
  );
}
