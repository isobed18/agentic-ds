/**
 * The rename field in the two workspace headers (#183).
 *
 * The old `w-[290px] max-w-[28vw]` read as responsive and was not: 28vw only
 * falls below 290px under a ~1036px viewport, so at every ordinary window size
 * the field sat at a flat 290px. Resizing the window did nothing, and a long
 * name simply overflowed.
 *
 * What is pinned here is the shape of the fix rather than a rendered pixel
 * width, which no static render can measure: the field is driven by the
 * viewport, it is smaller than it was, both headers read the same value, and a
 * name too long for it is cut with the whole of it still reachable.
 */
import { describe, expect, it } from "vitest";

import AUTOMATION_SOURCE from "./AutomationWorkspace.tsx?raw";
import PROJECT_SOURCE from "./ProjectWorkspace.tsx?raw";
import UI_SOURCE from "../components/ui.tsx?raw";
import { NAME_FIELD_WIDTH } from "../components/ui";

/** The three clamp terms, as numbers, from the shared constant. */
function clampTerms(): { floor: number; viewport: number; ceiling: number } {
  const match = /w-\[clamp\((\d+)px,(\d+)vw,(\d+)px\)\]/.exec(NAME_FIELD_WIDTH);
  expect(match, `no clamped width in ${NAME_FIELD_WIDTH}`).not.toBeNull();
  return { floor: Number(match![1]), viewport: Number(match![2]), ceiling: Number(match![3]) };
}

describe("the workspace rename field (#183)", () => {
  it("is driven by the viewport rather than a fixed width", () => {
    const { floor, viewport, ceiling } = clampTerms();
    expect(viewport).toBeGreaterThan(0);
    expect(floor).toBeLessThan(ceiling);
  });

  it("is smaller than the fixed width it replaced", () => {
    expect(clampTerms().ceiling).toBeLessThan(290);
  });

  it("stays clear of the section tabs centred in the same header", () => {
    // The tabs are absolutely centred, so they are outside the flex flow and
    // cannot push the field back -- it has to yield on its own. Left of the
    // tabs there is the header padding, the back button and one gap; the tab
    // strip itself is a little over 400px wide, so its left edge sits at
    // roughly 50% - 200px. Check the field still fits at a narrow-desktop
    // window, which is where the old 28vw cap first started to overlap.
    const { floor, viewport, ceiling } = clampTerms();
    const width = (px: number) => Math.min(Math.max(floor, (px * viewport) / 100), ceiling);
    for (const window of [1920, 1440, 1280, 1024, 900]) {
      const rightEdge = 16 + 90 + 12 + width(window);
      const tabsBegin = window / 2 - 202;
      expect(rightEdge, `field overlaps the tabs at ${window}px`).toBeLessThan(tabsBegin);
    }
  });

  it("is the same value in both headers, and neither keeps its own", () => {
    for (const source of [PROJECT_SOURCE, AUTOMATION_SOURCE]) {
      expect(source).toContain("NAME_FIELD_WIDTH");
      expect(source).not.toMatch(/w-\[\d+px\] max-w-\[\d+vw\]/);
    }
    expect(UI_SOURCE).toContain("export const NAME_FIELD_WIDTH");
  });

  it("cuts a name too long for it and keeps the whole of it on hover", () => {
    // `truncate` earns the ellipsis; `title` is the only way back to the rest,
    // since the field shows one line and never wraps.
    for (const source of [PROJECT_SOURCE, AUTOMATION_SOURCE]) {
      const field = /<input value=\{name\}[\s\S]*?\/>/.exec(source)?.[0] ?? "";
      expect(field, "the rename input was not found").not.toBe("");
      expect(field).toContain("truncate");
      expect(field).toContain("title={name}");
    }
  });
});
