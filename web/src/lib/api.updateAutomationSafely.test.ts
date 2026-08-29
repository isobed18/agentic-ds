import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

/**
 * #87: a rename and a large batch upload race two independent PUTs against the
 * same automation revision. Whichever lands first bumps the revision, so the
 * slower flow's PUT carries a stale `expected_revision` and the server answers
 * 409 — dropping the change (for the upload, orphaning the just-uploaded files).
 * updateAutomationSafely refetches the current revision and retries on conflict.
 */
describe("updateAutomationSafely (#87)", () => {
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
        // The rename already bumped revision 1 -> 2; the first attempt is stale.
        if (revision === 1) return jsonResponse(409, { detail: "automation revision changed from 1 to 2" });
        return jsonResponse(200, { automation_id: "a1", revision: revision + 1, source_id: "upload:xyz" });
      }
      // GET /api/automations/a1 — the current revision after the rename landed.
      return jsonResponse(200, { automation_id: "a1", revision: 2 });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const saved = await api.updateAutomationSafely("a1", 1, { source_id: "upload:xyz" });

    // Stale attempt at 1, refetch, retry at 2 — the upload's source is attached.
    expect(puts).toEqual([1, 2]);
    expect(saved.source_id).toBe("upload:xyz");
  });

  it("surfaces a non-conflict error without retrying", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(500, { detail: "boom" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(api.updateAutomationSafely("a1", 1, { name: "x" })).rejects.toBeInstanceOf(ApiError);
    // One PUT, no refetch/retry loop for a 500.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
