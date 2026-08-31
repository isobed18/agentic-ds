import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

/**
 * #207: making a project public gives it several writers, and the workspace
 * header holds the `revision` it loaded for as long as the tab stays open. A
 * rename after somebody else's edit therefore carries a stale
 * `expected_revision`, and the 409 reached the user as a raw error string with
 * the rename silently dropped. While a project had one writer this path was
 * nearly unreachable; publishing is what makes it a normal Tuesday.
 */
describe("updateProjectSafely (#207)", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => { globalThis.fetch = realFetch; vi.restoreAllMocks(); });

  function jsonResponse(status: number, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 409 ? "Conflict" : "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  it("retries the PUT against the refetched revision after a 409 conflict", async () => {
    const puts: number[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "PUT") {
        const revision = JSON.parse(String(init?.body)).expected_revision as number;
        puts.push(revision);
        // A collaborator's edit already bumped revision 1 -> 2 while the tab
        // sat open, so the header's first attempt is stale.
        if (revision === 1) return jsonResponse(409, { detail: "project revision changed from 1 to 2" });
        return jsonResponse(200, { project_id: "project-abc123def456", revision: revision + 1, name: "Churn" });
      }
      // GET /api/projects/… — the current revision after their edit landed.
      return jsonResponse(200, { project_id: "project-abc123def456", revision: 2 });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const saved = await api.updateProjectSafely("project-abc123def456", 1, { name: "Churn" });

    expect(puts).toEqual([1, 2]);
    expect(saved.name).toBe("Churn");
  });

  it("surfaces a non-conflict error without retrying", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(403, { detail: "only ishak-ads can change this" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(api.updateProjectSafely("project-abc123def456", 1, { name: "x" })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
