import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createEngineProfile,
  enableEngineProfile,
  probeEngineProfile,
  type EngineProfile,
  type ProbeResponse,
} from "../lib/api";
import {
  recommendationFixture,
  recommendationsFixture,
} from "../lib/engine-recommendations-fixture";
import { EngineRecommendations } from "./EngineRecommendations";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    createEngineProfile: vi.fn(),
    enableEngineProfile: vi.fn(),
    probeEngineProfile: vi.fn(),
  };
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

function serve(body: unknown = recommendationsFixture(), status = 200) {
  fetchMock = vi.fn(async () => json(body, status));
  vi.stubGlobal("fetch", fetchMock);
}

const storage = new Map<string, string>();

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => void storage.set(key, value),
    removeItem: (key: string) => void storage.delete(key),
    clear: () => storage.clear(),
  });
  serve();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function rankedList() {
  return screen.getByRole("list", { name: "Engines ranked for this machine" });
}

function row(name: string) {
  const item = screen
    .getAllByRole("listitem")
    .find((li) => within(li).queryByRole("heading", { name, level: 3 }));
  if (!item) throw new Error(`No recommendation row for ${name}`);
  return item;
}

describe("engine recommendations", () => {
  it("shows the machine, model-size guidance and the method disclaimer", async () => {
    render(<EngineRecommendations />);

    expect(await screen.findByText(/Windows 11 · Test CPU/)).toBeInTheDocument();
    expect(screen.getByText(/Models up to about 7B parameters/)).toBeInTheDocument();
    expect(screen.getByText(/this is not a speed measurement/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/engines/recommendations", expect.anything());
    expect(screen.getByRole("link", { name: "Which model should I run?" })).toHaveAttribute(
      "href",
      "#models",
    );
  });

  it("ranks the top three and expands to all suitable engines", async () => {
    render(<EngineRecommendations />);

    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    const names = within(rankedList())
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(names).toEqual(["Ollama", "llama.cpp", "LocalAI"]);
    expect(within(row("Ollama")).getByText("Best fit")).toBeInTheDocument();
    expect(within(row("llama.cpp")).getByText("Good fit")).toBeInTheDocument();
    expect(within(row("LocalAI")).getByText("Possible")).toBeInTheDocument();
    expect(within(row("Ollama")).getByLabelText("Rank 1")).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: "Show all 4 engines" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(within(rankedList()).getAllByRole("heading", { level: 3 })).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Show top 3 only" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("groups unsuitable engines at the end with their reason and no install push", async () => {
    render(<EngineRecommendations />);

    const summary = await screen.findByText("Not for this machine (1)");
    const group = summary.closest("details")!;
    expect(group).not.toHaveAttribute("open");
    expect(within(group).getByText("vLLM")).toBeInTheDocument();
    expect(
      within(group).getByText("Needs Linux with an NVIDIA GPU; this machine has neither."),
    ).toBeInTheDocument();
    expect(within(group).queryByRole("link")).not.toBeInTheDocument();
    expect(within(group).queryByRole("button")).not.toBeInTheDocument();
    expect(within(rankedList()).queryByText("vLLM")).not.toBeInTheDocument();
  });

  it("offers the one action that fits each status", async () => {
    render(<EngineRecommendations canAddManaged={() => true} onAddManaged={vi.fn()} />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    fireEvent.click(screen.getByRole("button", { name: "Show all 4 engines" }));

    // Running, no profile yet: one-click Add.
    expect(
      within(row("Ollama")).getByRole("button", { name: "Add Ollama on this machine" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Running")).toHaveLength(2);

    // Running with a profile: Benchmark it.
    expect(
      within(row("KoboldCpp")).getByRole("link", { name: "Benchmark KoboldCpp" }),
    ).toHaveAttribute("href", "#workloads?engine=kobold-profile");

    // Installed but not running: how to start it, plus managed mode for llama.cpp.
    const llama = row("llama.cpp");
    expect(within(llama).getByText("Installed")).toBeInTheDocument();
    fireEvent.click(within(llama).getByText("How to start it"));
    expect(
      within(llama).getByText("llama-server was found on PATH but is not running."),
    ).toBeInTheDocument();
    expect(within(llama).getByRole("button", { name: "Add as managed" })).toBeInTheDocument();

    // Not installed: external install guide and a copyable command.
    const localai = row("LocalAI");
    expect(within(localai).getByText("Not installed")).toBeInTheDocument();
    const guide = within(localai).getByRole("link", { name: /Install guide for LocalAI/ });
    expect(guide).toHaveAttribute("href", "https://localai.io/basics/getting_started/");
    expect(guide).toHaveAttribute("target", "_blank");
    expect(guide).toHaveAttribute("rel", "noopener noreferrer");
    expect(
      within(localai).getByText("docker run -p 8080:8080 localai/localai"),
    ).toBeInTheDocument();
    expect(within(localai).queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();
  });

  it("only offers Add as managed where the screen supports it", async () => {
    render(<EngineRecommendations />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    const llama = row("llama.cpp");
    fireEvent.click(within(llama).getByText("How to start it"));
    expect(within(llama).queryByRole("button", { name: "Add as managed" })).not.toBeInTheDocument();
    expect(within(llama).getByRole("link", { name: "llama.cpp setup guide" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
  });

  it("adds a running engine in one click: create, then probe, then enable", async () => {
    const created = {
      id: "ollama-1",
      label: "Ollama on this machine",
      is_remote: false,
    } as EngineProfile;
    vi.mocked(createEngineProfile).mockResolvedValue(created);
    vi.mocked(probeEngineProfile).mockResolvedValue({
      profile: created,
      result: { status: "succeeded", diagnostics: [] },
    } as unknown as ProbeResponse);
    vi.mocked(enableEngineProfile).mockResolvedValue(created);
    const onAdded = vi.fn();

    render(<EngineRecommendations onAdded={onAdded} />);
    fireEvent.click(await screen.findByRole("button", { name: "Add Ollama on this machine" }));

    expect(
      await within(row("Ollama")).findByRole("link", { name: "Benchmark Ollama" }),
    ).toHaveAttribute("href", "#workloads?engine=ollama-1");
    expect(createEngineProfile).toHaveBeenCalledWith({
      label: "Ollama on this machine",
      adapter_id: "localforge.ollama",
      engine_display_name: null,
      integration_mode: "attached_endpoint",
      endpoint_url: "http://127.0.0.1:11434",
      user_notes: null,
    });
    expect(probeEngineProfile).toHaveBeenCalledWith("ollama-1", false);
    expect(enableEngineProfile).toHaveBeenCalledWith("ollama-1");
    const order = [createEngineProfile, probeEngineProfile, enableEngineProfile].map(
      (mock) => vi.mocked(mock).mock.invocationCallOrder[0]!,
    );
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(onAdded).toHaveBeenCalledWith(expect.objectContaining({ ready: true }));
    expect(screen.getByText(/Ollama on this machine is ready/)).toBeInTheDocument();
  });

  it("does not enable an added engine whose probe fails", async () => {
    const created = { id: "ollama-1", label: "Ollama on this machine", is_remote: false };
    vi.mocked(createEngineProfile).mockResolvedValue(created as EngineProfile);
    vi.mocked(probeEngineProfile).mockResolvedValue({
      profile: created,
      result: {
        status: "failed",
        reachable: { state: "unsupported", details: null, source: null },
        diagnostics: [{ severity: "error", message: "Connection refused." }],
      },
    } as unknown as ProbeResponse);

    render(<EngineRecommendations />);
    fireEvent.click(await screen.findByRole("button", { name: "Add Ollama on this machine" }));

    expect(await screen.findByText(/Connection refused\. It stays disabled\./)).toBeInTheDocument();
    expect(enableEngineProfile).not.toHaveBeenCalled();
  });

  it("shows a measured badge only for engines measured on this machine", async () => {
    render(<EngineRecommendations />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });

    const badge = within(row("llama.cpp")).getByRole("link", {
      name: "Measured 24.7 tok/s · qwen2.5:3b",
    });
    expect(badge).toHaveAttribute("href", "#runs?run=run-1");
    expect(within(row("Ollama")).getByText("Not measured on this machine yet")).toBeInTheDocument();
    expect(within(row("Ollama")).queryByText(/tok\/s/)).not.toBeInTheDocument();
  });

  it("copies the install command and falls back when the clipboard is unavailable", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(<EngineRecommendations />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });

    fireEvent.click(screen.getByRole("button", { name: "Copy install command for LocalAI" }));
    expect(await screen.findByText("Command copied.")).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith("docker run -p 8080:8080 localai/localai");

    writeText.mockRejectedValueOnce(new Error("denied"));
    fireEvent.click(screen.getByRole("button", { name: "Copy install command for LocalAI" }));
    expect(
      await screen.findByText(/Copying is not available here\. The command is selected/),
    ).toBeInTheDocument();
  });

  it("refreshes on request and announces the result", async () => {
    render(<EngineRecommendations />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    serve(recommendationsFixture([recommendationFixture({ display_name: "Ollama (rescanned)" })]));

    fireEvent.click(screen.getByRole("button", { name: "Refresh engine recommendations" }));

    expect(
      await screen.findByRole("heading", { name: "Ollama (rescanned)", level: 3 }),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(screen.getByText("Engine recommendations updated.")).toBeInTheDocument(),
    );
  });

  it("reports loading, errors and an empty list", async () => {
    serve({ detail: "Recommendation service unavailable" }, 500);
    render(<EngineRecommendations />);
    expect(screen.getByText("Checking this machine and the engines on it…")).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Engine recommendations could not be loaded.",
    );

    serve(recommendationsFixture([]));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/No engines could be ranked/)).toBeInTheDocument();
  });

  it("collapses and remembers the collapsed state", async () => {
    const { unmount } = render(<EngineRecommendations collapseStorageKey="recs-collapsed" />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    fireEvent.click(screen.getByRole("button", { name: "Hide recommendations" }));
    expect(screen.queryByRole("list", { name: "Engines ranked for this machine" })).toBeNull();
    expect(storage.get("recs-collapsed")).toBe("true");
    unmount();

    render(<EngineRecommendations collapseStorageKey="recs-collapsed" />);
    const show = screen.getByRole("button", { name: "Show recommendations" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(show);
    expect(
      await screen.findByRole("list", { name: "Engines ranked for this machine" }),
    ).toBeInTheDocument();
  });

  it("limits the compact card to the top three and links to the Engines screen", async () => {
    render(<EngineRecommendations compact />);
    await screen.findByRole("list", { name: "Engines ranked for this machine" });
    expect(within(rankedList()).getAllByRole("heading", { level: 3 })).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Not for this machine \(/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See all 5 engines on the Engines screen" }),
    ).toHaveAttribute("href", "#engines");
  });
});
