import { expect, test, type Page } from "@playwright/test";

async function registerFakeEngine(page: Page, label: string) {
  await page.goto("/#engines");
  await page
    .locator(".catalog-card")
    .filter({ has: page.getByRole("heading", { name: "Deterministic Fake Engine" }) })
    .getByRole("button", { name: "Add this adapter" })
    .click();
  const dialog = page.getByRole("dialog", { name: "Add an engine" });
  await dialog.getByRole("textbox", { name: "Profile name" }).fill(label);
  await dialog.getByRole("button", { name: "Add engine" }).click();
  const profile = page.locator(".engine-profile-card").filter({
    has: page.getByRole("heading", { name: label }),
  });
  // Saving a new attached profile probes and enables it automatically.
  await expect(profile.locator(".status-pill")).toHaveText("Enabled");
  await expect(page.getByRole("heading", { name: "Models reported by this probe" })).toBeVisible();
}

test("runs a controlled sweep, explains the change, and exports its report", async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const suffix = Date.now().toString();
  const engineName = `Prompt 14 experiment ${suffix}`;
  const planName = `Prompt 14 baseline ${suffix}`;
  const experimentName = `Prompt 14 temperature sweep ${suffix}`;
  const duplicateKeyErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("same key")) {
      duplicateKeyErrors.push(message.text());
    }
  });

  await registerFakeEngine(page, engineName);
  await page.goto("/#workloads");
  const engineOption = page.getByLabel("Engine profile").locator("option").filter({
    hasText: engineName,
  });
  const engineId = await engineOption.getAttribute("value");
  if (!engineId) throw new Error("The registered fixture engine is missing from Workloads.");
  await page.getByLabel("Engine profile").selectOption(engineId);
  await page.getByLabel("Plan and workload name").fill(planName);
  await page.getByLabel("Measured request limit").fill("2");
  await page.getByLabel("Warmup requests").fill("0");
  await page.getByLabel("Input size in tokens").fill("8");
  await page.getByLabel("Output token target and maximum").fill("8");
  await page.getByRole("button", { name: "Review preflight" }).click();
  await expect(page.getByRole("heading", { name: "Preflight review" })).toBeVisible();
  await expect(
    page.getByText("Preflight has no blockers. Estimates are not a guarantee."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save plan" }).click();

  await page.goto("/#experiments");
  await page.getByLabel("Experiment name").fill(experimentName);
  await page.getByLabel("Baseline plan").selectOption({ label: planName });
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

  const workloadDimension = page
    .locator(".comparison-dimension")
    .filter({ hasText: "Workload, input snapshot, and sampling settings" });
  await workloadDimension.locator("summary").click();
  await expect(workloadDimension).toContainText("declared change");
  await expect(workloadDimension).toContainText("workload.max_output_tokens");

  await page.getByLabel("Format").selectOption("json");
  await page.getByRole("button", { name: "Create report" }).click();
  const download = page.getByRole("link", { name: "Download JSON" });
  await expect(download).toBeVisible();
  const href = await download.getAttribute("href");
  if (!href) throw new Error("The report download link does not have a target.");
  const response = await request.get(href);
  expect(response.ok()).toBe(true);
  const report = await response.json();
  expect(report.report_version).toBe(1);
  expect(report.comparability.grade).toBe("B");
  expect(report.comparability.declared_variable.path).toBe("workload.max_output_tokens");
  expect(report.experiment_group.independent_variable).toBe("max_output_tokens");
  expect(report.experiment_group.trials).toHaveLength(2);
  expect(duplicateKeyErrors).toEqual([]);

  await page.locator(".comparison-run-card input").first().uncheck();
  await expect(page.getByRole("link", { name: "Download JSON" })).toHaveCount(0);
});
