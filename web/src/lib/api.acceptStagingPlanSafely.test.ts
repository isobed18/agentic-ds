import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";
import AUTOMATION_SOURCE from "../pages/AutomationWorkspace.tsx?raw";

/**
 * #167: accepting the plan failed with "the staging workspace changed; review
 * the latest proposal" and did nothing else -- the panel did not refresh to
 * whatever it said should be reviewed, and the plan stayed unaccepted.
 *
 * The base artifact id is a moving value. The screen polls the workspace every
 * 2200ms and rewrites it on each tick, so any snapshot the runner writes
 * between the last tick and the POST invalidates the id that was just sent.
 * Two seconds later the client was holding the newer snapshot anyway, with no
 * path back to the accept the person had already asked for.
 */
describe("acceptStagingPlanSafely (#167)", () => {
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

  it("re-reads the workspace and accepts the snapshot that is current", async () => {
    const accepts: string[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "POST") {
        const base = JSON.parse(String(init?.body)).base_artifact_id as string;
        accepts.push(base);
        if (base === "staging-1") {
          return jsonResponse(409, { detail: "the staging workspace changed; review the latest proposal" });
        }
        return jsonResponse(200, { artifact_id: base, execution_plan_artifact_id: "plan-9" });
      }
      // GET /api/runs/r1/staging — the snapshot the runner wrote meanwhile.
      return jsonResponse(200, { artifact_id: "staging-2" });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const accepted = await api.acceptStagingPlanSafely("r1", "staging-1");

    expect(accepts).toEqual(["staging-1", "staging-2"]);
    expect(accepted.execution_plan_artifact_id).toBe("plan-9");
  });

  it("gives up rather than retrying forever against a workspace that keeps moving", async () => {
    let reads = 0;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "POST") {
        return jsonResponse(409, { detail: "the staging workspace changed; review the latest proposal" });
      }
      reads += 1;
      return jsonResponse(200, { artifact_id: `staging-${reads}` });
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(api.acceptStagingPlanSafely("r1", "staging-0")).rejects.toBeInstanceOf(ApiError);
    // Four attempts, then the conflict is the person's to see.
    expect(reads).toBe(3);
  });

  it("does not retry a request that would fail the same way again", async () => {
    // The route had to stop reporting a stale base and a malformed body alike
    // as 400 first, or this retry would loop on the client's own mistake.
    const fetchMock = vi.fn(async () => jsonResponse(400, { detail: "the Planner has not proposed a workflow" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(api.acceptStagingPlanSafely("r1", "staging-1")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("is what the accept button actually calls", () => {
    expect(AUTOMATION_SOURCE).toContain("api.acceptStagingPlanSafely(runId, workspace.artifact_id)");
    expect(AUTOMATION_SOURCE).not.toContain("api.acceptStagingPlan(runId, workspace.artifact_id)");
  });
});
