import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "./api";

/**
 * A minimal XMLHttpRequest stand-in driven by the test. fetch exposes neither
 * upload progress nor abort, so api.upload was rewritten on XHR (#84); these
 * tests exercise that mechanism directly.
 */
class FakeXHR {
  static instances: FakeXHR[] = [];
  upload: { onprogress?: (event: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;
  status = 0;
  responseText = "";
  method = "";
  url = "";
  aborted = false;
  sent = false;

  constructor() { FakeXHR.instances.push(this); }
  open(method: string, url: string) { this.method = method; this.url = url; }
  send() { this.sent = true; }
  abort() { this.aborted = true; this.onabort?.(); }
}

describe("upload progress and cancellation (#84)", () => {
  const realXHR = globalThis.XMLHttpRequest;
  beforeEach(() => {
    FakeXHR.instances = [];
    (globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = FakeXHR;
  });
  afterEach(() => {
    (globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = realXHR;
  });

  const file = { name: "big.csv" } as unknown as File;

  it("reports byte progress as a 0..1 fraction and resolves with the source", async () => {
    const seen: number[] = [];
    const pending = api.upload(file, undefined, { onProgress: (fraction) => seen.push(fraction) });
    const xhr = FakeXHR.instances[0];

    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 8 });
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 8, total: 8 });
    xhr.status = 200;
    xhr.responseText = JSON.stringify({ source_id: "upload:abc", label: "big.csv", files: ["big.csv"] });
    xhr.onload?.();

    await expect(pending).resolves.toMatchObject({ source_id: "upload:abc" });
    expect(seen).toEqual([0.25, 1]);
    expect(xhr.url).toBe("/api/uploads/big.csv");
  });

  it("aborts the in-flight request when its signal fires", async () => {
    const controller = new AbortController();
    const pending = api.upload(file, undefined, { signal: controller.signal });
    const xhr = FakeXHR.instances[0];
    expect(xhr.sent).toBe(true);

    controller.abort();

    expect(xhr.aborted).toBe(true);
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects a non-2xx response with the server detail", async () => {
    const pending = api.upload(file);
    const xhr = FakeXHR.instances[0];
    xhr.status = 400;
    xhr.responseText = JSON.stringify({ detail: "unsupported file type" });
    xhr.onload?.();

    await expect(pending).rejects.toThrow("unsupported file type");
  });
});
