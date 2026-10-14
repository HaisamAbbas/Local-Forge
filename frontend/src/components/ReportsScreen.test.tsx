import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SavedReport } from "../lib/api";
import { ReportsScreen } from "./ReportsScreen";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const reports: SavedReport[] = [
  {
    id: "report-2",
    created_at: "2026-02-02T10:00:00Z",
    title: "Concurrency sweep",
    format: "markdown",
    run_ids: ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"],
    size_bytes: 2048,
    available: true,
  },
  {
    id: "report-1",
    created_at: "2026-02-01T10:00:00Z",
    title: "Baseline export",
    format: "csv",
    run_ids: ["33333333-3333-4333-8333-333333333333"],
    size_bytes: null,
    available: false,
  },
];

function mockApi(listed: SavedReport[] = reports) {
  const calls: Array<{ url: string; method: string; token: string | null }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({
      url,
      method,
      token: new Headers(init?.headers).get("X-LOCALFORGE-SESSION"),
    });
    if (url === "/api/v1/session") return json({ token: "session-token" });
    if (url === "/api/v1/reports" && method === "GET") return json(listed);
    if (url.startsWith("/api/v1/reports/") && method === "DELETE")
      return new Response(null, { status: 204 });
    return json({ detail: { message: "Unexpected request" } }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReportsScreen", () => {
  it("lists saved reports with run links, sizes and downloads", async () => {
    mockApi();
    render(<ReportsScreen />);

    const sweepRow = (await screen.findByRole("row", { name: /Concurrency sweep/ })) as HTMLElement;
    expect(within(sweepRow).getByText("Markdown")).toBeInTheDocument();
    expect(within(sweepRow).getByText("2 KB")).toBeInTheDocument();
    expect(
      within(sweepRow).getByRole("link", {
        name: "Open run 11111111-1111-4111-8111-111111111111",
      }),
    ).toHaveAttribute("href", "#runs?run=11111111-1111-4111-8111-111111111111");
    expect(
      within(sweepRow).getByRole("link", { name: "Download Concurrency sweep" }),
    ).toHaveAttribute("href", "/api/v1/reports/report-2/download");

    const baselineRow = screen.getByRole("row", { name: /Baseline export/ });
    expect(within(baselineRow).getByText("Unknown")).toBeInTheDocument();
    expect(within(baselineRow).getByText("File missing")).toBeInTheDocument();
    expect(within(baselineRow).queryByRole("link", { name: /Download/ })).not.toBeInTheDocument();
  });

  it("filters by format", async () => {
    mockApi();
    render(<ReportsScreen />);
    await screen.findByRole("row", { name: /Concurrency sweep/ });

    fireEvent.change(screen.getByRole("combobox", { name: "Format" }), {
      target: { value: "csv" },
    });
    expect(screen.queryByRole("row", { name: /Concurrency sweep/ })).not.toBeInTheDocument();
    expect(screen.getByRole("row", { name: /Baseline export/ })).toBeInTheDocument();
  });

  it("deletes only after an inline confirmation, using the session token", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const calls = mockApi();
    render(<ReportsScreen />);
    await screen.findByRole("row", { name: /Concurrency sweep/ });

    fireEvent.click(screen.getByRole("button", { name: "Delete Concurrency sweep" }));
    const confirm = screen.getByRole("group", { name: "Confirm deleting Concurrency sweep" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Delete Concurrency sweep" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /Concurrency sweep/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Deleted “Concurrency sweep”.");
    const deletion = calls.find((call) => call.method === "DELETE");
    expect(deletion).toEqual({
      url: "/api/v1/reports/report-2",
      method: "DELETE",
      token: "session-token",
    });
    expect(confirmSpy).not.toHaveBeenCalled();
  });

  it("points to Experiments when there are no reports", async () => {
    mockApi([]);
    render(<ReportsScreen />);

    expect(
      await screen.findByRole("heading", { name: "No saved reports yet" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Experiments" })).toHaveAttribute(
      "href",
      "#experiments",
    );
  });
});
