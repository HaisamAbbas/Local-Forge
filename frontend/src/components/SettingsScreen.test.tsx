import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  fetchWorkspaceSettings,
  saveWorkspaceSettings,
  type WorkspaceSettingsResponse,
} from "../lib/api";
import { SettingsScreen } from "./SettingsScreen";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchWorkspaceSettings: vi.fn(),
    saveWorkspaceSettings: vi.fn(),
  };
});

const defaults = {
  show_test_fixture_engines: false,
  default_retain_prompt_output: false,
  default_request_timeout_seconds: 120,
  default_warmup_count: 2,
};

function response(
  settings: Partial<typeof defaults> = {},
  storage: Partial<WorkspaceSettingsResponse["storage"]> = {},
): WorkspaceSettingsResponse {
  return {
    settings: { ...defaults, ...settings },
    defaults,
    saved: Object.keys(settings).length > 0,
    test_fixture_adapter_ids: ["localforge.fake"],
    storage: {
      data_directory: "D:\\LocalForgeData",
      database_path: "D:\\LocalForgeData\\localforge.sqlite3",
      free_bytes: 50 * 1024 ** 3,
      total_bytes: 200 * 1024 ** 3,
      low_space: false,
      low_space_threshold_bytes: 1024 ** 3,
      ...storage,
    },
  };
}

beforeEach(() => {
  vi.mocked(fetchWorkspaceSettings).mockResolvedValue(response());
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("settings screen", () => {
  it("shows saved values and storage without a low-space warning", async () => {
    render(<SettingsScreen />);

    expect(await screen.findByLabelText(/^Request timeout \(seconds\)/)).toHaveValue(120);
    expect(screen.getByLabelText(/^Warmup requests/)).toHaveValue(2);
    expect(screen.getByLabelText(/Show test fixture engines/)).not.toBeChecked();
    expect(screen.getByText("D:\\LocalForgeData")).toBeInTheDocument();
    expect(screen.getByText("50 GiB")).toBeInTheDocument();
    expect(screen.getByText("75% used")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
  });

  it("warns when the data volume is low on space", async () => {
    vi.mocked(fetchWorkspaceSettings).mockResolvedValue(
      response({}, { free_bytes: 300 * 1024 ** 2, low_space: true }),
    );
    render(<SettingsScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Less than 1\.0 GiB is free/);
    expect(screen.getByText("300 MiB")).toBeInTheDocument();
  });

  it("saves edited settings and discards or resets unsaved edits", async () => {
    vi.mocked(saveWorkspaceSettings).mockImplementation(async (settings) => response(settings));
    render(<SettingsScreen />);
    const timeout = await screen.findByLabelText(/^Request timeout \(seconds\)/);

    fireEvent.change(timeout, { target: { value: "45" } });
    fireEvent.click(screen.getByLabelText(/Show test fixture engines/));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Settings saved.");
    expect(saveWorkspaceSettings).toHaveBeenCalledWith({
      ...defaults,
      show_test_fixture_engines: true,
      default_request_timeout_seconds: 45,
    });

    fireEvent.change(screen.getByLabelText(/^Warmup requests/), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(screen.getByLabelText(/^Warmup requests/)).toHaveValue(2);

    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));
    expect(screen.getByLabelText(/^Request timeout \(seconds\)/)).toHaveValue(120);
    expect(screen.getByLabelText(/Show test fixture engines/)).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });

  it("blocks invalid values locally and shows API validation errors", async () => {
    vi.mocked(saveWorkspaceSettings).mockRejectedValue(
      new ApiError("Request validation failed; submitted values were omitted.", 422, null, null, {
        default_warmup_count: "less_than_equal",
      }),
    );
    render(<SettingsScreen />);
    const warmup = await screen.findByLabelText(/^Warmup requests/);

    fireEvent.change(warmup, { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Enter a whole number from 0 to 1000.")).toBeInTheDocument();
    expect(warmup).toHaveAttribute("aria-invalid", "true");
    expect(saveWorkspaceSettings).not.toHaveBeenCalled();

    fireEvent.change(warmup, { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Request validation failed");
    await waitFor(() =>
      expect(
        screen.getByText("The local API rejected this value as out of range."),
      ).toBeInTheDocument(),
    );
  });

  it("offers a retry when settings cannot be loaded", async () => {
    vi.mocked(fetchWorkspaceSettings)
      .mockRejectedValueOnce(new Error("Local API offline"))
      .mockResolvedValueOnce(response());
    render(<SettingsScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Local API offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText(/^Warmup requests/)).toHaveValue(2);
  });
});
