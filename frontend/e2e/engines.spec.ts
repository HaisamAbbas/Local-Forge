import { expect, test } from "@playwright/test";

test("registers, probes, edits and removes a fake engine from the UI", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#engines");
  await expect(page.getByRole("heading", { name: "Engine catalog" })).toBeVisible();
  await expect(page.getByText("Deterministic Fake Engine").first()).toBeVisible();
  await expect(page.getByText("No engine profiles are configured.")).toBeVisible();

  await page.getByRole("button", { name: "Add this adapter" }).click();
  const addDialog = page.getByRole("dialog", { name: "Add an engine" });
  const profileName = addDialog.getByRole("textbox", { name: "Profile name" });
  await expect(profileName).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(addDialog.getByLabel("Connection mode")).toBeFocused();
  await addDialog.getByRole("button", { name: "Add engine" }).focus();
  await page.keyboard.press("Tab");
  await expect(addDialog.getByRole("button", { name: "Close dialog" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(profileName).toBeFocused();
  await profileName.fill("Browser test engine");
  await addDialog.getByRole("button", { name: "Add engine" }).click();

  const profileCard = page
    .locator(".engine-profile-card")
    .filter({ has: page.getByRole("heading", { name: "Browser test engine" }) });
  await expect(profileCard).toBeVisible();
  await expect(profileCard.getByText("Not configured").first()).toBeVisible();

  const probeButton = profileCard.getByRole("button", { name: "Probe" });
  await probeButton.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Models reported by this probe" })).toBeVisible();
  await expect(
    page.getByRole("row", {
      name: /LocalForge Test Model.*fake:\/\/deterministic-model.*fixture-1/,
    }),
  ).toBeVisible();
  await expect(page.getByText("Supported", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible();

  await profileCard.getByRole("button", { name: "Edit" }).click();
  const editDialog = page.getByRole("dialog", { name: "Edit engine profile" });
  await editDialog.getByRole("textbox", { name: "Profile name" }).fill("Edited browser engine");
  await editDialog.getByRole("button", { name: "Save changes" }).click();
  const editedCard = page
    .locator(".engine-profile-card")
    .filter({ has: page.getByRole("heading", { name: "Edited browser engine" }) });
  await expect(editedCard).toBeVisible();
  await expect(editedCard.locator(".profile-attributes dd").nth(2)).toHaveText("fake-1");
  await expect(editedCard.locator(".profile-attributes dd").nth(3)).not.toHaveText("Not recorded");

  await editedCard.getByRole("button", { name: "Remove" }).click();
  const removeDialog = page.getByRole("dialog", { name: "Remove engine profile?" });
  await expect(removeDialog).toContainText("does not remove the engine or model from your machine");
  await removeDialog.getByRole("button", { name: "Remove profile" }).click();
  await expect(page.getByText("No engine profiles are configured.")).toBeVisible();
});
