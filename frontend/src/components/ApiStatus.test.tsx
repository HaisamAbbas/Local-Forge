import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiStatus } from "./ApiStatus";

const healthyResponse = {
  status: "ok",
  api_version: "v1",
  app_version: "0.1.0",
  database: { status: "ready", schema_version: 1 },
};

describe("ApiStatus", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("announces an unavailable API and recovers when retry succeeds", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => healthyResponse,
    } as Response);
    vi.stubGlobal("fetch", fetchMock);

    render(<ApiStatus />);

    expect(await screen.findByRole("status")).toHaveTextContent("Local API unavailable");
    expect(screen.getByText("Start the local API, then retry the connection.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));

    expect(await screen.findByText("Local API connected")).toBeInTheDocument();
    expect(screen.getByText("API v1 · schema 1")).toBeInTheDocument();
  });

  it("shows a checking state while a request is pending", () => {
    const fetchMock = vi.fn<typeof fetch>(() => new Promise(() => undefined));
    vi.stubGlobal("fetch", fetchMock);

    render(<ApiStatus />);

    expect(screen.getByRole("status")).toHaveTextContent("Checking local API");
  });
});
