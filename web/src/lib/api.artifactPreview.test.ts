import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, clearArtifactPreviewCache } from "./api";

// `activeLanguage()` is fixed at module load in production -- switching the
// language reloads the page -- so the only way to exercise the language half of
// the cache key is to steer the reader directly.
const language = { value: "tr" };
vi.mock("./i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./i18n")>()),
  activeLanguage: () => language.value,
}));

/**
 * #368: switching project sections re-requested every artifact preview.
 *
 * `ProjectWorkspace` renders each section as `{view === "data" && <…/>}`, so a
 * tab switch unmounts the subtree and takes `ArtifactNodes`' `useRef` dedupe
 * with it. Coming back cost one `GET /api/artifacts/{id}/preview` per expanded
 * artifact and put every title back through "Loading…" on a run that had
 * finished hours ago. Nothing underneath caught it: `request()` is a bare
 * fetch, and the route set no `Cache-Control`.
 *
 * The dedupe belongs at module scope, which outlives the unmount. It is not a
 * guess about staleness: artifacts are immutable by contract, so the repeat
 * request was guaranteed to return what it returned before.
 */
describe("fetching an artifact preview", () => {
  const realFetch = globalThis.fetch;

  function response(body: unknown): Response {
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  beforeEach(() => {
    clearArtifactPreviewCache();
    language.value = "tr";
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    vi.restoreAllMocks();
    clearArtifactPreviewCache();
  });

  it("does not reach fetch a second time for the same artifact", async () => {
    const fetchMock = vi.fn(async () => response({ artifact_type: "data_card" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const first = await api.artifactPreview("art-1");
    const second = await api.artifactPreview("art-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
  });

  it("makes one request when several callers ask at once", async () => {
    // The real caller is a list of nodes expanding together, so the concurrent
    // case is the common one, not the edge case.
    const fetchMock = vi.fn(async () => response({ artifact_type: "data_card" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await Promise.all([api.artifactPreview("art-1"), api.artifactPreview("art-1")]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("answers synchronously once an artifact has been fetched", async () => {
    // What removes the "Loading…" pass on return: a remounting node reads the
    // title on its first frame instead of waiting for a promise to settle.
    globalThis.fetch = vi.fn(async () =>
      response({ artifact_type: "data_card" })) as unknown as typeof fetch;

    expect(api.cachedArtifactPreview("art-1")).toBeUndefined();
    await api.artifactPreview("art-1");

    expect(api.cachedArtifactPreview("art-1")?.artifact_type).toBe("data_card");
  });

  it("refetches after a language switch, because the prose is composed per language", async () => {
    const fetchMock = vi.fn(async (url: string) => (void url, response({ artifact_type: "data_card" })));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await api.artifactPreview("art-1");
    language.value = "en";
    await api.artifactPreview("art-1");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      "/api/artifacts/art-1/preview?lang=tr",
      "/api/artifacts/art-1/preview?lang=en",
    ]);
  });

  it("lets a failed preview be tried again", async () => {
    // A failure is not a fact about the artifact. Caching the rejection would
    // leave the row reading "Loading…" for the rest of the session.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: "Server Error",
        text: async () => "{}",
      })
      .mockResolvedValue(response({ artifact_type: "data_card" }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await api.artifactPreview("art-1").catch(() => undefined);
    const retried = await api.artifactPreview("art-1");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(retried.artifact_type).toBe("data_card");
  });
});
