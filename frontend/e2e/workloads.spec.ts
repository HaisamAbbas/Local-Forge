import { expect, test } from "@playwright/test";

test("builds, reviews, saves, and launches a fake-engine benchmark plan", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#engines");
  await expect(page.getByRole("heading", { name: "Engine catalog" })).toBeVisible();
  await page
    .locator(".catalog-card")
    .filter({ has: page.getByRole("heading", { name: "Deterministic Fake Engine" }) })
    .getByRole("button", { name: "Add this adapter" })
    .click();
  const dialog = page.getByRole("dialog", { name: "Add an engine" });
  await dialog.getByRole("textbox", { name: "Profile name" }).fill("Prompt 9 fake target");
  await dialog.getByRole("button", { name: "Add engine" }).click();

  const profile = page
    .locator(".engine-profile-card")
    .filter({ has: page.getByRole("heading", { name: "Prompt 9 fake target" }) });
  // A new attached profile is probed and enabled automatically after saving.
  await expect(profile.locator(".status-pill")).toHaveText("Enabled");
  await expect(page.getByRole("heading", { name: "Models reported by this probe" })).toBeVisible();

  // The checklist's Benchmark link opens Workloads with this engine preselected.
  await profile.getByRole("link", { name: "Benchmark" }).click();
  await expect(page).toHaveURL(/#workloads\?engine=/);
  const engineSelect = page.getByLabel("Engine profile");
  await expect(engineSelect.locator("option:checked")).toHaveText(/Prompt 9 fake target/);
  await expect(page.getByRole("option", { name: /LocalForge Test Model/ })).toBeAttached();
  await expect(page.getByText("This engine has been probed but is disabled.")).toHaveCount(0);

  const planName = page.getByLabel("Plan and workload name");
  await planName.focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Engine profile")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Model")).toBeFocused();
  await planName.fill("Browser fake benchmark");
  await page.getByLabel("Workload template").selectOption("mixed_chat");
  await page.getByLabel("Measured request limit").fill("2");
  await page.getByLabel("Warmup requests").fill("0");
  await page.getByLabel("Input size in tokens").fill("8");
  await page.getByLabel("Output token target and maximum").fill("8");
  await page.getByRole("button", { name: "Review preflight" }).click();

  await expect(page.getByRole("heading", { name: "Preflight review" })).toBeVisible();
  await expect(
    page.getByText("Preflight has no blockers. Estimates are not a guarantee."),
  ).toBeVisible();
  await expect(page.getByText("LocalForge Test Model")).toBeVisible();
  await page.getByRole("button", { name: "Save plan" }).click();
  await page.getByRole("button", { name: "Launch benchmark" }).click();
  await expect(page.getByText(/Run queued:/)).toBeVisible({ timeout: 15_000 });
});
