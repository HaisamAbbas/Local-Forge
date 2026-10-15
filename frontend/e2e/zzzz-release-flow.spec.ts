import { Buffer } from "node:buffer";

import { expect, test, type Page } from "@playwright/test";

async function registerAndEnableFakeEngine(page: Page, profileName: string): Promise<string> {
  await page.goto("/#engines");
  await page
    .locator(".catalog-card")
    .filter({ has: page.getByRole("heading", { name: "Deterministic Fake Engine" }) })
    .getByRole("button", { name: "Add this adapter" })
    .click();

  const dialog = page.getByRole("dialog", { name: "Add an engine" });
  await dialog.getByRole("textbox", { name: "Profile name" }).fill(profileName);
  await dialog.getByRole("button", { name: "Add engine" }).click();

  const profile = page
    .locator(".engine-profile-card")
    .filter({ has: page.getByRole("heading", { name: profileName }) });
  // Saving a new attached profile probes and enables it automatically.
  await expect(profile.locator(".status-pill")).toHaveText("Enabled");
  await expect(page.getByRole("heading", { name: "Models reported by this probe" })).toBeVisible();

  await page.goto("/#workloads");
  const option = page
    .getByLabel("Engine profile")
    .locator("option")
    .filter({ hasText: profileName });
  const profileId = await option.getAttribute("value");
  if (!profileId) throw new Error(`The registered fake engine ${profileName} is unavailable.`);

  await page.getByLabel("Engine profile").selectOption(profileId);
  await expect(page.getByText("This engine has been probed but is disabled.")).toHaveCount(0);
  return profileId;
}

async function saveAndLaunchPlan(page: Page): Promise<string> {
  await page.getByRole("button", { name: "Save plan" }).click();
  await page.getByRole("button", { name: "Launch benchmark" }).click();

  const queuedNotice = page.locator(".workload-run-created");
  await expect(queuedNotice).toBeVisible({ timeout: 15_000 });
  const runId = (await queuedNotice.locator("code").textContent())?.trim();
  if (!runId) throw new Error("The run start response did not include a run ID.");
  await queuedNotice.getByRole("link", { name: "Open live run details" }).click();
  return runId;
}

