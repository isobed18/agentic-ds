/**
 * The whole UI, 10% larger (#380).
 *
 * The app rendered at the browser's default 16px root while 262 of its
 * utilities pinned type at 9, 10 or 11px -- labels, badges, table headers, node
 * captions, rationale text, diagnostics. Reviewing an automation meant bumping
 * Chrome to 110% by hand.
 *
 * Moving the root alone would have been worse than doing nothing: the layout
 * would grow around type that stayed at 9-11px. So the guard here is not "the
 * root moved" but "nothing is left behind by it" -- no px-pinned type, no
 * px-pinned box around type, and the SVG text, which follows the viewBox rather
 * than the root, bumped at its source.
 */
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import TAILWIND from "../tailwind.config.js?raw";
import CHARTS from "./components/Charts.tsx?raw";
import SCHEMA from "./components/SchemaDiagram.tsx?raw";
import BUILDER from "./components/PipelineBuilder.tsx?raw";
import ZOOM from "./components/canvasZoom.ts?raw";

// Vite hands a `?raw` stylesheet back empty, so this one is read from disk.
const CSS = readFileSync(new URL("./index.css", import.meta.url), "utf-8");

/** Every shipped source file, so the sweep can be checked rather than trusted. */
const SOURCES = import.meta.glob("./**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

function shipped(): [string, string][] {
  return Object.entries(SOURCES).filter(([path]) => !path.includes(".test."));
}

describe("the 110% root (#380)", () => {
  it("moves the root, which is what browser zoom does", () => {
    expect(CSS).toContain("html { font-size: 110%; }");
  });

  it("names the three small steps so they follow the root", () => {
    // 11px, 10px and 9px over the old 16px root -- so at 110% each lands 10%
    // larger rather than staying exactly where it was.
    expect(TAILWIND).toContain('"2xs": "0.6875rem"');
    expect(TAILWIND).toContain('"3xs": "0.625rem"');
    expect(TAILWIND).toContain('"4xs": "0.5625rem"');
  });
});

describe("nothing is left behind by the root (#380)", () => {
  it("pins no type in px anywhere", () => {
    const offenders = shipped()
      .filter(([, text]) => /\btext-\[[0-9.]+px\]/.test(text))
      .map(([path]) => path);
    expect(offenders, "these keep their old size while everything grows").toEqual([]);
  });

  it("pins no box around that type in px either", () => {
    // A container whose height is px holds rows that are about to be 10%
    // taller. Hairlines are exempt: a 1px rule is not type-relative, and
    // neither is the 3px arrowhead nudge.
    const lengths = /\b(?:w|h|min-w|max-w|min-h|max-h|top|bottom|left|right|pt|pb|pl|pr|gap|basis|size)-\[([0-9.]+)px\]/g;
    const offenders: string[] = [];
    for (const [path, text] of shipped()) {
      for (const match of text.matchAll(lengths)) {
        if (Number(match[1]) > 4) offenders.push(`${path} ${match[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("grows the graph boxes handed to the elk layout", () => {
    // These are px constants, not utilities, so nothing about the root reaches
    // them -- and their captions are the most likely visible regression.
    expect(BUILDER).toContain("const NODE_WIDTH = 246");
    expect(BUILDER).toContain("const NODE_HEIGHT = 90");
    expect(BUILDER).toContain("const BRANCH_WIDTH = 275");
    expect(BUILDER).toContain("const BRANCH_HEIGHT = 101");
    expect(SCHEMA).toContain("const NODE_WIDTH = 275");
    expect(SCHEMA).toContain("const NODE_HEIGHT = 130");
    expect(ZOOM).toContain("export const CANVAS_BASE_WIDTH = 1650");
    expect(ZOOM).toContain("export const CANVAS_BASE_HEIGHT = 946");
  });

  it("bumps the SVG text, which scales with the viewBox and not the root", () => {
    // Left alone these end up visibly smaller than everything around them.
    const sizes = [...CHARTS.matchAll(/fontSize="(\d+)"/g)].map((match) => Number(match[1]));
    expect(sizes.length).toBeGreaterThan(0);
    // 9 and 10 became 10 and 11, 13 became 14: nothing is under 10 any more,
    // and the one 10 left is the bumped 9.
    expect(sizes.filter((size) => size < 10)).toEqual([]);
    expect(CHARTS).not.toContain('fontSize="13"');
    expect(CHARTS).toContain('fontSize="11"');
    expect(CHARTS).toContain('fontSize="14"');
    expect(CHARTS).toContain("Math.min(cell * 0.7, 11)");
    expect(SCHEMA).toContain("fontSize: 11");
  });
});
