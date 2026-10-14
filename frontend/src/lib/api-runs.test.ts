import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, cleanupRuns, cloneBenchmarkPlan, deleteRun } from "./api";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: unknown; token: string | null };

function stubApi(respond: (call: Call) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/session") return json({ token: "session-token" });
      const call = {
        url,
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
        token: new Headers(init?.headers).get("X-LOCALFORGE-SESSION"),
      };
      calls.push(call);
      return respond(call);
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("run retention and plan clone requests", () => {
  it("deletes a run with the session token and accepts 204", async () => {
    const calls = stubApi(() => new Response(null, { status: 204 }));
    await expect(deleteRun("run-1")).resolves.toBeUndefined();
    expect(calls).toEqual([
      { url: "/api/v1/runs/run-1", method: "DELETE", body: undefined, token: "session-token" },
    ]);
  });

  it("surfaces the run_active conflict code", async () => {
    stubApi(() => json({ detail: { code: "run_active", message: "Cancel the run first." } }, 409));
    const error = await deleteRun("run-1").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "run_active" });
  });

  it("posts cleanup filters, including the dry-run flag", async () => {
    const calls = stubApi(() => json({ run_ids: ["a"], count: 1, dry_run: true }));
    const result = await cleanupRuns({
      before: "2026-09-14T00:00:00.000Z",
      statuses: null,
      keep_latest: 5,
      dry_run: true,
    });
    expect(result.count).toBe(1);
    expect(calls[0]).toMatchObject({
      url: "/api/v1/runs/cleanup",
      method: "POST",
      body: {
        before: "2026-09-14T00:00:00.000Z",
        statuses: null,
        keep_latest: 5,
        dry_run: true,
      },
    });
  });

  it("clones a plan with one change", async () => {
    const calls = stubApi(() =>
      json({ plan: {}, preflight: {}, changed_fields: ["concurrency"] }, 201),
    );
    const result = await cloneBenchmarkPlan("plan-1", { name: null, changes: { concurrency: 4 } });
    expect(result.changed_fields).toEqual(["concurrency"]);
    expect(calls[0]).toMatchObject({
      url: "/api/v1/benchmark-plans/plan-1/clone",
      method: "POST",
      body: { name: null, changes: { concurrency: 4 } },
      token: "session-token",
    });
  });
});