test("completes the model-free release workflow from local setup through redacted report", async ({
  page,
  request,
}) => {
  test.setTimeout(150_000);
  const suffix = Date.now().toString();
  const profileName = `Prompt 18 release fixture ${suffix}`;
  const baselinePlanName = `Prompt 18 retained baseline ${suffix}`;
  const cancelPlanName = `Prompt 18 cancellation ${suffix}`;
  const experimentName = `Prompt 18 controlled comparison ${suffix}`;
  const secret = `release-flow-secret-${suffix}`;
  page.setDefaultTimeout(10_000);

  // 1. Start locally and inspect the data-retention disclosure before importing a fixture.
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.locator(".api-status [role=status]")).toContainText("Local API connected");
  await page.goto("/#workloads");
  await page.getByLabel("Request source").selectOption("dataset");
  await expect(
    page.getByText("Upload stays on this LOCALFORGE machine. Preview rows are always redacted."),
  ).toBeVisible();

  // 2. Inspect the current host and the UI's best-effort handling of unknown machine details.
  await page.goto("/#system");
  await expect(page.getByRole("heading", { name: "Current machine" })).toBeVisible();
  await expect(page.getByText(/LOCAL HOST.*BEST EFFORT DETECTION/)).toBeVisible();
  await expect(
    page.getByText("Detection is best effort. Missing values are not inferred."),
  ).toBeVisible();
  await expect(page.locator(".facts-grid").getByText("Accelerators", { exact: true })).toBeVisible();

  // 3. Register, probe, and enable the deterministic fake engine without loading a model.
  const profileId = await registerAndEnableFakeEngine(page, profileName);

  // 4. Import a local fixture. Its secret-like text must not appear in the redacted preview.
  const baselinePlan = page.getByLabel("Plan and workload name");
  await baselinePlan.fill(baselinePlanName);
  await page.getByLabel("Request source").selectOption("dataset");
  await page.getByLabel("Dataset name").fill(`Release fixture ${suffix}`);
  await page.locator('input[type="file"]').setInputFiles({
    name: "release-prompts.jsonl",
    mimeType: "application/x-ndjson",
    buffer: Buffer.from(
      [
        JSON.stringify({ prompt: `Explain local inference; api_key=${secret}` }),
        JSON.stringify({ prompt: "Explain how request latency is measured locally." }),
      ].join("\n") + "\n",
    ),
  });
  await page.getByRole("checkbox", { name: /Retain prompt content locally/ }).check();
  await page.getByLabel("Measured request limit").fill("2");
  await page.getByLabel("Warmup requests").fill("0");
  await page.getByLabel("Output token target and maximum").fill("8");
  await page
    .getByRole("checkbox", { name: /Retain prompt and generated output content with this plan/ })
    .check();

  // 5. Run preflight and prove that the retained dataset preview stays redacted.
  await page.getByRole("button", { name: "Review preflight" }).click();
  await expect(page.getByRole("heading", { name: "Preflight review" })).toBeVisible();
  await expect(
    page.getByText("Preflight has no blockers. Estimates are not a guarantee."),
  ).toBeVisible();
  const datasetSummary = page.locator(".dataset-summary");
  await expect(datasetSummary).toContainText("Dataset validated");
  await expect(datasetSummary).toContainText("Prompt snapshot retained locally for replay.");
  await expect(datasetSummary).not.toContainText(secret);

  // 6. Launch the benchmark, follow the live event stream, and inspect observed metrics.
  const baselineRunId = await saveAndLaunchPlan(page);
  const runDetail = page.locator(".run-detail-panel");
  await expect(runDetail.getByText(/Live event stream connected/)).toBeVisible({ timeout: 10_000 });
  await expect(runDetail.locator(".status-pill")).toHaveText("completed", { timeout: 20_000 });
  await expect(
    runDetail.locator(".key-result").filter({ hasText: "Succeeded" }).locator("strong"),
  ).toHaveText("2 / 2");
  for (const label of ["Request latency", "Request rate"]) {
    await expect(
      runDetail.locator(".key-result").filter({ hasText: label }).locator("strong"),
    ).not.toHaveText(/^(Unavailable|—)$/);
  }
  await expect(
    runDetail.getByRole("heading", { name: "Request latency observations" }),
  ).toBeVisible();
  await expect(
    runDetail.getByRole("heading", { name: "Resource usage during the run" }),
  ).toBeVisible();

  // 7. Launch a second run and verify that cancellation reaches a terminal cancelled state.
  await page.goto("/#workloads");
  await page.getByLabel("Engine profile").selectOption(profileId);
  await page.getByLabel("Plan and workload name").fill(cancelPlanName);
  await page.getByLabel("Measured request limit").fill("5000");
  await page.getByLabel("Warmup requests").fill("0");
  await page.getByLabel("Input size in tokens").fill("8");
  await page.getByLabel("Output token target and maximum").fill("8");
  await page.getByRole("button", { name: "Review preflight" }).click();
  await expect(page.getByRole("heading", { name: "Preflight review" })).toBeVisible();
  await expect(
    page.getByText("Preflight has no blockers. Estimates are not a guarantee."),
  ).toBeVisible();
  const cancelledRunId = await saveAndLaunchPlan(page);
  await expect(page).toHaveURL(new RegExp(`#runs\\?run=${cancelledRunId}$`));
  const cancellationDetail = page.locator(".run-detail-panel");
  await expect(cancellationDetail.getByText(/Live event stream connected/)).toBeVisible({
    timeout: 10_000,
  });
  const cancelButton = cancellationDetail.getByRole("button", { name: "Cancel run" });
  await expect(cancelButton).toBeVisible();
  await cancelButton.click();
  await cancellationDetail.getByRole("button", { name: "Confirm cancellation" }).click();
  await expect(cancellationDetail.locator(".status-pill")).toHaveText("cancelled", {
    timeout: 20_000,
  });

  // 8. Reopen the completed run and inspect normalized versus raw metric-source sections.
  await page.goto(`/#runs?run=${baselineRunId}`);
  const reopenedDetail = page.locator(".run-detail-panel");
  await expect(reopenedDetail.getByRole("heading", { name: baselinePlanName })).toBeVisible();
  await expect(reopenedDetail.locator(".status-pill")).toHaveText("completed");
  await expect(reopenedDetail).toContainText("Bodies retained by plan opt-in");
  await expect(reopenedDetail.getByRole("heading", { name: "Latency and timing" })).toBeVisible();
  const metricExplorer = reopenedDetail.locator(".metric-explorer");
  await metricExplorer.locator("summary").click();
  const explorerSource = metricExplorer.getByLabel("Source");
  await explorerSource.selectOption("derived");
  await expect(metricExplorer.locator("tbody tr").first()).toBeVisible();
  await expect(metricExplorer.locator("tbody")).toContainText("derived");
  await explorerSource.selectOption("engine_native");
  await expect(metricExplorer.locator("tbody")).toContainText("fake.requests_total");
  await expect(metricExplorer.locator("tbody")).toContainText("engine_native");
  await expect(metricExplorer.locator("tbody")).toContainText("Unavailable");
  await expect(reopenedDetail).not.toContainText(secret);

  // 9. Change one declared workload value and review the two-run comparability explanation.
  await page.goto("/#experiments");
  await page.getByLabel("Experiment name").fill(experimentName);
  await page.getByLabel("Baseline plan").selectOption({ label: baselinePlanName });
  await page.getByLabel("Independent variable").selectOption("max_output_tokens");
  await page.getByLabel("Variant values").fill("16");
  await page.getByLabel("Repetitions per setting").fill("1");
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Create and run sweep" }).click();

  const group = page.locator(".experiment-group-card").filter({
    has: page.getByRole("heading", { name: experimentName }),
  });
  await expect(group.locator(".experiment-group-card-heading .status-pill")).toHaveText(
    "completed",
    { timeout: 60_000 },
  );
  await group.getByRole("button", { name: "Compare group" }).click();
  await expect(page.getByRole("heading", { name: "Mostly controlled", exact: true })).toBeVisible();
  const declaredChange = page
    .locator(".comparison-dimension")
    .filter({ hasText: "Workload, input snapshot, and sampling settings" });
  await declaredChange.locator("summary").click();
  await expect(declaredChange).toContainText("declared change");
  await expect(declaredChange).toContainText("workload.max_output_tokens");

  // 10. Export the run provenance and retained content, then verify secret removal in the file.
  const includeRetained = page.getByRole("checkbox", {
    name: "Include prompt/output text retained by every selected plan",
  });
  await expect(includeRetained).toBeEnabled();
  await includeRetained.check();
  await page.getByLabel("Format").selectOption("json");
  await page.getByRole("button", { name: "Create report" }).click();
  const download = page.getByRole("link", { name: "Download JSON" });
  await expect(download).toBeVisible();
  const href = await download.getAttribute("href");
  if (!href) throw new Error("The report download link does not have a target.");
  const response = await request.get(new URL(href, page.url()).toString());
  expect(response.ok()).toBe(true);
  const report = await response.json();
  const serializedReport = JSON.stringify(report);
  expect(report.report_version).toBe(1);
  expect(report.privacy.credentials_included).toBe(false);
  expect(report.comparability.grade).toBe("B");
  expect(report.experiment_group.independent_variable).toBe("max_output_tokens");
  expect(report.experiment_group.trials).toHaveLength(2);
  expect(report.runs).toHaveLength(2);
  expect(
    report.runs.every(
      (item: { plan?: { retain_prompt_output?: boolean } }) => item.plan?.retain_prompt_output,
    ),
  ).toBe(true);
  expect(
    report.runs.some((item: { request_content?: Array<{ prompt_text?: string | null }> }) =>
      item.request_content?.some((sample) => sample.prompt_text?.includes("api_key=[REDACTED]")),
    ),
  ).toBe(true);
  expect(serializedReport).not.toContain(secret);
  expect(serializedReport).toContain("api_key=[REDACTED]");
  expect(serializedReport).toContain("localforge.fake");
});
