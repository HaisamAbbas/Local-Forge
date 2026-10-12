import { expect, test, type Page } from "@playwright/test";

async function registerFakeEngine(page: Page, label: string) {
  await page.goto("/#engines");
  await page.getByRole("button", { name: "Add this adapter" }).click();
  const dialog = page.getByRole("dialog", { name: "Add an engine" });
  await dialog.getByRole("textbox", { name: "Profile name" }).fill(label);
  await dialog.getByRole("button", { name: "Add engine" }).click();
  const profile = page.locator(".engine-profile-card").filter({
    has: page.getByRole("heading", { name: label }),
  });
  await profile.getByRole("button", { name: "Probe" }).click();
  await expect(page.getByRole("heading", { name: "Models reported by this probe" })).toBeVisible();
}

async function saveAndLaunchPlan(page: Page, name: string, requestCount: number) {
  await page.goto("/#workloads");
  await page.getByLabel("Plan and workload name").fill(name);
  await page.getByLabel("Measured request limit").fill(String(requestCount));
  await page.getByLabel("Warmup requests").fill("0");
  await page.getByLabel("Input size in tokens").fill("8");
  await page.getByLabel("Output token target and maximum").fill("8");
  await page.getByRole("button", { name: "Review preflight" }).click();
  await expect(page.getByRole("heading", { name: "Preflight review" })).toBeVisible();
  await expect(
    page.getByText("Preflight has no blockers. Estimates are not a guarantee."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Save plan" }).click();
  await page.getByRole("button", { name: "Launch benchmark" }).click();
  const queuedNotice = page.locator(".workload-run-created");
  await expect(queuedNotice).toBeVisible({ timeout: 15_000 });
  const id = await queuedNotice.locator("code").textContent();
  if (!id) throw new Error("The run start response did not include a run ID.");
  await queuedNotice.getByRole("link", { name: "Open live run details" }).click();
  return id;
}

async function selectFakeEngine(page: Page, label: string) {
  const option = page.getByLabel("Engine profile").locator("option").filter({ hasText: label });
  const engineId = await option.first().getAttribute("value");
  if (!engineId) throw new Error(`Could not find the saved engine profile ${label}.`);
  await page.getByLabel("Engine profile").selectOption(engineId);
  await expect(page.getByLabel("Model")).toHaveValue(/.+/);
}

test("follows, reconnects to, and cancels fake-engine runs from history", async ({ page }) => {
  test.setTimeout(90_000);
  const suffix = Date.now().toString();
  await registerFakeEngine(page, `Prompt 10 fake ${suffix}`);

  await page.goto("/#workloads");
  await selectFakeEngine(page, `Prompt 10 fake ${suffix}`);
  await page.getByRole("button", { name: "Enable this probed engine" }).click();

  const completedPlanName = `Prompt 10 completed ${suffix}`;
  const completedRunId = await saveAndLaunchPlan(page, completedPlanName, 3);
  let completedRunReads = 0;
  page.on("request", (request) => {
    if (
      request.method() === "GET" &&
      new URL(request.url()).pathname === `/api/v1/runs/${completedRunId}`
    ) {
      completedRunReads += 1;
    }
  });
  const detail = page.locator(".run-detail-panel");
  await expect(page).toHaveURL(new RegExp(`#runs\\?run=${completedRunId}$`));
  await expect(detail.getByRole("heading", { name: completedPlanName })).toBeVisible();
  await expect(detail.locator(".status-pill")).toHaveText("completed", { timeout: 20_000 });
  const settledRunReads = completedRunReads;
  await page.waitForTimeout(1300);
  expect(completedRunReads).toBe(settledRunReads);
  await expect(detail.getByRole("heading", { name: "Request latency observations" })).toBeVisible();
  await expect(
    detail.locator(".run-fact").filter({ hasText: "Latency samples" }).locator("strong"),
  ).toHaveText("3");
  await expect(
    detail.getByRole("heading", { name: "Engine and capabilities at run start" }),
  ).toBeVisible();
  await expect(
    detail.getByRole("table", {
      name: "Accessible table of the latency points shown in the chart",
    }),
  ).toBeVisible();
  await expect(detail.getByText("Client measured").first()).toBeVisible();
  await page.reload();
  await expect(detail.getByRole("heading", { name: completedPlanName })).toBeVisible();
  await expect(detail.locator(".status-pill")).toHaveText("completed");
  await expect(
    detail
      .getByRole("table", { name: "Accessible table of the latency points shown in the chart" })
      .getByRole("row"),
  ).toHaveCount(4);

  const serializedEvents = await page.evaluate(async (runId) => {
    const response = await fetch(`/api/v1/runs/${runId}/events`);
    return response.text();
  }, completedRunId);
  expect(serializedEvents).not.toContain("deterministic fake response");
  expect(serializedEvents).not.toMatch(/"(prompt_text|output_text)"\s*:/);
  await expect(detail.getByText("deterministic fake response", { exact: false })).toHaveCount(0);

  await detail.getByRole("button", { name: "Back to history" }).click();
  const completedHistoryRow = page.getByRole("button", {
    name: new RegExp(`Run ${completedRunId.slice(0, 8)}`),
  });
  await expect(completedHistoryRow).toBeVisible();
  await completedHistoryRow.click();
  await expect(detail.getByRole("heading", { name: completedPlanName })).toBeVisible();
  await expect(detail.locator(".status-pill")).toHaveText("completed");

  const cancelledPlanName = `Prompt 10 cancelled ${suffix}`;
  await page.goto("/#workloads");
  await selectFakeEngine(page, `Prompt 10 fake ${suffix}`);
  const cancelledRunId = await saveAndLaunchPlan(page, cancelledPlanName, 5000);
  await expect(detail.getByRole("heading", { name: cancelledPlanName })).toBeVisible();
  await expect(detail.getByText(/Live event stream connected/)).toBeVisible({ timeout: 10_000 });
  const cancelButton = detail.getByRole("button", { name: "Cancel run" });
  if (await cancelButton.isVisible()) {
    await cancelButton.click();
    await detail.getByRole("button", { name: "Confirm cancellation" }).click();
  }
  await expect(detail.locator(".status-pill")).toHaveText("cancelled", { timeout: 20_000 });

  await detail.getByRole("button", { name: "Back to history" }).click();
  const cancelledHistoryRow = page.getByRole("button", {
    name: new RegExp(`Run ${cancelledRunId.slice(0, 8)}`),
  });
  await expect(cancelledHistoryRow).toBeVisible();
  await cancelledHistoryRow.click();
  await expect(detail.getByRole("heading", { name: cancelledPlanName })).toBeVisible();
  await expect(detail.locator(".status-pill")).toHaveText("cancelled");
});
