import { expect, test } from "@playwright/test";

const workspacePages = [
  { id: "overview", title: "Overview", image: "localforge-overview-hero.jpg" },
  { id: "engines", title: "Engines", image: "localforge-engine-hero.jpg" },
  { id: "system", title: "System", image: "localforge-system-hero.jpg" },
  { id: "workloads", title: "Workloads", image: "localforge-workloads-hero.jpg" },
  { id: "runs", title: "Runs", image: "localforge-runs-hero.jpg" },
  { id: "experiments", title: "Experiments", image: "localforge-experiments-hero.jpg" },
  { id: "learning", title: "Learning", image: "localforge-learning-hero.jpg" },
] as const;

test("each workspace page uses its own available hero image", async ({ page, request }) => {
  await page.goto("/");
  const usedImages = new Set<string>();

  for (const { id, title, image } of workspacePages) {
    await page.locator(`.primary-navigation a[href="#${id}"]`).click();
    await expect(page.locator("h1")).toHaveText(title);

    const heroImage = await page.locator(".page-heading").evaluate((element) => {
      return window.getComputedStyle(element).backgroundImage;
    });
    expect(heroImage).toContain(image);
    expect(usedImages.has(image)).toBe(false);
    usedImages.add(image);

    const response = await request.get(`/images/${image}`);
    expect(response.ok()).toBe(true);
  }

  expect(usedImages.size).toBe(workspacePages.length);
});
