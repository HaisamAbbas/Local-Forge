import { defineConfig, devices } from "@playwright/test";
import { randomUUID } from "node:crypto";
import os from "node:os";
import path from "node:path";

const testDataDirectory = path.join(os.tmpdir(), "localforge-playwright", randomUUID());

export default defineConfig({
  testDir: "./e2e",
  // The browser suite shares one temporary LOCALFORGE database, so keep its scenarios isolated.
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:15174",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: [
    {
      command:
        "uv run --project ../backend uvicorn localforge.api.main:app --host 127.0.0.1 --port 18114",
      url: "http://127.0.0.1:18114/health",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        LOCALFORGE_DATA_DIR: testDataDirectory,
        // Browser checks drive the deterministic fake engine, which is hidden by default.
        LOCALFORGE_SHOW_TEST_FIXTURE_ENGINES: "1",
      },
    },
    {
      command: "npm run dev -- --host 127.0.0.1 --port 15174 --strictPort",
      url: "http://127.0.0.1:15174/",
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        LOCALFORGE_VITE_API_ORIGIN: "http://127.0.0.1:18114",
      },
    },
  ],
});
