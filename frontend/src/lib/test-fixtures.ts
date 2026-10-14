import type { BenchmarkPlan, BenchmarkRun, EngineProfile, WorkspaceSettingsResponse } from "./api";

/**
 * Adapter ids to hide (test-fixture engines such as localforge.fake), or null when nothing
 * should be hidden: Settings shows fixtures, or Settings could not be loaded.
 */
export function hiddenFixtureAdapters(
  settings: WorkspaceSettingsResponse | null,
): Set<string> | null {
  if (!settings || settings.settings.show_test_fixture_engines) return null;
  return new Set(settings.test_fixture_adapter_ids);
}

/** Engines that are not hidden test fixtures. */
export function visibleEngines(
  engines: EngineProfile[],
  settings: WorkspaceSettingsResponse | null,
): EngineProfile[] {
  const hidden = hiddenFixtureAdapters(settings);
  return hidden ? engines.filter((engine) => !hidden.has(engine.adapter_id)) : engines;
}

/**
 * Plans and runs bound to a hidden fixture engine. A run's captured engine snapshot wins;
 * otherwise its plan's current engine profile decides. Unknown engines stay visible.
 */
export function fixtureFilter(
  engines: EngineProfile[],
  plans: BenchmarkPlan[],
  settings: WorkspaceSettingsResponse | null,
) {
  const hidden = hiddenFixtureAdapters(settings);
  const adapterByEngine = new Map(engines.map((engine) => [engine.id, engine.adapter_id]));
  const planById = new Map(plans.map((plan) => [plan.id, plan]));
  const isFixturePlan = (plan: BenchmarkPlan | undefined | null) =>
    Boolean(hidden && plan && hidden.has(adapterByEngine.get(plan.engine_profile_id) ?? ""));
  const isFixtureRun = (run: BenchmarkRun) => {
    if (!hidden) return false;
    const captured = run.profile_snapshot?.engine?.adapter_id;
    if (captured) return hidden.has(captured);
    return isFixturePlan(planById.get(run.plan_id));
  };
  return { active: hidden !== null, isFixturePlan, isFixtureRun };
}
