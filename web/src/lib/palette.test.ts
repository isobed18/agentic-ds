/**
 * A colour utility naming a stop the theme never defines (#196).
 *
 * Tailwind emits no rule for it and reports nothing: the element simply keeps
 * whatever it already had. `bg-ok-300` on the completed-stage arrow drew no
 * line at all, so the arrow was a bare arrowhead, and twelve further shades
 * were dead the same way -- the "running" ring on the pipeline nodes among
 * them. Each one reads as a deliberate design decision in the source and as
 * nothing in the product.
 *
 * The theme is read here rather than the built stylesheet, which only contains
 * the classes this build happened to reach: a dead shade behind a rare state
 * would still pass against the bundle.
 */
import { describe, expect, it } from "vitest";

import CONFIG_SOURCE from "../../tailwind.config.js?raw";

/** The theme's own scales, parsed from the config the build actually loads. */
function palette(): Map<string, Set<string>> {
  const scales = new Map<string, Set<string>>();
  const colors = CONFIG_SOURCE.slice(
    CONFIG_SOURCE.indexOf("colors: {"),
    CONFIG_SOURCE.indexOf("fontFamily:"),
  );
  for (const [, name, body] of colors.matchAll(/^\s*(\w+): \{(.*)\},$/gm)) {
    scales.set(
      name,
      new Set([...body.matchAll(/(?:^|,)\s*"?([\w]+)"?\s*:/g)].map((stop) => stop[1])),
    );
  }
  return scales;
}

const SCALES = palette();
const NAMES = [...SCALES.keys()];

/** Only the theme's own names -- a Tailwind built-in like `slate-300` is real. */
const UTILITY = new RegExp(
  String.raw`(?<![\w-])(?:bg|text|border|ring|outline|divide|fill|stroke|accent|caret|decoration|placeholder|from|via|to)-(${NAMES.join("|")})(?:-(\w+))?(?![\w-])`,
  "g",
);

describe("theme palette (#196)", () => {
  it("was read from the config at all", () => {
    expect(NAMES).toContain("brand");
    expect(SCALES.get("ink")).toContain("faint");
  });

  it("defines every shade the interface asks for", () => {
    const modules = import.meta.glob("../**/*.{ts,tsx,css}", {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;

    const dead = new Set<string>();
    for (const [path, source] of Object.entries(modules)) {
      if (path.includes(".test.")) continue;
      for (const [utility, name, stop] of source.matchAll(UTILITY)) {
        if (!SCALES.get(name)!.has(stop ?? "DEFAULT")) dead.add(`${path}: ${utility}`);
      }
    }

    expect([...dead].sort()).toEqual([]);
  });

  it("keeps each scale continuous, so a neighbouring shade is always there", () => {
    const stops = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900"];
    for (const name of ["brand", "ok", "warn", "stop"]) {
      expect([...SCALES.get(name)!]).toEqual(stops);
    }
  });
});
