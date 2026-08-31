import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

/**
 * #242: the planner failed with an HTTP error whose whole user-facing message
 * was a raw JSON blob -- and, for a malformed model reply, a JSON parser's own
 * offset text ("Expecting ',' delimiter: line 1 column 5151 (char 5150)"). The
 * client used to throw `${status} ${statusText} — ${body}`, so the person saw
 * the blob rather than the sentence the server wrote for them. The error's
 * message must be the `detail` string, not the JSON envelope around it.
 */
describe("api error message is the detail, not the JSON blob (#242)", () => {
  const realFetch = globalThis.fetch;
  // plannerChat passes a request timeout, so `request` reaches for
  // window.setTimeout; the suite runs in node, where production's browser
  // window is absent. globalThis already carries setTimeout/clearTimeout.
  beforeAll(() => { (globalThis as { window?: unknown }).window = globalThis; });
  afterAll(() => { delete (globalThis as { window?: unknown }).window; });
  afterEach(() => { globalThis.fetch = realFetch; vi.restoreAllMocks(); });

  function response(status: number, statusText: string, body: unknown): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  it("surfaces the readable detail of an upstream planner failure", async () => {
    const detail = "The planner could not produce a usable response: the model returned malformed output. Please try again.";
    globalThis.fetch = vi.fn(async () => response(502, "Bad Gateway", { detail })) as unknown as typeof fetch;

    const caught = await api.plannerChat({ message: "plan it" }).catch((e) => e);

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(502);
    // Exactly the sentence, with no status prefix and no JSON envelope.
    expect(caught.message).toBe(detail);
    expect(caught.message).not.toContain("{");
    expect(caught.message).not.toContain("502");
  });

  it("never lets a JSON parser offset become the message", async () => {
    // The exact body the model-decode failure used to leak verbatim as a 400.
    const offset = "Expecting ',' delimiter: line 1 column 5151 (char 5150)";
    globalThis.fetch = vi.fn(async () => response(400, "Bad Request", { detail: offset })) as unknown as typeof fetch;

    const caught = await api.plannerChat({ message: "plan it" }).catch((e) => e);

    // If the raw offset ever reaches a person, it is only because the server
    // put it in `detail`; the client no longer wraps it in a JSON blob.
    expect(caught.message).toBe(offset);
    expect(caught.message).not.toContain('{"detail"');
  });
});
