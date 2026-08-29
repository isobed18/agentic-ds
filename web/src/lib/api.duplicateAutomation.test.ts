import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type AutomationDefinition } from "./api";

/**
 * #101: re-run an existing project's files as a new, independent project. The
 * only creation path was a blank "Untitled automation" with no source, so the
 * same files could only be tried again inside the same automation (mixing
 * histories) or by re-uploading by hand. duplicateAutomation creates a fresh
 * record and binds it to the original's source_id -- nothing else is copied.
 */
describe("duplicateAutomation (#101)", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => { globalThis.fetch = realFetch; vi.restoreAllMocks(); });

  function jsonResponse(status: number, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  const source = (over: Partial<AutomationDefinition>): AutomationDefinition =>
    ({ automation_id: "orig", name: "Sales", revision: 4, execution_ids: ["run-1", "run-2"], ...over }) as AutomationDefinition;

  it("creates a new automation bound to the original's source, with no history", async () => {
    const calls: Array<{ method: string; body: unknown }> = [];
    globalThis.fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ method, body });
      if (method === "POST") return jsonResponse(200, { automation_id: "copy", name: "Sales (copy)", revision: 1, source_id: null, execution_ids: [] });
      return jsonResponse(200, { automation_id: "copy", name: "Sales (copy)", revision: 2, source_id: "upload:s", execution_ids: [] });
    }) as unknown as typeof fetch;

    const copy = await api.duplicateAutomation(source({ source_id: "upload:s" }), "Sales (copy)");

    expect(copy.automation_id).toBe("copy");
    expect(copy.source_id).toBe("upload:s");
    // Fresh record: no execution history carried over from the original.
    expect(copy.execution_ids).toEqual([]);
    // A create followed by a source-binding update -- nothing else copied.
    expect(calls.map((c) => c.method)).toEqual(["POST", "PUT"]);
    expect(calls[0].body).toEqual({ name: "Sales (copy)" });
    expect(calls[1].body).toEqual({ expected_revision: 1, changes: { source_id: "upload:s" } });
  });

  it("does not bind a source when the original has no files", async () => {
    const methods: string[] = [];
    globalThis.fetch = vi.fn(async (_url: string, init?: RequestInit) => {
      methods.push(init?.method ?? "GET");
      return jsonResponse(200, { automation_id: "copy", name: "Blank (copy)", revision: 1, source_id: null, execution_ids: [] });
    }) as unknown as typeof fetch;

    await api.duplicateAutomation(source({ source_id: null }), "Blank (copy)");

    // Only the create; no PUT, because there is nothing to attach.
    expect(methods).toEqual(["POST"]);
  });
});
