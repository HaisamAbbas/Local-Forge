import { expect, test } from "@playwright/test";

test("opens the local workspace, navigates, and recovers after reload", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Local API connected");
  await expect(page.getByText("No benchmark runs yet")).toBeVisible();

  await page.getByRole("link", { name: "Engines" }).click();
  await expect(page).toHaveURL(/#engines$/);
  await expect(page.getByRole("heading", { name: "Engines" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Engines" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Local API connected");
  await expect(page.getByText("Internal Server Error")).toHaveCount(0);
});
