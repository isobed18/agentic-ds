/**
 * One presentation for two status vocabularies (#195).
 *
 * The ML pipeline's nodes rendered `statusLabel` in a badge, so a finished
 * stage read "tamamlandı" beside the understanding canvas's "Tamamlandı", and
 * the two canvases folded their own vocabularies into colours separately. The
 * mapping asserted here is the thing that makes them identical by construction.
 */
import { describe, expect, it } from "vitest";

import { statusBadgeLabel, statusLabel, statusTone } from "./status";

describe("the badge vocabulary", () => {
  it("gives both canvases the same capitalised label for the same state", () => {
    // succeeded (ML pipeline) and complete (understanding) are one state.
    expect(statusBadgeLabel("succeeded")).toBe("Tamamlandı");
    expect(statusBadgeLabel("complete")).toBe("Tamamlandı");
    expect(statusBadgeLabel("completed")).toBe("Tamamlandı");
    // pending (both) and running (both).
    expect(statusBadgeLabel("pending")).toBe("Bekliyor");
    expect(statusBadgeLabel("running")).toBe("İşleniyor");
    expect(statusBadgeLabel("failed")).toBe("Başarısız");
  });

  it("keeps the states a badge would otherwise flatten distinguishable", () => {
    // blocked shares the attention tone with failed but is not a failure.
    expect(statusBadgeLabel("blocked")).toBe("Onay bekliyor");
    expect(statusBadgeLabel("retry")).toBe("Yeniden deniyor");
    expect(statusBadgeLabel("staging")).toBe("Veri okunuyor");
    expect(statusBadgeLabel("awaiting_human")).toBe("Sizi bekliyor");
  });

  it("stays capitalised where statusLabel is deliberately lowercase", () => {
    // statusLabel keeps its mid-sentence form -- StageRow renders it as
    // "status · elapsed" -- and must not be dragged into the badge vocabulary.
    expect(statusLabel("succeeded")).toBe("tamamlandı");
    expect(statusLabel("pending")).toBe("bekliyor");
  });

  it("falls back to the state's own name rather than a blank badge", () => {
    expect(statusBadgeLabel("some_new_state")).toBe("Some new state");
    expect(statusBadgeLabel(null)).toBe("");
  });
});

describe("the status tone", () => {
  it("maps both vocabularies onto the same four presentations", () => {
    expect(statusTone("succeeded")).toBe("complete");
    expect(statusTone("complete")).toBe("complete");
    expect(statusTone("running")).toBe("running");
    expect(statusTone("retry")).toBe("running");
    expect(statusTone("failed")).toBe("attention");
    expect(statusTone("blocked")).toBe("attention");
    expect(statusTone("pending")).toBe("pending");
  });

  it("treats an unknown state as waiting rather than as a failure", () => {
    expect(statusTone(undefined)).toBe("pending");
    expect(statusTone("something else")).toBe("pending");
  });
});
