import { expect, test } from "@playwright/test";

test("opens the local workspace, navigates, and recovers after reload", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.locator(".api-status [role=status]")).toContainText("Local API connected");
  await expect(page.getByText("No benchmark runs yet")).toBeVisible();

  await page.getByRole("link", { name: "Engines", exact: true }).click();
  await expect(page).toHaveURL(/#engines$/);
  await expect(page.getByRole("heading", { name: "Engines" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Engines" })).toBeVisible();
  await expect(page.locator(".api-status [role=status]")).toContainText("Local API connected");
  await expect(page.getByText("Internal Server Error")).toHaveCount(0);

  await page.getByRole("link", { name: "System", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Current machine" })).toBeVisible();
  await page.getByRole("button", { name: "Save machine snapshot" }).click();
  await expect(page.locator(".success-message")).toContainText(
    "This detected snapshot is saved to the local machine profile list.",
  );
  await expect(page.getByRole("heading", { name: "This machine" })).toBeVisible();
});
