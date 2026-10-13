import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, compareRuns } from "./api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("local session token", () => {
  it("refreshes a token rejected after an API restart and retries once", async () => {
    const tokens = ["token-before-restart", "token-after-restart"];
    const sentTokens: Array<string | null> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/session") return json({ token: tokens.shift() });
      const token = new Headers(init?.headers).get("X-LOCALFORGE-SESSION");
      sentTokens.push(token);
      if (token === "token-before-restart")
        return json({ detail: { code: "local_session_required", message: "stale" } }, 403);
      return json({ run_ids: ["a", "b"], shared_metrics: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    await compareRuns({ run_ids: ["a", "b"] });

    expect(sentTokens).toEqual(["token-before-restart", "token-after-restart"]);
  });

  it("does not retry other permission errors", async () => {
    let posts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/v1/session") return json({ token: "token" });
        posts += 1;
        return json({ detail: { code: "origin_rejected", message: "Rejected origin." } }, 403);
      }),
    );

    await expect(compareRuns({ run_ids: ["a", "b"] })).rejects.toBeInstanceOf(ApiError);
    expect(posts).toBe(1);
  });
});
